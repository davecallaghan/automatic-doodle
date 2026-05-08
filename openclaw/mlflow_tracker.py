#!/usr/bin/env python3
"""
mlflow_tracker.py — Phase 9 MLflow tracking wrapper for OpenClaw.

Thin facade over the `mlflow` Python client. Lazy-imports mlflow so this
module is importable in test environments without it. Production VMs install
mlflow via openclaw/requirements.txt.

Connects to the MLflow tracking server started by Phase 6 (sidecar at
http://mlflow-server:5000 inside the openclaw-net Docker network, or
http://localhost:5000 from the host).

Each research run is logged as a single MLflow experiment run:
  params:    topic, model_id, prompt_version, agent_version, prompt_hash
  metrics:   the six fairness metrics + token counts + latency
  tags:      threshold_pass (bool), failed_thresholds (csv), status
  artifacts: (caller-controlled — typically the brief markdown + sources)
"""

from __future__ import annotations

import contextlib
import os
from typing import Any, Iterator

from openclaw.fairness_scorer import FairnessScorecard, RunSummary


class MLflowUnavailableError(RuntimeError):
    """mlflow Python package not installed."""


class MLflowTracker:
    """Wrapper around the mlflow client. Lazy-imports the package."""

    def __init__(
        self,
        tracking_uri: str | None = None,
        experiment_name: str = "openclaw-research",
    ) -> None:
        self.tracking_uri = (
            tracking_uri
            or os.environ.get("MLFLOW_TRACKING_URI")
            or "http://localhost:5000"
        )
        self.experiment_name = experiment_name
        self._mlflow: Any = None  # populated by _get_mlflow on first use

    def _get_mlflow(self) -> Any:
        if self._mlflow is None:
            try:
                import mlflow  # type: ignore[import-not-found]
            except ImportError as e:
                raise MLflowUnavailableError(
                    "mlflow Python package not installed. "
                    "Run: pip install -r openclaw/requirements.txt"
                ) from e
            mlflow.set_tracking_uri(self.tracking_uri)
            mlflow.set_experiment(self.experiment_name)
            self._mlflow = mlflow
        return self._mlflow

    @contextlib.contextmanager
    def run(self, **params: Any) -> Iterator[str]:
        """Context manager: starts a run, logs params, ends on exit.

        Yields the run_id (str) so callers can store it in run_summary.
        """
        mlf = self._get_mlflow()
        active = mlf.start_run()
        run_id: str = active.info.run_id
        try:
            for key, value in params.items():
                mlf.log_param(key, value)
            yield run_id
        finally:
            mlf.end_run()

    def log_scorecard(self, scorecard: FairnessScorecard) -> None:
        """Log the six fairness metrics + threshold-pass tags. Safe to call
        only inside an active run.
        """
        mlf = self._get_mlflow()
        mlf.log_metric("source_tier_t1_pct", scorecard.source_tier_t1_pct)
        mlf.log_metric("source_tier_t2_pct", scorecard.source_tier_t2_pct)
        mlf.log_metric("source_tier_t3_pct", scorecard.source_tier_t3_pct)
        mlf.log_metric("source_tier_t4_pct", scorecard.source_tier_t4_pct)
        mlf.log_metric("vendor_diversity_count", scorecard.vendor_diversity_count)
        mlf.log_metric("counter_argument_ratio", scorecard.counter_argument_ratio)
        mlf.log_metric("recency_within_18mo_pct", scorecard.recency_within_18mo_pct)
        mlf.set_tag("paid_content_disclosed", str(scorecard.paid_content_disclosed))
        mlf.set_tag("bias_check_complete", str(scorecard.bias_check_complete))
        mlf.set_tag("threshold_pass", str(scorecard.threshold_pass))
        if scorecard.failed_thresholds:
            mlf.set_tag(
                "failed_thresholds", ",".join(scorecard.failed_thresholds)
            )

    def log_run_summary(self, summary: RunSummary) -> None:
        """Log operational metrics from a RunSummary. Safe to call only
        inside an active run.
        """
        mlf = self._get_mlflow()
        mlf.log_metric("input_tokens", summary.input_tokens)
        mlf.log_metric("output_tokens", summary.output_tokens)
        mlf.log_metric("total_cost_usd", summary.total_cost_usd)
        mlf.log_metric("source_count", summary.source_count)
        mlf.log_metric("unique_domain_count", summary.unique_domain_count)
        mlf.log_metric("latency_seconds", summary.latency_seconds)
        mlf.log_metric("retry_count", summary.retry_count)

    def log_text_artifact(self, name: str, text: str) -> None:
        """Persist a text blob as a run artifact (e.g., the brief markdown)."""
        mlf = self._get_mlflow()
        mlf.log_text(text, name)
