"""Unit tests for promote (Phase 10).

Pure-Python — uses an in-memory FakeDelta to exercise the full promote/reject
logic without needing deltalake/pyarrow. The integration with Phase 8's
ChainWriter is exercised in the test_chain_hook_calls_writer test below.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

import pytest


# -----------------------------------------------------------------------------
# In-memory delta fake
# -----------------------------------------------------------------------------

class FakeDelta:
    catalog = "research"

    def __init__(self) -> None:
        self.tables: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def append(self, schema: str, table: str, rows: list[dict[str, Any]]) -> None:
        self.tables.setdefault((schema, table), []).extend(rows)

    def path_for(self, schema: str, table: str) -> str:
        return f"/tmp/fake/{schema}/{table}"


class FakeReader:
    """Stand-in for BriefReader that reads from FakeDelta directly (no Delta)."""

    def __init__(self, fake: FakeDelta) -> None:
        self.fake = fake

    def _table(self, schema: str, table: str):
        return list(self.fake.tables.get((schema, table), []))

    def latest_summaries(self, status_filter: str | None = None):
        from openclaw.fairness_scorer import ResearchSummary
        rows = self._table("gold", "research_summaries")
        latest: dict[str, dict[str, Any]] = {}
        for r in rows:
            sid = r["summary_id"]
            if sid not in latest or r["created_at"] > latest[sid]["created_at"]:
                latest[sid] = r
        out = [ResearchSummary.model_validate(r) for r in latest.values()]
        if status_filter:
            out = [s for s in out if s.status == status_filter]
        out.sort(key=lambda s: s.created_at, reverse=True)
        return out

    def summary_history(self, summary_id: str):
        from openclaw.fairness_scorer import ResearchSummary
        rows = [r for r in self._table("gold", "research_summaries")
                if r["summary_id"] == summary_id]
        rows.sort(key=lambda r: r["created_at"])
        return [ResearchSummary.model_validate(r) for r in rows]

    def get_brief(self, brief_id: str):
        from openclaw.databricks_worker import ValidatedBrief
        rows = [r for r in self._table("silver", "validated_briefs")
                if r["brief_id"] == brief_id]
        return ValidatedBrief.model_validate(rows[0]) if rows else None

    def citations_for(self, brief_id: str):
        from openclaw.databricks_worker import Citation
        rows = [r for r in self._table("silver", "citations")
                if r["brief_id"] == brief_id]
        rows.sort(key=lambda r: r["citation_id"])
        return [Citation.model_validate(r) for r in rows]

    def latest_scorecard_for(self, brief_id: str):
        from openclaw.fairness_scorer import FairnessScorecard
        rows = [r for r in self._table("gold", "fairness_scorecards")
                if r["brief_id"] == brief_id]
        if not rows:
            return None
        rows.sort(key=lambda r: r["computed_at"], reverse=True)
        return FairnessScorecard.model_validate(rows[0])


# -----------------------------------------------------------------------------
# Fixtures: a DRAFT brief seeded into the fake datastore
# -----------------------------------------------------------------------------

NOW = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)


def _seeded_world(*, status: str = "DRAFT", failed_thresholds=None):
    """Return (FakeDelta, FakeReader, summary_id, brief_id) with one brief
    + 3 citations + 1 scorecard + 1 research_summary already written."""
    from openclaw.databricks_worker import Citation, ValidatedBrief
    from openclaw.fairness_scorer import FairnessScorecard, ResearchSummary

    fake = FakeDelta()

    brief_id = "br-001"
    summary_id = "sum-001"
    response_id = "rs-001"

    brief = ValidatedBrief(
        brief_id=brief_id, response_id=response_id, created_at=NOW,
        topic_id="ai-safety", title="Test Brief on AI Safety",
        key_findings="Detailed findings about safety research. " * 20,
        counter_arguments="Detailed counter-arguments here. " * 25,
        practical_implications="Implications follow. " * 5,
        further_reading="Further reading here. " * 5,
        known_gaps="Some gaps noted.",
        content_hash="a" * 64, agent_version="openclaw-v4.0",
    )
    fake.append("silver", "validated_briefs", [brief.model_dump()])

    for i, (tier, vendor) in enumerate([(1, "Anthropic"), (1, "Google"), (2, "Databricks")]):
        c = Citation(
            citation_id=f"c-{i:03d}", brief_id=brief_id,
            url=f"https://example.com/{i}", title=f"Source {i}",
            authority_tier=tier, vendor_org=vendor,
            publication_date=date(2026, 1, 1), is_paid_content=False,
        )
        fake.append("silver", "citations", [c.model_dump()])

    sc = FairnessScorecard(
        scorecard_id="sc-001", brief_id=brief_id, computed_at=NOW,
        source_tier_t1_pct=66.7, source_tier_t2_pct=33.3,
        source_tier_t3_pct=0.0, source_tier_t4_pct=0.0,
        vendor_diversity_count=3, counter_argument_ratio=0.30,
        recency_within_18mo_pct=100.0, paid_content_disclosed=True,
        bias_check_complete=True,
        threshold_pass=(failed_thresholds is None),
        failed_thresholds=failed_thresholds or [],
    )
    fake.append("gold", "fairness_scorecards", [sc.model_dump()])

    summary = ResearchSummary(
        summary_id=summary_id, brief_id=brief_id, created_at=NOW,
        status=status,
        rejection_reasons=failed_thresholds,
        mlflow_run_id="run-001",
    )
    fake.append("gold", "research_summaries", [summary.model_dump()])

    return fake, FakeReader(fake), summary_id, brief_id


# -----------------------------------------------------------------------------
# PublishedBrief / PromotionLog Pydantic models
# -----------------------------------------------------------------------------

def test_published_brief_requires_hex64_content_hash():
    from openclaw.promote import PublishedBrief
    with pytest.raises(Exception):
        PublishedBrief(
            published_id="p", summary_id="s", published_at=NOW,
            license="CC BY 4.0", attribution="x", markdown_export="x",
            content_hash="not-hex",
        )
    PublishedBrief(
        published_id="p", summary_id="s", published_at=NOW,
        license="CC BY 4.0", attribution="x", markdown_export="x",
        content_hash="a" * 64,
    )


def test_promotion_log_requires_reviewer_name():
    from openclaw.promote import PromotionLog
    with pytest.raises(Exception):
        PromotionLog(
            promotion_id="p", summary_id="s", promoted_at=NOW,
            promoted_by="", prior_status="DRAFT", new_status="PROMOTED",
        )


# -----------------------------------------------------------------------------
# MarkdownExporter — determinism + structure
# -----------------------------------------------------------------------------

def test_markdown_is_deterministic():
    from openclaw.fairness_scorer import ResearchSummary
    from openclaw.promote import MarkdownExporter

    fake, reader, sid, bid = _seeded_world()
    brief = reader.get_brief(bid)
    citations = reader.citations_for(bid)
    sc = reader.latest_scorecard_for(bid)

    summary = ResearchSummary(
        summary_id=sid, brief_id=bid, created_at=NOW,
        status="PROMOTED", promoted_at=NOW, promoted_by="david",
        mlflow_run_id="r",
    )

    md1 = MarkdownExporter.render(
        brief=brief, citations=citations, scorecard=sc,
        published_id="pub-fixed", summary=summary, reviewer="david",
    )
    md2 = MarkdownExporter.render(
        brief=brief, citations=citations, scorecard=sc,
        published_id="pub-fixed", summary=summary, reviewer="david",
    )
    assert md1 == md2


def test_markdown_includes_required_frontmatter():
    from openclaw.fairness_scorer import ResearchSummary
    from openclaw.promote import MarkdownExporter

    fake, reader, sid, bid = _seeded_world()
    brief = reader.get_brief(bid)
    citations = reader.citations_for(bid)
    summary = ResearchSummary(
        summary_id=sid, brief_id=bid, created_at=NOW,
        status="PROMOTED", promoted_at=NOW, promoted_by="david",
        mlflow_run_id="r",
    )

    md = MarkdownExporter.render(
        brief=brief, citations=citations, scorecard=None,
        published_id="pub-001", summary=summary, reviewer="david",
    )
    assert md.startswith("---\n")
    assert "published_id: pub-001" in md
    assert f"summary_id: {sid}" in md
    assert "license: CC BY 4.0" in md
    # Body sections present
    assert "# Test Brief on AI Safety" in md
    assert "## Counter-Arguments" in md
    assert "## Citations" in md


def test_markdown_includes_override_section_when_present():
    from openclaw.fairness_scorer import ResearchSummary
    from openclaw.promote import MarkdownExporter

    fake, reader, sid, bid = _seeded_world(
        status="REJECTED", failed_thresholds=["recency", "vendor_diversity"]
    )
    brief = reader.get_brief(bid)
    citations = reader.citations_for(bid)
    sc = reader.latest_scorecard_for(bid)
    summary = ResearchSummary(
        summary_id=sid, brief_id=bid, created_at=NOW,
        status="PROMOTED", override_reason="niche topic — reviewer accepts the failures",
        promoted_at=NOW, promoted_by="david", mlflow_run_id="r",
        rejection_reasons=["recency", "vendor_diversity"],
    )

    md = MarkdownExporter.render(
        brief=brief, citations=citations, scorecard=sc,
        published_id="pub-001", summary=summary, reviewer="david",
    )
    assert 'override_reason: "niche topic' in md
    assert "Promotion override." in md
    assert "recency, vendor_diversity" in md or "vendor_diversity, recency" in md


def test_markdown_table_rows_match_citation_count():
    from openclaw.fairness_scorer import ResearchSummary
    from openclaw.promote import MarkdownExporter

    fake, reader, sid, bid = _seeded_world()
    brief = reader.get_brief(bid)
    citations = reader.citations_for(bid)
    summary = ResearchSummary(
        summary_id=sid, brief_id=bid, created_at=NOW,
        status="PROMOTED", promoted_at=NOW, promoted_by="d",
        mlflow_run_id="r",
    )
    md = MarkdownExporter.render(
        brief=brief, citations=citations, scorecard=None,
        published_id="p", summary=summary, reviewer="d",
    )
    # The table header is | # | Tier | Source | Vendor | Date |, then a separator,
    # then one row per citation. Look for body rows starting with "| 1 |", "| 2 |", etc.
    assert re.search(r"^\| 1 \| T1 \|", md, re.M)
    assert re.search(r"^\| 3 \| T2 \|", md, re.M)
    assert not re.search(r"^\| 4 \|", md, re.M)


# -----------------------------------------------------------------------------
# Promoter — approve/reject flow
# -----------------------------------------------------------------------------

def test_approve_draft_writes_three_rows():
    from openclaw.promote import Promoter
    fake, reader, sid, _bid = _seeded_world()

    promoter = Promoter(delta=fake, reader=reader, now=NOW)
    result = promoter.approve(sid, reviewer="david")

    # New research_summaries row appended
    summaries = fake.tables[("gold", "research_summaries")]
    assert len(summaries) == 2  # original DRAFT + new PROMOTED
    assert summaries[-1]["status"] == "PROMOTED"
    assert summaries[-1]["promoted_by"] == "david"

    # public_archive.published row appended
    pubs = fake.tables[("public_archive", "published")]
    assert len(pubs) == 1
    assert pubs[0]["summary_id"] == sid
    assert pubs[0]["license"] == "CC BY 4.0"
    assert len(pubs[0]["content_hash"]) == 64

    # promotion_log appended
    logs = fake.tables[("audit", "promotion_log")]
    assert len(logs) == 1
    assert logs[0]["prior_status"] == "DRAFT"
    assert logs[0]["new_status"] == "PROMOTED"

    # Returned data is consistent
    assert result["published"].content_hash == pubs[0]["content_hash"]


def test_approve_already_promoted_refused():
    from openclaw.promote import Promoter, PromotionError
    fake, reader, sid, _bid = _seeded_world(status="PROMOTED")
    promoter = Promoter(delta=fake, reader=reader, now=NOW)
    with pytest.raises(PromotionError, match="already PROMOTED"):
        promoter.approve(sid)


def test_approve_rejected_without_override_refused():
    from openclaw.promote import Promoter, PromotionError
    fake, reader, sid, _bid = _seeded_world(
        status="REJECTED", failed_thresholds=["recency"]
    )
    promoter = Promoter(delta=fake, reader=reader, now=NOW)
    with pytest.raises(PromotionError, match="--override REASON is required"):
        promoter.approve(sid)


def test_approve_rejected_with_override_works():
    from openclaw.promote import Promoter
    fake, reader, sid, _bid = _seeded_world(
        status="REJECTED", failed_thresholds=["recency"]
    )
    promoter = Promoter(delta=fake, reader=reader, now=NOW)
    result = promoter.approve(
        sid, reviewer="david", override_reason="historical-topic re-evaluation"
    )
    assert result["summary"].status == "PROMOTED"
    assert result["summary"].override_reason == "historical-topic re-evaluation"
    # Promotion log captures the prior status
    log = fake.tables[("audit", "promotion_log")][0]
    assert log["prior_status"] == "REJECTED"
    assert log["override_reason"] == "historical-topic re-evaluation"


def test_approve_unknown_summary_refused():
    from openclaw.promote import Promoter, PromotionError
    fake = FakeDelta()
    reader = FakeReader(fake)
    promoter = Promoter(delta=fake, reader=reader, now=NOW)
    with pytest.raises(PromotionError, match="not found"):
        promoter.approve("does-not-exist")


def test_reject_writes_two_rows():
    from openclaw.promote import Promoter
    fake, reader, sid, _bid = _seeded_world()
    promoter = Promoter(delta=fake, reader=reader, now=NOW)
    promoter.reject(sid, reason="factual error in claim 3", reviewer="david")

    summaries = fake.tables[("gold", "research_summaries")]
    assert summaries[-1]["status"] == "REJECTED"
    assert "reviewer:factual error in claim 3" in summaries[-1]["rejection_reasons"]

    logs = fake.tables[("audit", "promotion_log")]
    assert logs[-1]["prior_status"] == "DRAFT"
    assert logs[-1]["new_status"] == "REJECTED"
    assert logs[-1]["override_reason"] == "factual error in claim 3"


def test_reject_promoted_brief_refused():
    from openclaw.promote import Promoter, PromotionError
    fake, reader, sid, _bid = _seeded_world(status="PROMOTED")
    promoter = Promoter(delta=fake, reader=reader, now=NOW)
    with pytest.raises(PromotionError, match="retraction"):
        promoter.reject(sid, reason="late discovery")


def test_reject_requires_reason():
    from openclaw.promote import Promoter, PromotionError
    fake, reader, sid, _bid = _seeded_world()
    promoter = Promoter(delta=fake, reader=reader, now=NOW)
    with pytest.raises(PromotionError, match="--reason is required"):
        promoter.reject(sid, reason="")


# -----------------------------------------------------------------------------
# Chain hook integration — promotions produce chain entries when wired
# -----------------------------------------------------------------------------

def test_approve_calls_chain_writer_when_wired():
    from openclaw.promote import Promoter

    chain_calls: list[dict] = []

    class FakeChainWriter:
        def append_entry(self, **kwargs):
            chain_calls.append(kwargs)

    fake, reader, sid, _bid = _seeded_world()
    promoter = Promoter(
        delta=fake, reader=reader, chain_writer=FakeChainWriter(), now=NOW,
    )
    promoter.approve(sid, reviewer="david")

    # Three writes, three chain entries (gold.research_summaries, public_archive.published,
    # audit.promotion_log).
    assert len(chain_calls) == 3
    target_tables = {c["target_table"] for c in chain_calls}
    assert "research.gold.research_summaries" in target_tables
    assert "research.public_archive.published" in target_tables
    assert "research.audit.promotion_log" in target_tables
    for c in chain_calls:
        assert c["operation"] == "INSERT"
        assert c["author_identity"] == "david"
        assert len(c["target_payload_hash"]) == 64


def test_content_hash_changes_when_brief_changes():
    """Sanity: rendering different briefs produces different content_hash."""
    from openclaw.databricks_worker import ValidatedBrief
    from openclaw.fairness_scorer import ResearchSummary
    from openclaw.promote import MarkdownExporter
    from openclaw.databricks_worker import Hasher

    fake, reader, sid, bid = _seeded_world()
    brief1 = reader.get_brief(bid)
    citations = reader.citations_for(bid)
    summary = ResearchSummary(
        summary_id=sid, brief_id=bid, created_at=NOW,
        status="PROMOTED", promoted_at=NOW, promoted_by="d",
        mlflow_run_id="r",
    )

    md1 = MarkdownExporter.render(
        brief=brief1, citations=citations, scorecard=None,
        published_id="p", summary=summary, reviewer="d",
    )
    brief2 = brief1.model_copy(update={"title": "Different Title Now"})
    md2 = MarkdownExporter.render(
        brief=brief2, citations=citations, scorecard=None,
        published_id="p", summary=summary, reviewer="d",
    )
    assert Hasher.hash_string(md1) != Hasher.hash_string(md2)
