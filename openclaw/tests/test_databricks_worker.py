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

def test_chain_hook_logs_to_stderr(capsys, tmp_path):
    """Phase 7's chain hook is a stub; verify it emits the expected log line.
    Phase 8 replaces this with a real chain write — that test will live in
    test_integrity_engine.py.
    """
    from openclaw.databricks_worker import DatabricksWorker
    w = DatabricksWorker()
    w._chain_hook("INSERT", "research.bronze.raw_responses", "row-1", "abc123")
    captured = capsys.readouterr()
    assert "CHAIN_HOOK" in captured.err
    assert "operation=INSERT" in captured.err
    assert "row_id=row-1" in captured.err
    assert "payload_hash=abc123" in captured.err
