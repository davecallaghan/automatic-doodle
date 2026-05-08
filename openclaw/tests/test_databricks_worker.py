"""Unit tests for databricks_worker (Phase 7).

Tests the parts that don't require deltalake/pyarrow to be installed —
canonical JSON, hashing, local buffer, Pydantic validation. The Delta-write
path is exercised on the VM during the Phase 7 deployment runbook.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest


# -----------------------------------------------------------------------------
# CanonicalJSON
# -----------------------------------------------------------------------------

def test_canonical_json_key_order_independent():
    from openclaw.databricks_worker import CanonicalJSON
    a = CanonicalJSON.encode({"b": 2, "a": 1})
    b = CanonicalJSON.encode({"a": 1, "b": 2})
    assert a == b == '{"a":1,"b":2}'


def test_canonical_json_datetime_utc_iso():
    from openclaw.databricks_worker import CanonicalJSON
    dt = datetime(2026, 5, 7, 14, 30, 45, 123456, tzinfo=timezone.utc)
    encoded = CanonicalJSON.encode({"t": dt})
    assert encoded == '{"t":"2026-05-07T14:30:45.123456Z"}'


def test_canonical_json_date_iso():
    from openclaw.databricks_worker import CanonicalJSON
    encoded = CanonicalJSON.encode({"d": date(2026, 5, 7)})
    assert encoded == '{"d":"2026-05-07"}'


def test_canonical_json_no_whitespace():
    from openclaw.databricks_worker import CanonicalJSON
    encoded = CanonicalJSON.encode({"a": 1, "nested": {"x": 2}})
    assert " " not in encoded
    assert "\n" not in encoded


# -----------------------------------------------------------------------------
# Hasher — known SHA-256 vectors
# -----------------------------------------------------------------------------

def test_hasher_empty_string():
    from openclaw.databricks_worker import Hasher
    # SHA-256("")
    assert Hasher.hash_string("") == \
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_hasher_known_vector():
    from openclaw.databricks_worker import Hasher
    # SHA-256("abc")
    assert Hasher.hash_string("abc") == \
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_hasher_canonical_consistency():
    from openclaw.databricks_worker import Hasher
    h1 = Hasher.hash_canonical({"b": 2, "a": 1})
    h2 = Hasher.hash_canonical({"a": 1, "b": 2})
    assert h1 == h2


def test_hasher_file_streaming(tmp_path: Path):
    from openclaw.databricks_worker import Hasher
    p = tmp_path / "x.bin"
    p.write_bytes(b"hello world")
    # SHA-256("hello world")
    assert Hasher.hash_file_streaming(p) == \
        "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"


# -----------------------------------------------------------------------------
# LocalBuffer
# -----------------------------------------------------------------------------

def test_local_buffer_round_trip(tmp_path: Path):
    from openclaw.databricks_worker import LocalBuffer
    buf = LocalBuffer(root=tmp_path)
    buf.append("bronze.raw_responses", {"response_id": "abc", "v": 1})
    buf.append("bronze.raw_responses", {"response_id": "def", "v": 2})

    pending = list(buf.iter_pending())
    assert len(pending) == 2
    assert {p[2]["response_id"] for p in pending} == {"abc", "def"}
    assert all(p[1] == "bronze.raw_responses" for p in pending)


def test_local_buffer_separate_tables(tmp_path: Path):
    from openclaw.databricks_worker import LocalBuffer
    buf = LocalBuffer(root=tmp_path)
    buf.append("bronze.raw_responses", {"id": "r1"})
    buf.append("silver.validated_briefs", {"id": "b1"})

    tables = {p[1] for p in buf.iter_pending()}
    assert tables == {"bronze.raw_responses", "silver.validated_briefs"}


# -----------------------------------------------------------------------------
# Pydantic models
# -----------------------------------------------------------------------------

def test_citation_t4_requires_justification():
    from openclaw.databricks_worker import Citation
    # Tier 1 — no justification needed
    Citation(
        citation_id="c1", brief_id="b1", url="https://x", title="t",
        authority_tier=1, is_paid_content=False,
    )
    # Tier 4 with no justification — must fail
    with pytest.raises(Exception):
        Citation(
            citation_id="c2", brief_id="b1", url="https://x", title="t",
            authority_tier=4, is_paid_content=False,
        )
    # Tier 4 with justification — ok
    Citation(
        citation_id="c3", brief_id="b1", url="https://x", title="t",
        authority_tier=4, is_paid_content=False,
        t4_justification="primary author of the spec",
    )


def test_citation_tier_range():
    from openclaw.databricks_worker import Citation
    with pytest.raises(Exception):
        Citation(
            citation_id="c", brief_id="b", url="https://x", title="t",
            authority_tier=0, is_paid_content=False,
        )
    with pytest.raises(Exception):
        Citation(
            citation_id="c", brief_id="b", url="https://x", title="t",
            authority_tier=5, is_paid_content=False,
        )


def test_validated_brief_counter_arg_min_length():
    from openclaw.databricks_worker import ValidatedBrief
    base = dict(
        brief_id="b1", response_id="r1",
        created_at=datetime.now(timezone.utc),
        topic_id="t1", title="x", key_findings="y",
        practical_implications="z", further_reading="w",
        content_hash="h", agent_version="v",
    )
    # Too short — must fail
    with pytest.raises(Exception):
        ValidatedBrief(**base, counter_arguments="too short")
    # Long enough — ok
    ValidatedBrief(**base, counter_arguments="x" * 100)


# -----------------------------------------------------------------------------
# Worker chain hook (stub)
# -----------------------------------------------------------------------------

def test_chain_hook_stub_logs_to_stderr(capsys, tmp_path):
    """When no chain_writer is wired (Phase 7 / Phase 8 with no SECRET_SALT),
    the hook logs to stderr instead of writing to the chain table. The
    Phase 8 wired path is tested in test_integrity_engine.py.
    """
    from openclaw.databricks_worker import DatabricksWorker
    # Force stub mode so the test doesn't accidentally pick up a real
    # ChainWriter from a SECRET_SALT in the environment.
    w = DatabricksWorker(chain_writer=False)
    w._chain_hook("INSERT", "research.bronze.raw_responses", "row-1", "abc123")
    captured = capsys.readouterr()
    assert "CHAIN_HOOK" in captured.err
    assert "operation=INSERT" in captured.err
    assert "row_id=row-1" in captured.err
    assert "payload_hash=abc123" in captured.err


class _FakeDelta:
    """A complete-enough stand-in for DeltaTableWriter — satisfies all the
    attribute access the worker does after a successful append.
    """
    catalog = "research"

    def __init__(self):
        self.writes: list[tuple[str, str, list[dict]]] = []

    def append(self, schema, table, rows):
        self.writes.append((schema, table, rows))

    def path_for(self, schema, table):
        return f"/tmp/fake-delta/{schema}/{table}"


def test_record_validated_brief_phase7_only_writes_silver(tmp_path):
    """Phase 7 contract: with no fairness_scorer, only silver tables are written."""
    from openclaw.databricks_worker import (
        Citation, DatabricksWorker, LocalBuffer, ValidatedBrief,
    )
    from datetime import datetime, timezone

    fake = _FakeDelta()
    w = DatabricksWorker(
        delta_writer=fake,
        local_buffer=LocalBuffer(root=tmp_path),
        chain_writer=False,
        fairness_scorer=None,
        mlflow_tracker=None,
    )
    brief = ValidatedBrief(
        brief_id="b-1", response_id="r-1",
        created_at=datetime.now(timezone.utc),
        topic_id="t", title="x", key_findings="y",
        counter_arguments="x" * 100,
        practical_implications="z", further_reading="w",
        content_hash="h", agent_version="v",
    )
    citation = Citation(
        citation_id="c-1", brief_id="b-1", url="https://x", title="t",
        authority_tier=1, vendor_org="A", is_paid_content=False,
    )
    result = w.record_validated_brief(brief, [citation])

    assert result == {}
    written_tables = [(s, t) for s, t, _ in fake.writes]
    assert ("silver", "validated_briefs") in written_tables
    assert ("silver", "citations") in written_tables
    assert not any(s == "gold" for s, _ in written_tables)


def test_record_validated_brief_phase9_writes_gold(tmp_path):
    """With fairness_scorer wired, gold tables are also written and a status returned."""
    from openclaw.databricks_worker import (
        Citation, DatabricksWorker, LocalBuffer, ValidatedBrief,
    )
    from openclaw.fairness_scorer import BiasChecklist, FairnessScorer
    from datetime import datetime, timezone

    fake = _FakeDelta()
    w = DatabricksWorker(
        delta_writer=fake,
        local_buffer=LocalBuffer(root=tmp_path),
        chain_writer=False,
        fairness_scorer=FairnessScorer(now=datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)),
        mlflow_tracker=None,
    )
    brief = ValidatedBrief(
        brief_id="b-1", response_id="r-1",
        created_at=datetime.now(timezone.utc),
        topic_id="t", title="x", key_findings="y",
        counter_arguments=" ".join(["counter"] * 30),
        practical_implications="z", further_reading="w",
        content_hash="h", agent_version="v",
    )
    citations = [
        Citation(
            citation_id=f"c-{i}", brief_id="b-1", url=f"https://x/{i}", title="t",
            authority_tier=1, vendor_org=v, is_paid_content=False,
            publication_date=datetime(2026, 5, 7).date(),
        )
        for i, v in enumerate(["A", "B", "C"])
    ]
    bias = BiasChecklist(
        only_confirming_sources="No", competing_perspectives_included="Yes",
        limitations_acknowledged="Yes", financial_incentive_disclosed="Yes",
        publication_date_checked="Yes",
    )

    result = w.record_validated_brief(brief, citations, bias_check=bias)

    assert "scorecard" in result
    assert result["status"] in ("DRAFT", "REJECTED")
    written_tables = {(s, t) for s, t, _ in fake.writes}
    assert ("gold", "fairness_scorecards") in written_tables
    assert ("gold", "research_summaries") in written_tables


def test_chain_hook_calls_writer_when_wired(monkeypatch):
    """When chain_writer is provided, the hook delegates to it instead of
    logging to stderr.
    """
    from openclaw.databricks_worker import DatabricksWorker

    calls: list[dict] = []

    class FakeWriter:
        def append_entry(self, **kwargs):
            calls.append(kwargs)
            return None

    w = DatabricksWorker(chain_writer=FakeWriter())
    w._chain_hook("INSERT", "research.bronze.raw_responses", "row-1", "abc123")

    assert len(calls) == 1
    assert calls[0]["operation"] == "INSERT"
    assert calls[0]["target_table"] == "research.bronze.raw_responses"
    assert calls[0]["target_row_id"] == "row-1"
    assert calls[0]["target_payload_hash"] == "abc123"
