#!/usr/bin/env python3
"""
fairness_scorer.py — Phase 9 fairness scoring for OpenClaw research briefs.

Pure Python, no I/O. Takes a ValidatedBrief + list[Citation] + (optional)
BiasChecklist + topic metadata, returns a FairnessScorecard.

Six metrics are computed (per the master plan's threshold table):

  source_tier_t1_pct..t4_pct   distribution of authority tiers
  vendor_diversity_count       distinct vendor_orgs cited
  counter_argument_ratio       counter-arg words / total brief words
  recency_within_18mo_pct      share of citations dated within 540 days
  paid_content_disclosed       did the agent answer the paid-content bias
                                checklist question
  bias_check_complete          did the agent answer all five bias-checklist
                                questions

A brief that fails any threshold lands in research.gold.research_summaries
with status='REJECTED'; the failed metric names go in `failed_thresholds`.
A human can still override (Phase 10 promotion CLI), but only deliberately —
fairness failures are surfaced loudly, never silently dropped.

The scorer is a pure function of its inputs; deterministic, idempotent, and
unit-testable in isolation. Wiring into DatabricksWorker happens via opt-in
constructor argument so Phase 7's Phase-7-only behavior keeps working.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from openclaw.databricks_worker import Citation, ValidatedBrief


# -----------------------------------------------------------------------------
# Thresholds — overridable via env or constructor for testing/policy changes
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class FairnessThresholds:
    """Defaults from EXECUTION_databricks_integration.md "Fairness Scorecard"."""
    t1_t2_min_share: float = 0.60
    vendor_diversity_min: int = 3
    counter_argument_min_ratio: float = 0.15
    recency_min_share: float = 0.50
    recency_window_days: int = 540  # 18 months

    @classmethod
    def from_env(cls) -> "FairnessThresholds":
        """Read overrides from FAIRNESS_* env vars; fall back to defaults."""
        return cls(
            t1_t2_min_share=float(os.environ.get("FAIRNESS_T1_T2_MIN", cls.t1_t2_min_share)),
            vendor_diversity_min=int(os.environ.get("FAIRNESS_VENDOR_MIN", cls.vendor_diversity_min)),
            counter_argument_min_ratio=float(os.environ.get("FAIRNESS_CA_MIN", cls.counter_argument_min_ratio)),
            recency_min_share=float(os.environ.get("FAIRNESS_RECENCY_MIN", cls.recency_min_share)),
            recency_window_days=int(os.environ.get("FAIRNESS_RECENCY_DAYS", cls.recency_window_days)),
        )


# -----------------------------------------------------------------------------
# Bias checklist — the agent's self-reported answers to the five questions
# from workspace/virtue_prompt.md
# -----------------------------------------------------------------------------

class BiasChecklist(BaseModel):
    """The five bias-awareness questions from the Virtue Protocol.

    Each field accepts a free-text answer. The metric `bias_check_complete`
    is True iff all five are non-empty. The semantic content of the answers
    is for human review (Phase 10 promotion CLI), not for automated scoring.
    """

    only_confirming_sources: str | None = None
    competing_perspectives_included: str | None = None
    limitations_acknowledged: str | None = None
    financial_incentive_disclosed: str | None = None
    publication_date_checked: str | None = None

    @property
    def is_complete(self) -> bool:
        return all(
            v is not None and len(v.strip()) > 0
            for v in (
                self.only_confirming_sources,
                self.competing_perspectives_included,
                self.limitations_acknowledged,
                self.financial_incentive_disclosed,
                self.publication_date_checked,
            )
        )

    @property
    def has_paid_content_answer(self) -> bool:
        return (
            self.financial_incentive_disclosed is not None
            and len(self.financial_incentive_disclosed.strip()) > 0
        )


# -----------------------------------------------------------------------------
# Fairness scorecard — Pydantic model mirroring research.gold.fairness_scorecards
# -----------------------------------------------------------------------------

class FairnessScorecard(BaseModel):
    """research.gold.fairness_scorecards"""

    model_config = {"frozen": True}

    scorecard_id: str
    brief_id: str
    computed_at: datetime
    source_tier_t1_pct: float = Field(ge=0, le=100)
    source_tier_t2_pct: float = Field(ge=0, le=100)
    source_tier_t3_pct: float = Field(ge=0, le=100)
    source_tier_t4_pct: float = Field(ge=0, le=100)
    vendor_diversity_count: int = Field(ge=0)
    counter_argument_ratio: float = Field(ge=0, le=1)
    recency_within_18mo_pct: float = Field(ge=0, le=100)
    paid_content_disclosed: bool
    bias_check_complete: bool
    threshold_pass: bool
    failed_thresholds: list[str]


# -----------------------------------------------------------------------------
# Run summary + research summary — Pydantic models for the other gold tables
# -----------------------------------------------------------------------------

class RunSummary(BaseModel):
    """research.gold.run_summaries — operational metrics for one research run."""

    model_config = {"protected_namespaces": ()}

    run_summary_id: str
    mlflow_run_id: str
    brief_id: str
    started_at: datetime
    ended_at: datetime
    model_id: str
    prompt_version: str
    agent_version: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_cost_usd: float = Field(ge=0)
    source_count: int = Field(ge=0)
    unique_domain_count: int = Field(ge=0)
    latency_seconds: float = Field(ge=0)
    retry_count: int = Field(ge=0)


class ResearchSummary(BaseModel):
    """research.gold.research_summaries — the reviewable brief with status."""

    summary_id: str
    brief_id: str
    created_at: datetime
    status: Literal["DRAFT", "REJECTED", "PROMOTED"]
    rejection_reasons: list[str] | None = None
    override_reason: str | None = None
    promoted_at: datetime | None = None
    promoted_by: str | None = None
    mlflow_run_id: str

    @field_validator("rejection_reasons")
    @classmethod
    def _rejection_reasons_only_when_rejected(cls, v, info):
        # We can't enforce status-specific cross-field rules trivially with
        # field_validator; this is informational. The worker is expected to
        # populate rejection_reasons iff status='REJECTED'.
        return v


# -----------------------------------------------------------------------------
# The scorer — pure function from (brief, citations, checklist, metadata) to scorecard
# -----------------------------------------------------------------------------

class FairnessScorer:
    """Deterministic, no-I/O scorer. Same inputs always yield the same scorecard."""

    def __init__(
        self,
        thresholds: FairnessThresholds | None = None,
        now: datetime | None = None,
    ) -> None:
        self.thresholds = thresholds or FairnessThresholds()
        # `now` is injectable so tests can pin a deterministic "today" for
        # the recency calculation.
        self._now = now

    def _today(self) -> date:
        if self._now is not None:
            return self._now.date()
        return datetime.now(timezone.utc).date()

    def score(
        self,
        brief: ValidatedBrief,
        citations: list[Citation],
        bias_check: BiasChecklist | None = None,
        topic_metadata: dict | None = None,
    ) -> FairnessScorecard:
        topic_metadata = topic_metadata or {}
        n = len(citations)

        # ---- Tier distribution
        if n == 0:
            t1_pct = t2_pct = t3_pct = t4_pct = 0.0
        else:
            t1_pct = sum(1 for c in citations if c.authority_tier == 1) / n * 100
            t2_pct = sum(1 for c in citations if c.authority_tier == 2) / n * 100
            t3_pct = sum(1 for c in citations if c.authority_tier == 3) / n * 100
            t4_pct = sum(1 for c in citations if c.authority_tier == 4) / n * 100

        # ---- Vendor diversity
        vendors = {
            c.vendor_org for c in citations
            if c.vendor_org and c.vendor_org.strip()
        }
        vendor_diversity_count = len(vendors)

        # ---- Counter-argument ratio
        counter_argument_ratio = self._counter_argument_ratio(brief)

        # ---- Recency
        if n == 0:
            recency_pct = 0.0
        else:
            today = self._today()
            window = self.thresholds.recency_window_days
            within = sum(
                1 for c in citations
                if c.publication_date is not None
                and (today - c.publication_date).days <= window
            )
            recency_pct = within / n * 100

        # ---- Bias checklist + paid-content disclosure
        bias_complete = bias_check.is_complete if bias_check else False
        paid_disclosed = bias_check.has_paid_content_answer if bias_check else False

        # ---- Threshold check
        failed: list[str] = []
        single_vendor_ok = bool(topic_metadata.get("single_vendor_justification"))
        historical_topic = bool(topic_metadata.get("historical_topic"))

        t1_t2_share = (t1_pct + t2_pct) / 100
        if t1_t2_share < self.thresholds.t1_t2_min_share:
            failed.append("source_tier_t1_t2_share")

        if (
            vendor_diversity_count < self.thresholds.vendor_diversity_min
            and not single_vendor_ok
        ):
            failed.append("vendor_diversity")

        if counter_argument_ratio < self.thresholds.counter_argument_min_ratio:
            failed.append("counter_argument_ratio")

        if (
            (recency_pct / 100) < self.thresholds.recency_min_share
            and not historical_topic
        ):
            failed.append("recency")

        if not bias_complete:
            failed.append("bias_check_complete")

        return FairnessScorecard(
            scorecard_id=str(uuid.uuid4()),
            brief_id=brief.brief_id,
            computed_at=self._now or datetime.now(timezone.utc),
            source_tier_t1_pct=t1_pct,
            source_tier_t2_pct=t2_pct,
            source_tier_t3_pct=t3_pct,
            source_tier_t4_pct=t4_pct,
            vendor_diversity_count=vendor_diversity_count,
            counter_argument_ratio=counter_argument_ratio,
            recency_within_18mo_pct=recency_pct,
            paid_content_disclosed=paid_disclosed,
            bias_check_complete=bias_complete,
            threshold_pass=len(failed) == 0,
            failed_thresholds=failed,
        )

    @staticmethod
    def _counter_argument_ratio(brief: ValidatedBrief) -> float:
        ca_words = len(brief.counter_arguments.split()) if brief.counter_arguments else 0
        total = " ".join(
            s for s in (
                brief.title,
                brief.key_findings,
                brief.counter_arguments,
                brief.practical_implications,
                brief.further_reading,
                brief.known_gaps or "",
            )
            if s
        )
        total_words = len(total.split())
        return ca_words / total_words if total_words else 0.0


# -----------------------------------------------------------------------------
# Status decision — separate from the scorer so policy can change without
# rewriting the scorer
# -----------------------------------------------------------------------------

def decide_status(scorecard: FairnessScorecard) -> Literal["DRAFT", "REJECTED"]:
    """Map a fairness scorecard to a research_summary status. The agent is
    never allowed to write 'PROMOTED' — that's a human decision in Phase 10.
    """
    return "DRAFT" if scorecard.threshold_pass else "REJECTED"
