"""Phase 11 — adversarial validation suite.

These tests don't introduce new features; they confirm the boundaries
asserted by Phases 7-10 hold under attack. They focus on cross-cutting
scenarios that the per-module tests don't reach:

  Section A — code-boundary statics: prove there's no API path from the
              agent to the promotion surface.
  Section B — invariant exhaustion: assert architectural promises hold
              across many inputs.
  Section C — end-to-end attack scenarios: build a brief that fails
              fairness, push it through the worker, confirm it lands as
              REJECTED in gold and never reaches public_archive.
  Section D — post-promotion tamper detection.
  Section E — source integrity.
  Section F — chain pipeline integration.
  Section G — salt and signing edge cases.
  Section H — forged chain entry detection.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
OPENCLAW_DIR = REPO_ROOT / "openclaw"
NOW = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)
TEST_SALT = "0" * 64  # satisfies Salter's MIN_SALT_LENGTH


# =============================================================================
# Section A — code-boundary statics
# =============================================================================

AGENT_MODULES = [
    "databricks_worker.py",
    "integrity_engine.py",
    "fairness_scorer.py",
    "mlflow_tracker.py",
]


def test_agent_modules_do_not_import_promote_module():
    """Agent-side modules must not be able to reach the promotion surface.
    promote.py is meant to be invoked only by the human reviewer's CLI.
    """
    for mod in AGENT_MODULES:
        content = (OPENCLAW_DIR / mod).read_text()
        assert "from openclaw.promote" not in content, mod
        assert "import openclaw.promote" not in content, mod


def test_only_promote_module_writes_promoted_status():
    """Search every Python file under openclaw/ for the literal
    string `status="PROMOTED"` or `status='PROMOTED'`. The only file
    that should construct such a row is promote.py.
    """
    pattern = re.compile(r"""status=["']PROMOTED["']""")
    # promote.py is the only legitimate writer; tests/ may assert on PROMOTED
    # state; adversarial_drills.py contains the same regex pattern. Skip all.
    offenders: list[Path] = []
    for py in OPENCLAW_DIR.rglob("*.py"):
        if py.name in ("promote.py", "adversarial_drills.py"):
            continue
        if "tests" in py.parts:
            continue
        if pattern.search(py.read_text()):
            offenders.append(py)
    assert offenders == [], (
        f"PROMOTED status constructed outside promote.py: {offenders}"
    )


def test_databricks_worker_exposes_no_promotion_method():
    """DatabricksWorker is the agent-side surface. None of its public
    methods may suggest promotion capability.
    """
    from openclaw.databricks_worker import DatabricksWorker

    public_methods = [
        m for m in dir(DatabricksWorker) if not m.startswith("_")
    ]
    forbidden_substrings = ["promote", "publish", "approve"]
    for m in public_methods:
        for kw in forbidden_substrings:
            assert kw not in m.lower(), (
                f"DatabricksWorker.{m} suggests promotion capability"
            )


def test_decide_status_only_returns_draft_or_rejected_literals():
    """Inspect the bytecode of decide_status: every constant returned must
    be one of {'DRAFT', 'REJECTED'}. Stronger than docstring grep — looks
    at what the function can actually emit, ignoring documentation.
    """
    import dis
    from openclaw.fairness_scorer import decide_status

    return_constants: set[str] = set()
    for instr in dis.get_instructions(decide_status):
        # Capture string constants that are RETURN_VALUE'd or part of a
        # ternary's two branches (LOAD_CONST + RETURN_VALUE / IF_TRUE).
        if instr.opname == "LOAD_CONST" and isinstance(instr.argval, str):
            return_constants.add(instr.argval)

    assert return_constants <= {"DRAFT", "REJECTED"}, (
        f"decide_status loads unexpected string constants: {return_constants}"
    )
    assert "PROMOTED" not in return_constants


# =============================================================================
# Section B — invariant exhaustion
# =============================================================================

def test_decide_status_never_returns_promoted_for_any_input():
    """Generate scorecards spanning the threshold space; confirm the
    decision is always DRAFT or REJECTED, never PROMOTED.
    """
    from openclaw.fairness_scorer import FairnessScorecard, decide_status

    base = dict(
        scorecard_id="sc", brief_id="b", computed_at=NOW,
        source_tier_t1_pct=50.0, source_tier_t2_pct=20.0,
        source_tier_t3_pct=20.0, source_tier_t4_pct=10.0,
        vendor_diversity_count=3, counter_argument_ratio=0.20,
        recency_within_18mo_pct=80.0, paid_content_disclosed=True,
        bias_check_complete=True,
    )
    # Vary each metric around its threshold
    for pass_flag in (True, False):
        for failed in ([], ["recency"], ["vendor_diversity"],
                       ["source_tier_t1_t2_share", "bias_check_complete"]):
            sc = FairnessScorecard(
                **base, threshold_pass=pass_flag, failed_thresholds=failed,
            )
            status = decide_status(sc)
            assert status in ("DRAFT", "REJECTED")
            assert status != "PROMOTED"


# =============================================================================
# Section C — end-to-end attack scenarios via DatabricksWorker
# =============================================================================

class _FakeDelta:
    """In-memory Delta stand-in. Used across multiple tests below."""
    catalog = "research"

    def __init__(self):
        self.tables: dict[tuple[str, str], list[dict]] = {}

    def append(self, schema, table, rows):
        self.tables.setdefault((schema, table), []).extend(rows)

    def path_for(self, schema, table):
        return f"/tmp/fake/{schema}/{table}"


def _make_worker(tmp_path, fairness_scorer=None, mlflow_tracker=None):
    from openclaw.databricks_worker import DatabricksWorker, LocalBuffer
    fake = _FakeDelta()
    w = DatabricksWorker(
        delta_writer=fake,
        local_buffer=LocalBuffer(root=tmp_path),
        chain_writer=False,
        fairness_scorer=fairness_scorer,
        mlflow_tracker=mlflow_tracker,
    )
    return fake, w


def _brief(brief_id="b-adv-1"):
    from openclaw.databricks_worker import ValidatedBrief
    return ValidatedBrief(
        brief_id=brief_id, response_id="r-adv-1",
        created_at=NOW, topic_id="ai-safety",
        title="Adversarial test brief",
        key_findings="Findings " * 100,
        counter_arguments="Counter-arguments " * 30,
        practical_implications="Implications.",
        further_reading="Reading.",
        content_hash="a" * 64, agent_version="openclaw-v4.0",
    )


def _t4_citation(i):
    from openclaw.databricks_worker import Citation
    return Citation(
        citation_id=f"c-{i}", brief_id="b-adv-1",
        url=f"https://blog.example/{i}", title="t",
        authority_tier=4, vendor_org="OnlyVendor",
        publication_date=date(2024, 1, 1),  # well over 18 months
        is_paid_content=True,
        t4_justification="adversarial test",
    )


def test_full_pipeline_rejects_all_T4_citations(tmp_path):
    """End-to-end: a brief with all-T4 citations from a single vendor with
    old dates and missing bias check must land as REJECTED with multiple
    failures, and no public_archive write occurs."""
    from openclaw.fairness_scorer import FairnessScorer

    fake, w = _make_worker(tmp_path, fairness_scorer=FairnessScorer(now=NOW))
    citations = [_t4_citation(i) for i in range(3)]
    result = w.record_validated_brief(_brief(), citations, bias_check=None)

    assert result["status"] == "REJECTED"
    failures = set(result["scorecard"].failed_thresholds)
    # All four major thresholds must fail for this attack scenario
    assert "source_tier_t1_t2_share" in failures
    assert "vendor_diversity" in failures
    assert "recency" in failures
    assert "bias_check_complete" in failures

    # Critically: nothing in public_archive
    assert ("public_archive", "published") not in fake.tables

    # The REJECTED row IS in gold for human review
    summaries = fake.tables[("gold", "research_summaries")]
    assert len(summaries) == 1
    assert summaries[0]["status"] == "REJECTED"


def test_replay_same_brief_yields_two_distinct_summaries(tmp_path):
    """Writing the same brief twice produces two distinct rows in
    gold.research_summaries (different summary_id), no exception, no
    silent merge.
    """
    from openclaw.databricks_worker import Citation
    from openclaw.fairness_scorer import BiasChecklist, FairnessScorer

    fake, w = _make_worker(tmp_path, fairness_scorer=FairnessScorer(now=NOW))
    citations = [
        Citation(
            citation_id=f"c-{i}", brief_id="b-adv-1",
            url=f"https://example.com/{i}", title="t",
            authority_tier=1, vendor_org=v,
            publication_date=date(2026, 5, 1), is_paid_content=False,
        )
        for i, v in enumerate(["A", "B", "C"])
    ]
    bias = BiasChecklist(
        only_confirming_sources="No", competing_perspectives_included="Yes",
        limitations_acknowledged="Yes", financial_incentive_disclosed="Yes",
        publication_date_checked="Yes",
    )
    r1 = w.record_validated_brief(_brief(), citations, bias_check=bias)
    r2 = w.record_validated_brief(_brief(), citations, bias_check=bias)

    summaries = fake.tables[("gold", "research_summaries")]
    assert len(summaries) == 2
    assert summaries[0]["summary_id"] != summaries[1]["summary_id"]
    # Both should have valid (different) UUIDs
    assert r1["summary"].summary_id != r2["summary"].summary_id


# =============================================================================
# Section D — post-promotion tamper detection
# =============================================================================

def _seeded_promotable_world():
    """Build a complete world where one DRAFT brief sits ready for promotion."""
    from openclaw.databricks_worker import Citation, ValidatedBrief
    from openclaw.fairness_scorer import FairnessScorecard, ResearchSummary
    fake = _FakeDelta()

    brief = ValidatedBrief(
        brief_id="b-1", response_id="r-1", created_at=NOW,
        topic_id="ai-safety", title="Solid brief",
        key_findings="Findings " * 50,
        counter_arguments="Counter-arguments " * 30,
        practical_implications="Impl.",
        further_reading="Refs.",
        content_hash="a" * 64, agent_version="openclaw-v4.0",
    )
    fake.append("silver", "validated_briefs", [brief.model_dump()])
    for i, v in enumerate(["A", "B", "C"]):
        c = Citation(
            citation_id=f"c-{i:03d}", brief_id="b-1",
            url=f"https://example.com/{i}", title=f"S{i}",
            authority_tier=1, vendor_org=v,
            publication_date=date(2026, 5, 1), is_paid_content=False,
        )
        fake.append("silver", "citations", [c.model_dump()])
    sc = FairnessScorecard(
        scorecard_id="sc-1", brief_id="b-1", computed_at=NOW,
        source_tier_t1_pct=100.0, source_tier_t2_pct=0.0,
        source_tier_t3_pct=0.0, source_tier_t4_pct=0.0,
        vendor_diversity_count=3, counter_argument_ratio=0.30,
        recency_within_18mo_pct=100.0, paid_content_disclosed=True,
        bias_check_complete=True, threshold_pass=True, failed_thresholds=[],
    )
    fake.append("gold", "fairness_scorecards", [sc.model_dump()])
    summary = ResearchSummary(
        summary_id="sum-1", brief_id="b-1", created_at=NOW,
        status="DRAFT", mlflow_run_id="run-1",
    )
    fake.append("gold", "research_summaries", [summary.model_dump()])
    return fake


def test_published_markdown_content_hash_recomputable():
    """After promotion, recomputing SHA-256 of the markdown_export must
    match the stored content_hash. This is the property a downstream
    consumer relies on to verify the published artifact is intact.
    """
    from openclaw.databricks_worker import Hasher
    from openclaw.promote import Promoter
    from openclaw.tests.test_promote import FakeReader

    fake = _seeded_promotable_world()
    reader = FakeReader(fake)
    promoter = Promoter(delta=fake, reader=reader, now=NOW)
    result = promoter.approve("sum-1", reviewer="david")

    pub = result["published"]
    recomputed = Hasher.hash_string(pub.markdown_export)
    assert recomputed == pub.content_hash


def test_published_markdown_tamper_detectable_via_hash_recompute():
    """Mutate the markdown after promotion and confirm the recomputed
    hash differs from the stored content_hash.
    """
    from openclaw.databricks_worker import Hasher
    from openclaw.promote import Promoter
    from openclaw.tests.test_promote import FakeReader

    fake = _seeded_promotable_world()
    reader = FakeReader(fake)
    promoter = Promoter(delta=fake, reader=reader, now=NOW)
    result = promoter.approve("sum-1", reviewer="david")

    tampered = result["published"].markdown_export.replace(
        "Solid brief", "TAMPERED brief"
    )
    assert Hasher.hash_string(tampered) != result["published"].content_hash


def test_markdown_export_is_deterministic_across_calls():
    """Two promotions of the same data with the same published_id and
    timestamp produce byte-identical markdown.
    """
    from openclaw.databricks_worker import ValidatedBrief, Citation
    from openclaw.fairness_scorer import ResearchSummary
    from openclaw.promote import MarkdownExporter

    brief = ValidatedBrief(
        brief_id="b-1", response_id="r-1", created_at=NOW,
        topic_id="t", title="T", key_findings="kf",
        counter_arguments="x" * 100,
        practical_implications="pi", further_reading="fr",
        content_hash="a"*64, agent_version="v",
    )
    citations = [Citation(
        citation_id="c-1", brief_id="b-1", url="https://x", title="t",
        authority_tier=1, vendor_org="A", is_paid_content=False,
    )]
    summary = ResearchSummary(
        summary_id="sum-1", brief_id="b-1", created_at=NOW,
        status="PROMOTED", promoted_at=NOW, promoted_by="d", mlflow_run_id="r",
    )
    md1 = MarkdownExporter.render(
        brief=brief, citations=citations, scorecard=None,
        published_id="pub-fixed", summary=summary, reviewer="d",
    )
    md2 = MarkdownExporter.render(
        brief=brief, citations=citations, scorecard=None,
        published_id="pub-fixed", summary=summary, reviewer="d",
    )
    assert md1 == md2


# =============================================================================
# Section E — source integrity
# =============================================================================

def test_source_body_hash_consistent_for_same_bytes():
    from openclaw.databricks_worker import Hasher
    body = b"<html>Source page content here.</html>"
    h1 = Hasher.hash_bytes(body)
    h2 = Hasher.hash_bytes(body)
    assert h1 == h2


def test_source_body_change_produces_different_hash():
    """The audit story for source tampering: if the source URL changes
    its body after the brief is written, the body_hash recorded in
    bronze.source_fetches won't match a fresh re-fetch.
    """
    from openclaw.databricks_worker import Hasher
    original = b"<html>v1 content</html>"
    tampered = b"<html>v1 content (modified)</html>"
    assert Hasher.hash_bytes(original) != Hasher.hash_bytes(tampered)


# =============================================================================
# Section F — chain pipeline integration
# =============================================================================

def test_promotion_produces_three_chain_entries():
    """Promotion writes 3 rows: research_summaries (PROMOTED),
    public_archive.published, audit.promotion_log. Each must produce
    a chain entry tagged with the reviewer's identity.
    """
    from openclaw.promote import Promoter
    from openclaw.tests.test_promote import FakeReader

    chain_calls: list[dict] = []

    class FakeChain:
        def append_entry(self, **kwargs):
            chain_calls.append(kwargs)

    fake = _seeded_promotable_world()
    reader = FakeReader(fake)
    promoter = Promoter(
        delta=fake, reader=reader, chain_writer=FakeChain(), now=NOW,
    )
    promoter.approve("sum-1", reviewer="david")

    assert len(chain_calls) == 3
    target_tables = {c["target_table"] for c in chain_calls}
    assert target_tables == {
        "research.gold.research_summaries",
        "research.public_archive.published",
        "research.audit.promotion_log",
    }
    assert all(c["author_identity"] == "david" for c in chain_calls)
    assert all(c["operation"] == "INSERT" for c in chain_calls)


def test_chain_payload_hash_matches_canonical_of_written_row():
    """The target_payload_hash recorded in the chain must equal
    SHA-256(canonical_json(row)) — confirms the chain genuinely commits
    to the row's content, not a synthesized substitute.
    """
    from openclaw.databricks_worker import Hasher
    from openclaw.promote import Promoter
    from openclaw.tests.test_promote import FakeReader

    chain_calls: list[dict] = []

    class FakeChain:
        def append_entry(self, **kwargs):
            chain_calls.append(kwargs)

    fake = _seeded_promotable_world()
    reader = FakeReader(fake)
    promoter = Promoter(
        delta=fake, reader=reader, chain_writer=FakeChain(), now=NOW,
    )
    promoter.approve("sum-1", reviewer="david")

    # The promotion_log chain entry should hash the actual logged row.
    pl_call = next(c for c in chain_calls
                   if c["target_table"].endswith("promotion_log"))
    pl_row = fake.tables[("audit", "promotion_log")][0]
    expected = Hasher.hash_canonical(pl_row)
    assert pl_call["target_payload_hash"] == expected


# =============================================================================
# Section G — salt and signing edge cases
# =============================================================================

def test_salter_without_env_var_raises_clear_error(monkeypatch):
    from openclaw.integrity_engine import Salter, SaltUnavailableError
    monkeypatch.delenv("SECRET_SALT", raising=False)
    with pytest.raises(SaltUnavailableError) as exc:
        Salter()
    assert "SECRET_SALT" in str(exc.value)


def test_signer_with_wrong_salt_produces_different_signature():
    from openclaw.integrity_engine import Salter, Signer
    s1 = Signer(Salter(salt="0" * 64))
    s2 = Signer(Salter(salt="1" * 64))
    sig1 = s1.sign("a" * 64, "b" * 64)
    sig2 = s2.sign("a" * 64, "b" * 64)
    assert sig1 != sig2


# =============================================================================
# Section H — forged chain entry detection
# =============================================================================

def test_forged_chain_entry_with_wrong_prev_hash_detected():
    """An attacker who knows SECRET_SALT but lies about prev_hash to
    insert a forged entry produces a chain whose link to the prior row
    is broken; the auditor pinpoints the exact sequence_id of the lie.
    """
    from datetime import datetime, timezone
    import hashlib
    from openclaw.integrity_engine import (
        ChainEntry, ChainOperation, IntegrityAuditor, Salter, Signer,
    )

    class InMemStore:
        def __init__(self):
            self.entries = []

        def read_all_sorted(self):
            return sorted(self.entries, key=lambda e: e.sequence_id)

        def read_latest(self):
            return self.read_all_sorted()[-1] if self.entries else None

    salter = Salter(salt=TEST_SALT)
    signer = Signer(salter)
    store = InMemStore()

    # Build a 5-entry valid chain
    prev = "0" * 64
    for seq in range(5):
        op = ChainOperation.GENESIS if seq == 0 else ChainOperation.INSERT
        target_table = None if seq == 0 else f"research.bronze.t_{seq}"
        target_row = None if seq == 0 else f"row-{seq}"
        ph = hashlib.sha256(f"payload-{seq}".encode()).hexdigest()
        sig = signer.sign(ph, prev, 1)
        store.entries.append(ChainEntry(
            sequence_id=seq,
            recorded_at=datetime(2026, 5, 8, 12, 0, seq, tzinfo=timezone.utc),
            author_identity="agent",
            operation=op,
            target_table=target_table,
            target_row_id=target_row,
            target_payload_hash=ph,
            previous_hash=prev,
            row_signature=sig,
            salt_version=1,
        ))
        prev = sig

    # Now forge an entry at sequence_id=5 that lies about previous_hash:
    # claims the predecessor was the genesis sentinel rather than the actual
    # entry-4 signature.
    forged_payload_hash = hashlib.sha256(b"forged").hexdigest()
    forged_prev = "0" * 64  # the lie
    forged_sig = signer.sign(forged_payload_hash, forged_prev, 1)
    store.entries.append(ChainEntry(
        sequence_id=5,
        recorded_at=datetime(2026, 5, 8, 13, 0, 0, tzinfo=timezone.utc),
        author_identity="adversary",
        operation=ChainOperation.INSERT,
        target_table="research.bronze.forged",
        target_row_id="forged-1",
        target_payload_hash=forged_payload_hash,
        previous_hash=forged_prev,
        row_signature=forged_sig,
        salt_version=1,
    ))

    verdict = IntegrityAuditor(store, signer).audit()
    assert not verdict.is_intact
    assert verdict.first_tampered_sequence_id == 5
    assert "previous_hash" in verdict.failure_reason


# =============================================================================
# Section I — Pydantic-enforced invariants
# =============================================================================

def test_validated_brief_rejects_short_counter_arguments():
    """Phase 7's Pydantic model enforces the silver CHECK constraint
    (counter_arguments length >= 100). An adversarial brief with empty
    counter-args is rejected at the type-system layer, before any write.
    """
    from openclaw.databricks_worker import ValidatedBrief
    from datetime import datetime, timezone
    with pytest.raises(Exception):
        ValidatedBrief(
            brief_id="b", response_id="r",
            created_at=datetime.now(timezone.utc),
            topic_id="t", title="x", key_findings="y",
            counter_arguments="too short",  # < 100 chars
            practical_implications="z", further_reading="w",
            content_hash="h", agent_version="v",
        )


def test_t4_citation_without_justification_rejected_at_construction():
    """Phase 7's Pydantic model rejects a T4 citation without a
    justification — the agent can't slip a low-quality source through
    by simply marking it tier 4 and hoping no one looks.
    """
    from openclaw.databricks_worker import Citation
    with pytest.raises(Exception):
        Citation(
            citation_id="c", brief_id="b", url="https://blog.example", title="t",
            authority_tier=4, is_paid_content=False,
        )
    # With justification, allowed (the human reviews the justification).
    Citation(
        citation_id="c", brief_id="b", url="https://blog.example", title="t",
        authority_tier=4, is_paid_content=False,
        t4_justification="primary author of the spec; no peer-reviewed alternative",
    )


def test_promoted_status_unreachable_via_decide_status():
    """The decision function exposed by Phase 9 to determine status
    cannot return PROMOTED no matter what scorecard is fed to it.
    """
    from openclaw.fairness_scorer import FairnessScorecard, decide_status
    sc = FairnessScorecard(
        scorecard_id="x", brief_id="b", computed_at=NOW,
        source_tier_t1_pct=100.0, source_tier_t2_pct=0.0,
        source_tier_t3_pct=0.0, source_tier_t4_pct=0.0,
        vendor_diversity_count=10,
        counter_argument_ratio=0.5,
        recency_within_18mo_pct=100.0,
        paid_content_disclosed=True, bias_check_complete=True,
        threshold_pass=True, failed_thresholds=[],
    )
    # Even a perfect scorecard yields DRAFT, never PROMOTED.
    assert decide_status(sc) == "DRAFT"
