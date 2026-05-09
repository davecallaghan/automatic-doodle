"""Unit tests for commons_publisher (Phase 12).

Pure-Python — exercises the rollup math, brief export idempotency, seal
log append, and retraction-commit parser without any Delta or MLflow
dependency.
"""

from __future__ import annotations

import textwrap
from datetime import date, datetime, timezone
from pathlib import Path

import pytest


D1 = date(2026, 5, 7)
D2 = date(2026, 5, 8)
D_OUTSIDE = date(2026, 5, 6)


# -----------------------------------------------------------------------------
# Rollup math
# -----------------------------------------------------------------------------

def _run(rid="r1", started_at=None, model="google/gemini-2.5-flash",
         in_tok=1000, out_tok=200, cost=0.001):
    return {
        "run_summary_id": f"rs-{rid}", "mlflow_run_id": rid,
        "brief_id": "b1",
        "started_at": started_at or datetime(D1.year, D1.month, D1.day, 12, 0, tzinfo=timezone.utc),
        "ended_at": started_at or datetime(D1.year, D1.month, D1.day, 12, 5, tzinfo=timezone.utc),
        "model_id": model, "prompt_version": "v1", "agent_version": "v4",
        "input_tokens": in_tok, "output_tokens": out_tok,
        "total_cost_usd": cost,
        "source_count": 3, "unique_domain_count": 3,
        "latency_seconds": 5.0, "retry_count": 0,
    }


def _summary(sid="s1", status="DRAFT", created_at=None):
    return {
        "summary_id": sid, "brief_id": "b1",
        "created_at": created_at or datetime(D1.year, D1.month, D1.day, 12, 30, tzinfo=timezone.utc),
        "status": status,
        "rejection_reasons": None, "override_reason": None,
        "promoted_at": None, "promoted_by": None,
        "mlflow_run_id": "r1",
    }


def _fetch(fid="f1", fetched_at=None, used=True):
    return {
        "fetch_id": fid, "response_id": "resp1",
        "fetched_at": fetched_at or datetime(D1.year, D1.month, D1.day, 11, 0, tzinfo=timezone.utc),
        "url": f"https://example.com/{fid}",
        "http_status": 200, "body_hash": "a" * 64,
        "body_bytes": 1000, "content_type": "text/html",
        "used_in_brief": used,
    }


def test_rollup_aggregates_tokens_and_cost():
    from openclaw.commons_publisher import compute_rollup
    runs = [
        _run("r1", in_tok=1000, out_tok=200, cost=0.001),
        _run("r2", in_tok=500, out_tok=100, cost=0.0005),
    ]
    entry = compute_rollup(D1, D1, runs, [], [])
    assert entry.total_input_tokens == 1500
    assert entry.total_output_tokens == 300
    assert abs(entry.estimated_cost_usd - 0.0015) < 1e-9
    assert entry.total_model_calls == 2


def test_rollup_excludes_data_outside_period():
    from openclaw.commons_publisher import compute_rollup
    runs = [
        _run("r1", started_at=datetime(D1.year, D1.month, D1.day, 12, tzinfo=timezone.utc)),
        _run("r2", started_at=datetime(D_OUTSIDE.year, D_OUTSIDE.month, D_OUTSIDE.day, 12, tzinfo=timezone.utc)),
    ]
    entry = compute_rollup(D1, D1, runs, [], [])
    assert entry.total_model_calls == 1


def test_rollup_counts_summary_statuses():
    from openclaw.commons_publisher import compute_rollup
    summaries = [
        _summary("s1", "DRAFT"),
        _summary("s2", "REJECTED"),
        _summary("s3", "REJECTED"),
        _summary("s4", "PROMOTED"),
        _summary("s5", "PROMOTED"),
        _summary("s6", "PROMOTED"),
    ]
    entry = compute_rollup(D1, D1, [], summaries, [])
    assert entry.briefs_drafted == 1
    assert entry.briefs_rejected == 2
    assert entry.briefs_published == 3


def test_rollup_give_back_ratio():
    from openclaw.commons_publisher import compute_rollup
    runs = [_run(in_tok=1000, out_tok=200, cost=0.10)]
    summaries = [_summary("s1", "PROMOTED"), _summary("s2", "PROMOTED")]
    entry = compute_rollup(D1, D1, runs, summaries, [])
    assert entry.estimated_cost_usd == pytest.approx(0.10)
    assert entry.give_back_ratio == pytest.approx(20.0)  # 2 published / $0.10


def test_rollup_zero_cost_yields_zero_ratio():
    """Avoid div-by-zero; a no-spend day gives a zero ratio rather than NaN."""
    from openclaw.commons_publisher import compute_rollup
    entry = compute_rollup(D1, D1, [], [_summary("s1", "PROMOTED")], [])
    assert entry.estimated_cost_usd == 0.0
    assert entry.give_back_ratio == 0.0


def test_rollup_models_used_is_sorted_unique():
    from openclaw.commons_publisher import compute_rollup
    runs = [
        _run("r1", model="google/gemini-2.5-flash"),
        _run("r2", model="anthropic/claude-haiku-4.5"),
        _run("r3", model="google/gemini-2.5-flash"),
    ]
    entry = compute_rollup(D1, D1, runs, [], [])
    assert entry.models_used == [
        "anthropic/claude-haiku-4.5", "google/gemini-2.5-flash"
    ]


def test_rollup_counts_distinct_runs():
    """Multiple run_summaries with the same mlflow_run_id count as 1 call."""
    from openclaw.commons_publisher import compute_rollup
    runs = [_run("r1"), _run("r1"), _run("r2")]
    entry = compute_rollup(D1, D1, runs, [], [])
    assert entry.total_model_calls == 2


def test_rollup_source_fetches_filtered():
    from openclaw.commons_publisher import compute_rollup
    fetches = [
        _fetch("f1"),
        _fetch("f2"),
        _fetch("f3", fetched_at=datetime(D_OUTSIDE.year, D_OUTSIDE.month, D_OUTSIDE.day, 12, tzinfo=timezone.utc)),
    ]
    entry = compute_rollup(D1, D1, [], [], fetches)
    assert entry.total_source_fetches == 2


def test_rollup_period_spans_multiple_days():
    """A multi-day period aggregates across all of them."""
    from openclaw.commons_publisher import compute_rollup
    runs = [
        _run("r1", started_at=datetime(D1.year, D1.month, D1.day, 12, tzinfo=timezone.utc)),
        _run("r2", started_at=datetime(D2.year, D2.month, D2.day, 12, tzinfo=timezone.utc)),
    ]
    entry = compute_rollup(D1, D2, runs, [], [])
    assert entry.total_model_calls == 2


def test_rollup_includes_human_review_hours():
    from openclaw.commons_publisher import compute_rollup
    entry = compute_rollup(D1, D1, [], [], [], human_review_hours=2.5)
    assert entry.human_review_hours == 2.5


# -----------------------------------------------------------------------------
# Render ledger markdown
# -----------------------------------------------------------------------------

def test_ledger_markdown_contains_headline_metrics():
    from openclaw.commons_publisher import compute_rollup, render_ledger_markdown
    runs = [_run(cost=0.5)]
    summaries = [_summary("s1", "PROMOTED"), _summary("s2", "DRAFT")]
    entry = compute_rollup(D1, D1, runs, summaries, [])
    md = render_ledger_markdown(entry)
    assert "Give-back ratio" in md
    assert "Briefs published" in md
    assert "$0.5000" in md or "$0.5" in md


# -----------------------------------------------------------------------------
# Brief export idempotency
# -----------------------------------------------------------------------------

def _published_row(pid="pub-001", at=None, content_hash=None, markdown="# B\n\nbody"):
    from openclaw.databricks_worker import Hasher
    return {
        "published_id": pid,
        "summary_id": "s1",
        "published_at": at or datetime(2026, 5, 7, 22, 0, tzinfo=timezone.utc),
        "license": "CC BY 4.0",
        "attribution": "OpenClaw Project",
        "markdown_export": markdown,
        "content_hash": content_hash or Hasher.hash_string(markdown),
    }


def test_export_briefs_writes_files(tmp_path: Path):
    from openclaw.commons_publisher import export_briefs
    rows = [_published_row("pub-001"), _published_row("pub-002")]
    written = export_briefs(rows, tmp_path)
    assert len(written) == 2
    assert (tmp_path / "briefs" / "2026-05" / "pub-001.md").exists()
    assert (tmp_path / "briefs" / "2026-05" / "pub-002.md").exists()


def test_export_briefs_idempotent(tmp_path: Path):
    """Re-running export with no changes writes nothing."""
    from openclaw.commons_publisher import export_briefs
    rows = [_published_row("pub-001")]
    export_briefs(rows, tmp_path)
    second = export_briefs(rows, tmp_path)
    assert second == []  # nothing rewritten


def test_export_briefs_overwrites_when_content_changes(tmp_path: Path):
    """If a row's content_hash differs from the file's hash, rewrite it."""
    from openclaw.commons_publisher import export_briefs
    from openclaw.databricks_worker import Hasher
    initial = _published_row("pub-001", markdown="# v1\n")
    export_briefs([initial], tmp_path)

    new_md = "# v2\nrevised body\n"
    updated = _published_row("pub-001", markdown=new_md, content_hash=Hasher.hash_string(new_md))
    written = export_briefs([updated], tmp_path)
    assert len(written) == 1
    file_path = tmp_path / "briefs" / "2026-05" / "pub-001.md"
    assert file_path.read_text() == new_md


def test_export_briefs_groups_by_year_month(tmp_path: Path):
    from openclaw.commons_publisher import export_briefs
    rows = [
        _published_row("pub-001", at=datetime(2026, 5, 7, tzinfo=timezone.utc)),
        _published_row("pub-002", at=datetime(2026, 6, 1, tzinfo=timezone.utc)),
    ]
    export_briefs(rows, tmp_path)
    assert (tmp_path / "briefs" / "2026-05" / "pub-001.md").exists()
    assert (tmp_path / "briefs" / "2026-06" / "pub-002.md").exists()


# -----------------------------------------------------------------------------
# Seal publication
# -----------------------------------------------------------------------------

def _checkpoint(seq, recorded_at, signature, payload_hash, salt_v=1):
    return {
        "sequence_id": seq, "recorded_at": recorded_at,
        "author_identity": "system", "operation": "CHECKPOINT",
        "target_table": None, "target_row_id": None,
        "target_payload_hash": payload_hash,
        "previous_hash": "0" * 64,
        "row_signature": signature,
        "salt_version": salt_v,
    }


def test_format_seal_line_structure():
    from openclaw.commons_publisher import format_seal_line
    cp = _checkpoint(
        seq=42,
        recorded_at=datetime(2026, 5, 14, 16, 55, tzinfo=timezone.utc),
        signature="b" * 64,
        payload_hash="a" * 64,
    )
    line = format_seal_line(cp)
    assert line.startswith("2026-05-14")
    assert "seq=42" in line
    assert f"merkle={'a' * 64}" in line
    assert f"seal={'b' * 64}" in line
    assert "salt_v=1" in line
    assert line.endswith("\n")


def test_append_seals_writes_new_lines(tmp_path: Path):
    from openclaw.commons_publisher import append_seals
    cps = [
        _checkpoint(10, datetime(2026, 5, 13, 16, 55, tzinfo=timezone.utc), "1" * 64, "0" * 64),
        _checkpoint(20, datetime(2026, 5, 14, 16, 55, tzinfo=timezone.utc), "2" * 64, "f" * 64),
    ]
    seal_log = tmp_path / "SEALS.md"
    n = append_seals(cps, seal_log)
    assert n == 2
    content = seal_log.read_text()
    assert "seq=10" in content
    assert "seq=20" in content


def test_append_seals_idempotent(tmp_path: Path):
    from openclaw.commons_publisher import append_seals
    cps = [_checkpoint(10, datetime(2026, 5, 13, 16, 55, tzinfo=timezone.utc), "a" * 64, "f" * 64)]
    seal_log = tmp_path / "SEALS.md"
    append_seals(cps, seal_log)
    n2 = append_seals(cps, seal_log)
    assert n2 == 0
    assert seal_log.read_text().count("seq=10") == 1


def test_append_seals_skips_non_checkpoints(tmp_path: Path):
    """INSERT entries that share the chain table must NOT be written to SEALS."""
    from openclaw.commons_publisher import append_seals
    insert_entry = {
        "sequence_id": 5,
        "recorded_at": datetime(2026, 5, 13, 16, 55, tzinfo=timezone.utc),
        "operation": "INSERT",
        "target_payload_hash": "a" * 64,
        "row_signature": "b" * 64,
        "salt_version": 1,
    }
    seal_log = tmp_path / "SEALS.md"
    n = append_seals([insert_entry], seal_log)
    assert n == 0
    assert not seal_log.exists()


# -----------------------------------------------------------------------------
# Retraction parser
# -----------------------------------------------------------------------------

def test_parse_retraction_commits_basic():
    from openclaw.commons_publisher import parse_retraction_commits
    log = textwrap.dedent("""
        sha=abc123
        author=david@example.com
        date=2026-05-15T12:00:00Z
        subject=retraction: pub-3a7c — pricing claim outdated
        body=Brief at briefs/2026-05/pub-3a7c.md cited e2-medium at $0.033.
        body=Correct: $0.034.
        body=superseded-by: pub-7e2f
    """).strip().splitlines()
    out = parse_retraction_commits(log)
    assert len(out) == 1
    r = out[0]
    assert r.published_id == "pub-3a7c"
    assert r.retraction_reason == "pricing claim outdated"
    assert r.superseded_by == "pub-7e2f"
    assert r.git_commit_sha == "abc123"
    assert r.retracted_by == "david@example.com"


def test_parse_retraction_skips_non_retraction_commits():
    from openclaw.commons_publisher import parse_retraction_commits
    log = textwrap.dedent("""
        sha=def456
        author=david@example.com
        date=2026-05-15T12:00:00Z
        subject=docs: update README
        body=routine update
    """).strip().splitlines()
    out = parse_retraction_commits(log)
    assert out == []


def test_parse_multiple_retractions():
    from openclaw.commons_publisher import parse_retraction_commits
    log = textwrap.dedent("""
        sha=aaa
        author=david@example.com
        date=2026-05-15T12:00:00Z
        subject=retraction: pub-001 — first reason
        body=details

        sha=bbb
        author=david@example.com
        date=2026-05-16T12:00:00Z
        subject=retraction: pub-002 — second reason
        body=details
    """).strip().splitlines()
    out = parse_retraction_commits(log)
    assert len(out) == 2
    assert {r.published_id for r in out} == {"pub-001", "pub-002"}


# -----------------------------------------------------------------------------
# Pydantic validation
# -----------------------------------------------------------------------------

def test_ledger_entry_rejects_negative_values():
    from openclaw.commons_publisher import AiUsageLedgerEntry
    with pytest.raises(Exception):
        AiUsageLedgerEntry(
            ledger_id="l", period_start=D1, period_end=D1,
            total_input_tokens=-1,  # negative
            total_output_tokens=0, total_model_calls=0,
            total_source_fetches=0, estimated_cost_usd=0.0,
            models_used=[], briefs_published=0, briefs_drafted=0,
            briefs_rejected=0, give_back_ratio=0.0,
        )
