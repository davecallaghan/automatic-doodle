"""Unit tests for fairness_scorer (Phase 9).

Pure-Python tests — no Delta dependency, no MLflow dependency. The scorer is
deterministic by design; tests pin a synthetic "today" via the `now` arg so
the recency calculation doesn't depend on wall-clock time.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest


# -----------------------------------------------------------------------------
# Helpers — synthetic briefs and citations near the threshold boundaries
# -----------------------------------------------------------------------------

NOW = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
TODAY = NOW.date()
RECENT = TODAY  # 0 days old
OLD = date(2024, 1, 1)  # ~16 months before TODAY (>540 days, so "not recent")


def _brief(counter_arg_words: int = 100, total_extra_words: int = 400):
    from openclaw.databricks_worker import ValidatedBrief
    counter_arg = " ".join(["counter"] * max(counter_arg_words, 17))
    # 17 words minimum so that len >= 100 chars passes the silver CHECK
    extra = " ".join(["extra"] * total_extra_words)
    return ValidatedBrief(
        brief_id="b-1",
        response_id="r-1",
        created_at=NOW,
        topic_id="t",
        title="Title",
        key_findings=extra,
        counter_arguments=counter_arg,
        practical_implications="impl",
        further_reading="reading",
        content_hash="x" * 64,
        agent_version="v1",
    )


def _citation(
    cid: str = "c-1",
    tier: int = 1,
    vendor: str | None = "Acme",
    pub_date: date | None = None,
    paid: bool = False,
):
    from openclaw.databricks_worker import Citation
    kwargs = dict(
        citation_id=cid, brief_id="b-1", url="https://example.com",
        title="t", authority_tier=tier, vendor_org=vendor,
        publication_date=pub_date, is_paid_content=paid,
    )
    if tier == 4:
        kwargs["t4_justification"] = "edge case"
    return Citation(**kwargs)


def _full_bias_check():
    from openclaw.fairness_scorer import BiasChecklist
    return BiasChecklist(
        only_confirming_sources="No, I searched broadly.",
        competing_perspectives_included="Yes, included two competitors.",
        limitations_acknowledged="Yes, noted dataset gaps.",
        financial_incentive_disclosed="Yes, vendor blog flagged.",
        publication_date_checked="Yes, all within 12 months.",
    )


def _scorer():
    from openclaw.fairness_scorer import FairnessScorer
    return FairnessScorer(now=NOW)


# -----------------------------------------------------------------------------
# Threshold defaults
# -----------------------------------------------------------------------------

def test_thresholds_default_values():
    from openclaw.fairness_scorer import FairnessThresholds
    t = FairnessThresholds()
    assert t.t1_t2_min_share == 0.60
    assert t.vendor_diversity_min == 3
    assert t.counter_argument_min_ratio == 0.15
    assert t.recency_min_share == 0.50
    assert t.recency_window_days == 540


def test_thresholds_from_env(monkeypatch):
    from openclaw.fairness_scorer import FairnessThresholds
    monkeypatch.setenv("FAIRNESS_T1_T2_MIN", "0.75")
    monkeypatch.setenv("FAIRNESS_VENDOR_MIN", "5")
    t = FairnessThresholds.from_env()
    assert t.t1_t2_min_share == 0.75
    assert t.vendor_diversity_min == 5


# -----------------------------------------------------------------------------
# BiasChecklist
# -----------------------------------------------------------------------------

def test_bias_check_empty_is_incomplete():
    from openclaw.fairness_scorer import BiasChecklist
    bc = BiasChecklist()
    assert not bc.is_complete
    assert not bc.has_paid_content_answer


def test_bias_check_full_is_complete():
    bc = _full_bias_check()
    assert bc.is_complete
    assert bc.has_paid_content_answer


def test_bias_check_partial_is_incomplete():
    from openclaw.fairness_scorer import BiasChecklist
    bc = BiasChecklist(
        only_confirming_sources="No",
        competing_perspectives_included="Yes",
        limitations_acknowledged="Yes",
        financial_incentive_disclosed="Yes",
        # publication_date_checked missing
    )
    assert not bc.is_complete


def test_bias_check_whitespace_only_is_incomplete():
    from openclaw.fairness_scorer import BiasChecklist
    bc = BiasChecklist(
        only_confirming_sources="   ",
        competing_perspectives_included="Yes",
        limitations_acknowledged="Yes",
        financial_incentive_disclosed="Yes",
        publication_date_checked="Yes",
    )
    assert not bc.is_complete


# -----------------------------------------------------------------------------
# Tier distribution
# -----------------------------------------------------------------------------

def test_tier_distribution_all_tier1():
    s = _scorer()
    citations = [_citation(f"c-{i}", tier=1) for i in range(5)]
    sc = s.score(_brief(), citations, _full_bias_check())
    assert sc.source_tier_t1_pct == 100.0
    assert sc.source_tier_t4_pct == 0.0


def test_tier_distribution_mixed():
    s = _scorer()
    citations = [
        _citation("c-1", tier=1),
        _citation("c-2", tier=2),
        _citation("c-3", tier=3),
        _citation("c-4", tier=4),
    ]
    sc = s.score(_brief(), citations, _full_bias_check())
    assert sc.source_tier_t1_pct == 25.0
    assert sc.source_tier_t2_pct == 25.0
    assert sc.source_tier_t3_pct == 25.0
    assert sc.source_tier_t4_pct == 25.0


def test_t1_t2_below_60_fails():
    """50% T1+T2 fails the 60% threshold."""
    s = _scorer()
    citations = [
        _citation("c-1", tier=1, vendor="A"),
        _citation("c-2", tier=2, vendor="B"),
        _citation("c-3", tier=3, vendor="C"),
        _citation("c-4", tier=4, vendor="D"),
    ]
    sc = s.score(_brief(), citations, _full_bias_check())
    # T1+T2 = 50% < 60% threshold
    assert "source_tier_t1_t2_share" in sc.failed_thresholds


def test_t1_t2_at_60_passes():
    """60% T1+T2 (exactly) passes the threshold."""
    s = _scorer()
    citations = [
        _citation("c-1", tier=1, vendor="A", pub_date=RECENT),
        _citation("c-2", tier=1, vendor="B", pub_date=RECENT),
        _citation("c-3", tier=2, vendor="C", pub_date=RECENT),
        _citation("c-4", tier=3, vendor="D", pub_date=RECENT),
        _citation("c-5", tier=4, vendor="E", pub_date=RECENT),
    ]
    sc = s.score(_brief(), citations, _full_bias_check())
    assert "source_tier_t1_t2_share" not in sc.failed_thresholds


# -----------------------------------------------------------------------------
# Vendor diversity
# -----------------------------------------------------------------------------

def test_vendor_diversity_below_min_fails():
    s = _scorer()
    citations = [
        _citation(f"c-{i}", tier=1, vendor="OnlyVendor", pub_date=RECENT)
        for i in range(5)
    ]
    sc = s.score(_brief(), citations, _full_bias_check())
    assert sc.vendor_diversity_count == 1
    assert "vendor_diversity" in sc.failed_thresholds


def test_vendor_diversity_at_min_passes():
    s = _scorer()
    citations = [
        _citation("c-1", tier=1, vendor="A", pub_date=RECENT),
        _citation("c-2", tier=1, vendor="B", pub_date=RECENT),
        _citation("c-3", tier=2, vendor="C", pub_date=RECENT),
    ]
    sc = s.score(_brief(), citations, _full_bias_check())
    assert sc.vendor_diversity_count == 3
    assert "vendor_diversity" not in sc.failed_thresholds


def test_vendor_diversity_single_vendor_justified():
    """Single vendor is OK if topic_metadata.single_vendor_justification is truthy."""
    s = _scorer()
    citations = [
        _citation(f"c-{i}", tier=1, vendor="Anthropic", pub_date=RECENT)
        for i in range(3)
    ]
    sc = s.score(
        _brief(), citations, _full_bias_check(),
        topic_metadata={"single_vendor_justification": "topic is anthropic-specific"},
    )
    assert sc.vendor_diversity_count == 1
    assert "vendor_diversity" not in sc.failed_thresholds


def test_vendor_diversity_ignores_none_and_empty():
    s = _scorer()
    citations = [
        _citation("c-1", tier=1, vendor="A", pub_date=RECENT),
        _citation("c-2", tier=1, vendor=None, pub_date=RECENT),
        _citation("c-3", tier=1, vendor="", pub_date=RECENT),
    ]
    sc = s.score(_brief(), citations, _full_bias_check())
    assert sc.vendor_diversity_count == 1


# -----------------------------------------------------------------------------
# Counter-argument ratio
# -----------------------------------------------------------------------------

def test_counter_argument_ratio_pass():
    """100 counter-arg words out of ~500 total = 20% > 15% threshold."""
    s = _scorer()
    sc = s.score(_brief(counter_arg_words=100, total_extra_words=400),
                 [_citation(tier=1, pub_date=RECENT)] * 3, _full_bias_check())
    assert sc.counter_argument_ratio >= 0.15
    assert "counter_argument_ratio" not in sc.failed_thresholds


def test_counter_argument_ratio_fail():
    """20 counter-arg words out of ~1000 total = 2% < 15% threshold."""
    s = _scorer()
    sc = s.score(_brief(counter_arg_words=20, total_extra_words=1000),
                 [_citation(tier=1, pub_date=RECENT)] * 3, _full_bias_check())
    assert sc.counter_argument_ratio < 0.15
    assert "counter_argument_ratio" in sc.failed_thresholds


# -----------------------------------------------------------------------------
# Recency
# -----------------------------------------------------------------------------

def test_recency_all_recent_passes():
    s = _scorer()
    citations = [
        _citation(f"c-{i}", tier=1, vendor=f"V{i}", pub_date=RECENT)
        for i in range(3)
    ]
    sc = s.score(_brief(), citations, _full_bias_check())
    assert sc.recency_within_18mo_pct == 100.0
    assert "recency" not in sc.failed_thresholds


def test_recency_all_old_fails():
    s = _scorer()
    citations = [
        _citation(f"c-{i}", tier=1, vendor=f"V{i}", pub_date=OLD)
        for i in range(3)
    ]
    sc = s.score(_brief(), citations, _full_bias_check())
    assert sc.recency_within_18mo_pct == 0.0
    assert "recency" in sc.failed_thresholds


def test_recency_no_dates_treated_as_old():
    """Citations without publication_date are excluded from numerator only;
    a brief with only undated citations fails the recency threshold."""
    s = _scorer()
    citations = [_citation(f"c-{i}", tier=1, vendor=f"V{i}", pub_date=None)
                 for i in range(3)]
    sc = s.score(_brief(), citations, _full_bias_check())
    assert sc.recency_within_18mo_pct == 0.0
    assert "recency" in sc.failed_thresholds


def test_recency_historical_topic_exempt():
    s = _scorer()
    citations = [_citation(f"c-{i}", tier=1, vendor=f"V{i}", pub_date=OLD)
                 for i in range(3)]
    sc = s.score(_brief(), citations, _full_bias_check(),
                 topic_metadata={"historical_topic": True})
    assert "recency" not in sc.failed_thresholds


# -----------------------------------------------------------------------------
# Bias checklist threshold
# -----------------------------------------------------------------------------

def test_bias_check_missing_fails():
    s = _scorer()
    citations = [_citation(f"c-{i}", tier=1, vendor=f"V{i}", pub_date=RECENT)
                 for i in range(3)]
    sc = s.score(_brief(), citations, bias_check=None)
    assert not sc.bias_check_complete
    assert "bias_check_complete" in sc.failed_thresholds
    assert not sc.paid_content_disclosed


# -----------------------------------------------------------------------------
# Overall threshold pass / decision logic
# -----------------------------------------------------------------------------

def test_clean_brief_passes_all_thresholds():
    """All metrics in the passing zone → status DRAFT."""
    from openclaw.fairness_scorer import decide_status
    s = _scorer()
    citations = [
        _citation("c-1", tier=1, vendor="A", pub_date=RECENT),
        _citation("c-2", tier=1, vendor="B", pub_date=RECENT),
        _citation("c-3", tier=2, vendor="C", pub_date=RECENT),
    ]
    sc = s.score(_brief(counter_arg_words=100, total_extra_words=400),
                 citations, _full_bias_check())
    assert sc.threshold_pass
    assert sc.failed_thresholds == []
    assert decide_status(sc) == "DRAFT"


def test_brief_failing_one_metric_is_rejected():
    """Single threshold failure → status REJECTED."""
    from openclaw.fairness_scorer import decide_status
    s = _scorer()
    citations = [
        _citation(f"c-{i}", tier=4, vendor=f"V{i}", pub_date=RECENT)
        for i in range(3)
    ]
    sc = s.score(_brief(), citations, _full_bias_check())
    assert not sc.threshold_pass
    assert decide_status(sc) == "REJECTED"


def test_brief_failing_multiple_metrics_records_all():
    """All failures are surfaced — never silently masked by another failure."""
    s = _scorer()
    citations = [
        _citation("c-1", tier=4, vendor="OnlyVendor", pub_date=OLD),
    ]
    sc = s.score(_brief(counter_arg_words=5, total_extra_words=500),
                 citations, bias_check=None)
    expected_failures = {
        "source_tier_t1_t2_share",
        "vendor_diversity",
        "counter_argument_ratio",
        "recency",
        "bias_check_complete",
    }
    assert set(sc.failed_thresholds) == expected_failures


# -----------------------------------------------------------------------------
# 10-brief synthetic boundary sweep — the integration point of the spec
# -----------------------------------------------------------------------------

@pytest.mark.parametrize("scenario,citations_kwargs,bias,expect_pass,expect_failures", [
    ("all-T1-recent-3-vendors",
     [
         dict(cid="c1", tier=1, vendor="A", pub_date=RECENT),
         dict(cid="c2", tier=1, vendor="B", pub_date=RECENT),
         dict(cid="c3", tier=1, vendor="C", pub_date=RECENT),
     ], True, True, []),
    ("all-T2-recent-3-vendors",
     [
         dict(cid="c1", tier=2, vendor="A", pub_date=RECENT),
         dict(cid="c2", tier=2, vendor="B", pub_date=RECENT),
         dict(cid="c3", tier=2, vendor="C", pub_date=RECENT),
     ], True, True, []),
    ("all-T3-fails-tier",
     [
         dict(cid="c1", tier=3, vendor="A", pub_date=RECENT),
         dict(cid="c2", tier=3, vendor="B", pub_date=RECENT),
         dict(cid="c3", tier=3, vendor="C", pub_date=RECENT),
     ], True, False, ["source_tier_t1_t2_share"]),
    ("all-T4-fails-tier",
     [
         dict(cid="c1", tier=4, vendor="A", pub_date=RECENT),
         dict(cid="c2", tier=4, vendor="B", pub_date=RECENT),
         dict(cid="c3", tier=4, vendor="C", pub_date=RECENT),
     ], True, False, ["source_tier_t1_t2_share"]),
    ("60-40-T1T2-T3T4-passes",
     [
         dict(cid="c1", tier=1, vendor="A", pub_date=RECENT),
         dict(cid="c2", tier=1, vendor="B", pub_date=RECENT),
         dict(cid="c3", tier=2, vendor="C", pub_date=RECENT),
         dict(cid="c4", tier=3, vendor="D", pub_date=RECENT),
         dict(cid="c5", tier=4, vendor="E", pub_date=RECENT),
     ], True, True, []),
    ("single-vendor-no-justification-fails",
     [
         dict(cid="c1", tier=1, vendor="Solo", pub_date=RECENT),
         dict(cid="c2", tier=1, vendor="Solo", pub_date=RECENT),
         dict(cid="c3", tier=1, vendor="Solo", pub_date=RECENT),
     ], True, False, ["vendor_diversity"]),
    ("two-vendors-fails-by-one",
     [
         dict(cid="c1", tier=1, vendor="A", pub_date=RECENT),
         dict(cid="c2", tier=1, vendor="A", pub_date=RECENT),
         dict(cid="c3", tier=1, vendor="B", pub_date=RECENT),
     ], True, False, ["vendor_diversity"]),
    ("all-old-fails-recency",
     [
         dict(cid="c1", tier=1, vendor="A", pub_date=OLD),
         dict(cid="c2", tier=1, vendor="B", pub_date=OLD),
         dict(cid="c3", tier=1, vendor="C", pub_date=OLD),
     ], True, False, ["recency"]),
    ("missing-bias-fails",
     [
         dict(cid="c1", tier=1, vendor="A", pub_date=RECENT),
         dict(cid="c2", tier=1, vendor="B", pub_date=RECENT),
         dict(cid="c3", tier=1, vendor="C", pub_date=RECENT),
     ], False, False, ["bias_check_complete"]),
    ("multi-failure",
     [
         dict(cid="c1", tier=4, vendor="Only", pub_date=OLD),
     ], False, False, [
         "source_tier_t1_t2_share", "vendor_diversity", "recency", "bias_check_complete"
     ]),
])
def test_threshold_boundary_sweep(scenario, citations_kwargs, bias, expect_pass, expect_failures):
    s = _scorer()
    citations = [_citation(**ckw) for ckw in citations_kwargs]
    bias_check = _full_bias_check() if bias else None
    sc = s.score(_brief(), citations, bias_check)
    assert sc.threshold_pass is expect_pass, scenario
    assert set(sc.failed_thresholds) == set(expect_failures), scenario


# -----------------------------------------------------------------------------
# Determinism — same inputs yield same metrics (modulo scorecard_id UUID)
# -----------------------------------------------------------------------------

def test_scorer_is_deterministic_for_metrics():
    s1 = _scorer()
    s2 = _scorer()
    citations = [_citation("c-1", tier=1, vendor="A", pub_date=RECENT)] * 3
    sc1 = s1.score(_brief(), citations, _full_bias_check())
    sc2 = s2.score(_brief(), citations, _full_bias_check())

    # All metrics identical
    for field in (
        "source_tier_t1_pct", "source_tier_t2_pct", "source_tier_t3_pct",
        "source_tier_t4_pct", "vendor_diversity_count",
        "counter_argument_ratio", "recency_within_18mo_pct",
        "paid_content_disclosed", "bias_check_complete", "threshold_pass",
        "failed_thresholds",
    ):
        assert getattr(sc1, field) == getattr(sc2, field), field

    # IDs differ (UUIDs); computed_at frozen via injected `now`
    assert sc1.scorecard_id != sc2.scorecard_id


# -----------------------------------------------------------------------------
# Empty edge cases
# -----------------------------------------------------------------------------

def test_no_citations_fails_multiple_thresholds():
    s = _scorer()
    sc = s.score(_brief(), [], _full_bias_check())
    assert not sc.threshold_pass
    assert "source_tier_t1_t2_share" in sc.failed_thresholds
    assert "vendor_diversity" in sc.failed_thresholds
    assert "recency" in sc.failed_thresholds


def test_research_summary_status_literal():
    """Pydantic enforces the status enum."""
    from openclaw.fairness_scorer import ResearchSummary
    with pytest.raises(Exception):
        ResearchSummary(
            summary_id="s", brief_id="b",
            created_at=NOW, status="WHATEVER",
            mlflow_run_id="r",
        )
