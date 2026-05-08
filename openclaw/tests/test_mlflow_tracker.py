"""Unit tests for mlflow_tracker (Phase 9).

The mlflow Python package is not required to run these — we patch
sys.modules['mlflow'] with a fake module so the lazy-import path works.
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest


def _fake_mlflow():
    """Build a minimal stand-in for the mlflow module."""
    fake = types.ModuleType("mlflow")
    fake.set_tracking_uri = MagicMock()
    fake.set_experiment = MagicMock()
    fake.log_param = MagicMock()
    fake.log_metric = MagicMock()
    fake.set_tag = MagicMock()
    fake.log_text = MagicMock()
    fake.end_run = MagicMock()

    run_info = types.SimpleNamespace(run_id="fake-run-id-123")
    active_run = types.SimpleNamespace(info=run_info)
    fake.start_run = MagicMock(return_value=active_run)
    return fake


@pytest.fixture
def mlflow_module(monkeypatch):
    fake = _fake_mlflow()
    monkeypatch.setitem(sys.modules, "mlflow", fake)
    return fake


def _scorecard():
    from openclaw.fairness_scorer import FairnessScorecard
    return FairnessScorecard(
        scorecard_id="sc-1",
        brief_id="b-1",
        computed_at=datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc),
        source_tier_t1_pct=80.0,
        source_tier_t2_pct=10.0,
        source_tier_t3_pct=5.0,
        source_tier_t4_pct=5.0,
        vendor_diversity_count=4,
        counter_argument_ratio=0.20,
        recency_within_18mo_pct=80.0,
        paid_content_disclosed=True,
        bias_check_complete=True,
        threshold_pass=True,
        failed_thresholds=[],
    )


def _run_summary():
    from openclaw.fairness_scorer import RunSummary
    return RunSummary(
        run_summary_id="rs-1",
        mlflow_run_id="fake-run-id-123",
        brief_id="b-1",
        started_at=datetime(2026, 5, 7, 11, 0, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 7, 11, 5, 0, tzinfo=timezone.utc),
        model_id="google/gemini-2.5-flash",
        prompt_version="virtue-prompt-v1",
        agent_version="openclaw-v4.0",
        input_tokens=1500,
        output_tokens=600,
        total_cost_usd=0.0023,
        source_count=5,
        unique_domain_count=4,
        latency_seconds=42.7,
        retry_count=0,
    )


def test_unavailable_when_mlflow_missing(monkeypatch):
    """Tracker raises a clear error when the mlflow package isn't installed."""
    from openclaw.mlflow_tracker import MLflowTracker, MLflowUnavailableError
    # Hide any existing mlflow + block re-import
    monkeypatch.setitem(sys.modules, "mlflow", None)
    t = MLflowTracker()
    with pytest.raises(MLflowUnavailableError):
        t._get_mlflow()


def test_run_context_manager_starts_and_ends(mlflow_module):
    from openclaw.mlflow_tracker import MLflowTracker
    t = MLflowTracker(tracking_uri="http://test:5000")
    with t.run(topic="ai-safety", model_id="x") as run_id:
        assert run_id == "fake-run-id-123"
    mlflow_module.start_run.assert_called_once()
    mlflow_module.end_run.assert_called_once()
    mlflow_module.log_param.assert_any_call("topic", "ai-safety")
    mlflow_module.log_param.assert_any_call("model_id", "x")


def test_run_context_ends_on_exception(mlflow_module):
    from openclaw.mlflow_tracker import MLflowTracker
    t = MLflowTracker()
    with pytest.raises(RuntimeError):
        with t.run(topic="x"):
            raise RuntimeError("boom")
    mlflow_module.end_run.assert_called_once()


def test_log_scorecard_emits_metrics_and_tags(mlflow_module):
    from openclaw.mlflow_tracker import MLflowTracker
    t = MLflowTracker()
    sc = _scorecard()
    with t.run(topic="x"):
        t.log_scorecard(sc)

    metric_calls = mlflow_module.log_metric.call_args_list
    metric_names = {c.args[0] for c in metric_calls}
    assert "source_tier_t1_pct" in metric_names
    assert "vendor_diversity_count" in metric_names
    assert "counter_argument_ratio" in metric_names
    assert "recency_within_18mo_pct" in metric_names

    tag_calls = mlflow_module.set_tag.call_args_list
    tag_names = {c.args[0] for c in tag_calls}
    assert "threshold_pass" in tag_names
    assert "bias_check_complete" in tag_names


def test_log_scorecard_includes_failed_thresholds_tag(mlflow_module):
    from openclaw.mlflow_tracker import MLflowTracker
    t = MLflowTracker()
    sc = _scorecard().model_copy(update={
        "threshold_pass": False,
        "failed_thresholds": ["source_tier_t1_t2_share", "recency"],
    })
    with t.run(topic="x"):
        t.log_scorecard(sc)

    failed_tag = next(
        (c for c in mlflow_module.set_tag.call_args_list
         if c.args[0] == "failed_thresholds"),
        None,
    )
    assert failed_tag is not None
    assert failed_tag.args[1] == "source_tier_t1_t2_share,recency"


def test_log_run_summary_emits_operational_metrics(mlflow_module):
    from openclaw.mlflow_tracker import MLflowTracker
    t = MLflowTracker()
    rs = _run_summary()
    with t.run(topic="x"):
        t.log_run_summary(rs)

    metric_names = {c.args[0] for c in mlflow_module.log_metric.call_args_list}
    assert "input_tokens" in metric_names
    assert "output_tokens" in metric_names
    assert "total_cost_usd" in metric_names
    assert "latency_seconds" in metric_names


def test_log_text_artifact(mlflow_module):
    from openclaw.mlflow_tracker import MLflowTracker
    t = MLflowTracker()
    with t.run(topic="x"):
        t.log_text_artifact("brief.md", "# Sample brief\n\nbody")
    mlflow_module.log_text.assert_called_once_with("# Sample brief\n\nbody", "brief.md")


def test_tracking_uri_from_env(monkeypatch, mlflow_module):
    from openclaw.mlflow_tracker import MLflowTracker
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://custom:5050")
    t = MLflowTracker()
    t._get_mlflow()  # triggers lazy init
    mlflow_module.set_tracking_uri.assert_called_with("http://custom:5050")
