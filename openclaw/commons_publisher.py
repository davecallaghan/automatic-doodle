#!/usr/bin/env python3
"""
commons_publisher.py — Phase 12 public commons exporter.

Three responsibilities, each as a subcommand:

  rollup-ledger    Compute a daily AI-usage rollup from gold.run_summaries
                   + gold.research_summaries + bronze.source_fetches; write
                   the result to research.shared.ai_usage_ledger. The
                   `give_back_ratio` (briefs_published / estimated_cost_usd)
                   is the proportionality argument's evidence.

  export-briefs    Render newly-promoted briefs as markdown files in
                   <commons_dir>/briefs/<YYYY-MM>/<published_id>.md.
                   Idempotent: skips files that already match their
                   content_hash.

  publish-seal     Append the latest CHECKPOINT entries to
                   <commons_dir>/SEALS.md so external observers can verify
                   the integrity chain without access to SECRET_SALT.

  publish-all      Run all three idempotently; safe for nightly cron.

Phase 12 v1 writes to a LOCAL export directory. Pushing the output to a
public GitHub repo is a separate `git add/commit/push` step; the runbook
documents how to wire that as a follow-up.

Math is split into pure functions so unit tests don't need MLflow or
delta-rs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

# Allow `python3 ~/openclaw/commons_publisher.py ...` from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel, Field

from openclaw.databricks_worker import (
    CATALOG, DeltaTableWriter, Hasher, _DELTA_AVAILABLE,
)

if _DELTA_AVAILABLE:
    from deltalake import DeltaTable

DEFAULT_COMMONS_DIR = Path(os.environ.get(
    "COMMONS_DIR", "/mnt/disks/research/public_commons_export"
))
DEFAULT_LICENSE = "CC BY 4.0"


# -----------------------------------------------------------------------------
# Pydantic models — research.shared.ai_usage_ledger and retractions
# -----------------------------------------------------------------------------

class AiUsageLedgerEntry(BaseModel):
    """research.shared.ai_usage_ledger — one row per day or arbitrary period.
    `give_back_ratio` is the headline metric for the proportionality story.
    """

    ledger_id: str
    period_start: date
    period_end: date
    total_input_tokens: int = Field(ge=0)
    total_output_tokens: int = Field(ge=0)
    total_model_calls: int = Field(ge=0)
    total_source_fetches: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    models_used: list[str]
    briefs_published: int = Field(ge=0)
    briefs_drafted: int = Field(ge=0)
    briefs_rejected: int = Field(ge=0)
    human_review_hours: float | None = None
    give_back_ratio: float = Field(ge=0)


class Retraction(BaseModel):
    """research.shared.retractions — mirror of git retraction commits."""

    retraction_id: str
    published_id: str
    retracted_at: datetime
    retraction_reason: str
    retracted_by: str
    superseded_by: str | None = None
    git_commit_sha: str


# -----------------------------------------------------------------------------
# Pure functions — testable without MLflow or delta-rs
# -----------------------------------------------------------------------------

def _to_date(value: Any) -> date:
    """Coerce datetime / date / ISO-string to date."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date() if value.tzinfo else value.date()
    if isinstance(value, str):
        # Try datetime first, fall back to date
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return date.fromisoformat(value)
    raise TypeError(f"cannot coerce {type(value).__name__} to date")


def compute_rollup(
    period_start: date,
    period_end: date,
    run_summaries: list[dict[str, Any]],
    research_summaries: list[dict[str, Any]],
    source_fetches: list[dict[str, Any]],
    human_review_hours: float | None = None,
) -> AiUsageLedgerEntry:
    """Aggregate operational metrics over [period_start, period_end] inclusive.

    A brief that transitions DRAFT → PROMOTED inside the period contributes
    to BOTH `briefs_drafted` and `briefs_published` — the ledger tracks
    state transitions, not unique briefs. This is the honest framing: AI
    consumption that produced rejected/unpromoted output is in the
    denominator just as cost-equivalent published output is in the numerator.
    """

    def _in_period(field: str, row: dict[str, Any]) -> bool:
        d = _to_date(row[field])
        return period_start <= d <= period_end

    runs_in = [r for r in run_summaries if _in_period("started_at", r)]
    fetches_in = [r for r in source_fetches if _in_period("fetched_at", r)]
    summaries_in = [r for r in research_summaries if _in_period("created_at", r)]

    total_input = sum(int(r["input_tokens"]) for r in runs_in)
    total_output = sum(int(r["output_tokens"]) for r in runs_in)
    total_cost = sum(float(r["total_cost_usd"]) for r in runs_in)
    distinct_runs = {r["mlflow_run_id"] for r in runs_in if r.get("mlflow_run_id")}
    models_used = sorted({r["model_id"] for r in runs_in if r.get("model_id")})

    drafted = sum(1 for s in summaries_in if s["status"] == "DRAFT")
    rejected = sum(1 for s in summaries_in if s["status"] == "REJECTED")
    published = sum(1 for s in summaries_in if s["status"] == "PROMOTED")

    give_back = (published / total_cost) if total_cost > 0 else 0.0

    return AiUsageLedgerEntry(
        ledger_id=str(uuid.uuid4()),
        period_start=period_start,
        period_end=period_end,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_model_calls=len(distinct_runs),
        total_source_fetches=len(fetches_in),
        estimated_cost_usd=total_cost,
        models_used=models_used,
        briefs_published=published,
        briefs_drafted=drafted,
        briefs_rejected=rejected,
        human_review_hours=human_review_hours,
        give_back_ratio=give_back,
    )


def render_ledger_markdown(entry: AiUsageLedgerEntry) -> str:
    """One-paragraph human-readable summary of a ledger row."""
    lines = [
        f"# AI Usage Ledger — {entry.period_start.isoformat()} to {entry.period_end.isoformat()}",
        "",
        f"- **Briefs published (numerator):** {entry.briefs_published}",
        f"- **Briefs drafted:** {entry.briefs_drafted}",
        f"- **Briefs rejected:** {entry.briefs_rejected}",
        f"- **Estimated cost (denominator):** ${entry.estimated_cost_usd:.4f} USD",
        f"- **Give-back ratio:** {entry.give_back_ratio:.2f} briefs/USD",
        "",
        f"- **Total input tokens:** {entry.total_input_tokens:,}",
        f"- **Total output tokens:** {entry.total_output_tokens:,}",
        f"- **Total model calls:** {entry.total_model_calls}",
        f"- **Total source fetches:** {entry.total_source_fetches}",
        f"- **Models used:** {', '.join(entry.models_used) if entry.models_used else '—'}",
    ]
    if entry.human_review_hours is not None:
        lines.append(f"- **Human review hours:** {entry.human_review_hours:.2f}")
    lines.append("")
    return "\n".join(lines)


def export_briefs(
    published_rows: Iterable[dict[str, Any]],
    export_dir: Path,
) -> list[Path]:
    """Write each published row's markdown_export to a file under export_dir.
    Returns the paths written (skipped existing-and-matching files are not
    in the return list).
    """
    written: list[Path] = []
    for row in published_rows:
        published_at = row["published_at"]
        if isinstance(published_at, str):
            published_at = datetime.fromisoformat(
                published_at.replace("Z", "+00:00")
            )
        ym = published_at.strftime("%Y-%m")
        path = export_dir / "briefs" / ym / f"{row['published_id']}.md"
        path.parent.mkdir(parents=True, exist_ok=True)

        existing_hash = None
        if path.exists():
            existing_hash = Hasher.hash_string(path.read_text())
        if existing_hash == row["content_hash"]:
            continue  # already exported with matching content

        path.write_text(row["markdown_export"])
        written.append(path)
    return written


def format_seal_line(checkpoint: dict[str, Any]) -> str:
    """Format a CHECKPOINT chain entry as one line in SEALS.md.

    Both `merkle` (target_payload_hash — verifiable without the salt) and
    `seal` (row_signature — verifiable if the verifier has the prior chain's
    signatures) are published, so external observers have two independent
    handles for tamper detection.
    """
    recorded_at = checkpoint["recorded_at"]
    if isinstance(recorded_at, str):
        recorded_at = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    return (
        f"{recorded_at.date().isoformat()}  "
        f"seq={checkpoint['sequence_id']}  "
        f"merkle={checkpoint['target_payload_hash']}  "
        f"seal={checkpoint['row_signature']}  "
        f"salt_v={checkpoint['salt_version']}\n"
    )


def append_seals(
    checkpoints: Iterable[dict[str, Any]],
    seal_log_path: Path,
) -> int:
    """Append CHECKPOINT entries to seal_log_path. Idempotent: skips lines
    already present (matched by full line equality).
    Returns count appended. Does not create the file if there's nothing to write.
    """
    existing = seal_log_path.read_text() if seal_log_path.exists() else ""
    sorted_cps = sorted(checkpoints, key=lambda c: c["sequence_id"])
    pending: list[str] = []
    for cp in sorted_cps:
        if cp.get("operation") != "CHECKPOINT":
            continue
        line = format_seal_line(cp)
        if line in existing or line in pending:
            continue
        pending.append(line)

    if not pending:
        return 0

    seal_log_path.parent.mkdir(parents=True, exist_ok=True)
    with seal_log_path.open("a", encoding="utf-8") as f:
        for line in pending:
            f.write(line)
    return len(pending)


def parse_retraction_commits(
    git_log_lines: Iterable[str],
) -> list[Retraction]:
    """Parse `git log` output lines for retraction commits.

    Expected input format (one entry per commit, blank-line separated):
        sha=<sha>
        author=<email>
        date=<iso8601>
        subject=retraction: pub-<id> — <reason>
        body=<remaining lines, optional 'superseded-by: pub-<id>'>

    Phase 12 v1 ships this parser; the wiring to actually invoke `git log`
    on a checked-out public repo is a follow-up step in the runbook.
    """
    out: list[Retraction] = []
    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in git_log_lines:
        line = line.rstrip("\n")
        if line == "" and current:
            blocks.append(current)
            current = {}
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            if k in current and k == "body":
                current[k] += "\n" + v
            else:
                current[k] = v
    if current:
        blocks.append(current)

    for b in blocks:
        subject = b.get("subject", "")
        if not subject.startswith("retraction:"):
            continue
        # subject format: "retraction: pub-<id> — <reason>"
        rest = subject[len("retraction:"):].strip()
        if "—" in rest:
            id_part, reason = rest.split("—", 1)
        elif "-" in rest:
            id_part, reason = rest.split("-", 1)
        else:
            id_part, reason = rest, ""
        published_id = id_part.strip()
        reason = reason.strip()

        superseded_by = None
        body = b.get("body", "")
        for body_line in body.splitlines():
            if body_line.lower().startswith("superseded-by:"):
                superseded_by = body_line.split(":", 1)[1].strip()

        retracted_at_str = b.get("date", "")
        retracted_at = datetime.fromisoformat(
            retracted_at_str.replace("Z", "+00:00")
        ) if retracted_at_str else datetime.now(timezone.utc)

        out.append(Retraction(
            retraction_id=str(uuid.uuid4()),
            published_id=published_id,
            retracted_at=retracted_at,
            retraction_reason=reason,
            retracted_by=b.get("author", "unknown"),
            superseded_by=superseded_by,
            git_commit_sha=b.get("sha", ""),
        ))
    return out


# -----------------------------------------------------------------------------
# Delta readers (live data) — wrapped so tests can substitute fakes
# -----------------------------------------------------------------------------

class CommonsReader:
    """Reads the live tables Phase 12 needs to publish."""

    def __init__(self, delta: DeltaTableWriter | None = None) -> None:
        self.delta = delta or DeltaTableWriter()

    def _read(self, schema: str, table: str) -> list[dict[str, Any]]:
        if not _DELTA_AVAILABLE:
            raise RuntimeError(
                "deltalake not installed. Run: pip install -r openclaw/requirements.txt"
            )
        path = self.delta.path_for(schema, table)
        if not Path(path).exists():
            return []
        return DeltaTable(path).to_pyarrow_table().to_pylist()

    def run_summaries(self) -> list[dict[str, Any]]:
        return self._read("gold", "run_summaries")

    def research_summaries(self) -> list[dict[str, Any]]:
        return self._read("gold", "research_summaries")

    def source_fetches(self) -> list[dict[str, Any]]:
        return self._read("bronze", "source_fetches")

    def published_briefs(self) -> list[dict[str, Any]]:
        return self._read("public_archive", "published")

    def chain_checkpoints(self) -> list[dict[str, Any]]:
        rows = self._read("audit", "integrity_chain")
        return [r for r in rows if r.get("operation") == "CHECKPOINT"]


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _cmd_rollup_ledger(args: argparse.Namespace) -> int:
    reader = CommonsReader()
    target = (
        date.fromisoformat(args.date) if args.date
        else datetime.now(timezone.utc).date() - timedelta(days=1)
    )
    period_start = target if args.days <= 1 else target - timedelta(days=args.days - 1)
    period_end = target

    entry = compute_rollup(
        period_start, period_end,
        reader.run_summaries(),
        reader.research_summaries(),
        reader.source_fetches(),
    )

    # Write to research.shared.ai_usage_ledger via delta
    delta = reader.delta
    path = delta.path_for("shared", "ai_usage_ledger")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    delta.append("shared", "ai_usage_ledger", [entry.model_dump()])

    # Also write a human-readable copy alongside the markdown briefs.
    commons_dir = Path(args.commons_dir)
    md_path = commons_dir / "ledger" / f"{period_end.isoformat()}.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_ledger_markdown(entry))

    print(f"✓ ledger entry written")
    print(f"  period:           {period_start} to {period_end}")
    print(f"  briefs_published: {entry.briefs_published}")
    print(f"  estimated_cost:   ${entry.estimated_cost_usd:.4f}")
    print(f"  give_back_ratio:  {entry.give_back_ratio:.2f} briefs/USD")
    print(f"  human-readable:   {md_path}")
    return 0


def _cmd_export_briefs(args: argparse.Namespace) -> int:
    reader = CommonsReader()
    rows = reader.published_briefs()
    written = export_briefs(rows, Path(args.commons_dir))
    print(f"✓ exported {len(written)} new brief(s)")
    for p in written:
        print(f"  {p.relative_to(Path(args.commons_dir))}")
    skipped = len(rows) - len(written)
    if skipped:
        print(f"  ({skipped} brief(s) already up-to-date — content_hash matched)")
    return 0


def _cmd_publish_seal(args: argparse.Namespace) -> int:
    reader = CommonsReader()
    cps = reader.chain_checkpoints()
    seal_log = Path(args.commons_dir) / "SEALS.md"
    n = append_seals(cps, seal_log)
    print(f"✓ appended {n} new seal(s) to {seal_log}")
    return 0


def _cmd_publish_all(args: argparse.Namespace) -> int:
    rc1 = _cmd_rollup_ledger(args)
    print()
    rc2 = _cmd_export_briefs(args)
    print()
    rc3 = _cmd_publish_seal(args)
    return max(rc1, rc2, rc3)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="commons_publisher",
        description="Phase 12 public commons exporter — daily AI-usage rollup, "
                    "markdown brief export, and public seal log.",
    )
    parser.add_argument(
        "--commons-dir",
        default=str(DEFAULT_COMMONS_DIR),
        help=f"Output directory (default: {DEFAULT_COMMONS_DIR})",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    rl = sub.add_parser("rollup-ledger", help="Compute daily AI-usage rollup")
    rl.add_argument("--date", help="YYYY-MM-DD; defaults to yesterday (UTC)")
    rl.add_argument("--days", type=int, default=1, help="Period length in days")

    sub.add_parser("export-briefs", help="Render new published briefs as markdown")
    sub.add_parser("publish-seal", help="Append new chain seals to SEALS.md")
    sub.add_parser("publish-all", help="Run rollup + export + seal idempotently")

    args = parser.parse_args(argv)
    handlers = {
        "rollup-ledger": _cmd_rollup_ledger,
        "export-briefs": _cmd_export_briefs,
        "publish-seal": _cmd_publish_seal,
        "publish-all": _cmd_publish_all,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
