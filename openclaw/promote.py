#!/usr/bin/env python3
"""
promote.py — Phase 10 promotion CLI for OpenClaw.

Reviews DRAFT and REJECTED briefs and (with explicit operator action)
promotes them to research.public_archive.published. Promotion is the
**only** code path that produces status='PROMOTED'; the agent has no
way to reach this surface.

Subcommands:
    list [--status DRAFT|REJECTED|PROMOTED] [--limit N]
    show <summary_id>
    approve <summary_id> [--reviewer NAME] [--override REASON]
    reject <summary_id> --reason TEXT [--reviewer NAME]
    history [--limit N]

Append-only semantics: a status change is a new research_summaries row
with the same summary_id and a later created_at. The "current" status of
a brief is "the latest row by created_at". This keeps delta-rs happy
(UPDATE remains Spark-only as of writing) and makes the integrity chain
naturally consistent — every state transition is its own chain entry.

Trust model (Phase 10 v1): the CLI trusts the operator's --reviewer arg.
Each promotion is recorded with that identity in audit.promotion_log AND
in the integrity chain's `author_identity` field. Physical principal
separation (a separate SECRET_SALT or UC token for the human reviewer)
is deferred to a future phase.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from openclaw.databricks_worker import (
    CATALOG,
    Citation,
    DeltaTableWriter,
    Hasher,
    ValidatedBrief,
    _DELTA_AVAILABLE,
)
from openclaw.fairness_scorer import (
    FairnessScorecard,
    ResearchSummary,
)

if _DELTA_AVAILABLE:
    from deltalake import DeltaTable

DEFAULT_REVIEWER = "david"
DEFAULT_ATTRIBUTION = "OpenClaw Project, reviewed by D. Callaghan"
DEFAULT_LICENSE = "CC BY 4.0"


# -----------------------------------------------------------------------------
# Pydantic models for the two new tables Phase 10 writes to
# -----------------------------------------------------------------------------

class PublishedBrief(BaseModel):
    """research.public_archive.published"""
    published_id: str
    summary_id: str
    published_at: datetime
    license: str
    attribution: str
    markdown_export: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class PromotionLog(BaseModel):
    """research.audit.promotion_log"""
    promotion_id: str
    summary_id: str
    promoted_at: datetime
    promoted_by: str = Field(min_length=1)
    override_reason: str | None = None
    prior_status: str
    new_status: str


# -----------------------------------------------------------------------------
# Brief reader — pulls a brief + citations + scorecard + summary history
# -----------------------------------------------------------------------------

class BriefReader:
    """Reads bronze/silver/gold tables to assemble a complete view of a brief."""

    def __init__(self, delta: DeltaTableWriter | None = None) -> None:
        self.delta = delta or DeltaTableWriter()

    def _read_table(self, schema: str, table: str) -> list[dict[str, Any]]:
        if not _DELTA_AVAILABLE:
            raise RuntimeError(
                "deltalake not installed. Run: pip install -r openclaw/requirements.txt"
            )
        path = self.delta.path_for(schema, table)
        if not Path(path).exists():
            return []
        dt = DeltaTable(path)
        return dt.to_pyarrow_table().to_pylist()

    def latest_summaries(self, status_filter: str | None = None) -> list[ResearchSummary]:
        """Return the latest row per summary_id, optionally filtered by status."""
        rows = self._read_table("gold", "research_summaries")
        # Group by summary_id, take the row with the largest created_at.
        latest: dict[str, dict[str, Any]] = {}
        for r in rows:
            sid = r["summary_id"]
            if sid not in latest or r["created_at"] > latest[sid]["created_at"]:
                latest[sid] = r
        summaries = [ResearchSummary.model_validate(r) for r in latest.values()]
        if status_filter:
            summaries = [s for s in summaries if s.status == status_filter]
        summaries.sort(key=lambda s: s.created_at, reverse=True)
        return summaries

    def summary_history(self, summary_id: str) -> list[ResearchSummary]:
        """Every row for one summary_id, oldest first — the status timeline."""
        rows = [
            r for r in self._read_table("gold", "research_summaries")
            if r["summary_id"] == summary_id
        ]
        rows.sort(key=lambda r: r["created_at"])
        return [ResearchSummary.model_validate(r) for r in rows]

    def get_brief(self, brief_id: str) -> ValidatedBrief | None:
        rows = [
            r for r in self._read_table("silver", "validated_briefs")
            if r["brief_id"] == brief_id
        ]
        if not rows:
            return None
        return ValidatedBrief.model_validate(rows[0])

    def citations_for(self, brief_id: str) -> list[Citation]:
        rows = [
            r for r in self._read_table("silver", "citations")
            if r["brief_id"] == brief_id
        ]
        rows.sort(key=lambda r: r["citation_id"])
        return [Citation.model_validate(r) for r in rows]

    def latest_scorecard_for(self, brief_id: str) -> FairnessScorecard | None:
        rows = [
            r for r in self._read_table("gold", "fairness_scorecards")
            if r["brief_id"] == brief_id
        ]
        if not rows:
            return None
        rows.sort(key=lambda r: r["computed_at"], reverse=True)
        return FairnessScorecard.model_validate(rows[0])


# -----------------------------------------------------------------------------
# Markdown exporter — deterministic CC BY 4.0 formatter for published briefs
# -----------------------------------------------------------------------------

class MarkdownExporter:
    """Renders a brief + citations + scorecard as a markdown document.

    Deterministic: same inputs always produce identical output. The
    content_hash is computed over the final markdown so external readers
    can verify the file hasn't been tampered with.
    """

    @staticmethod
    def render(
        *,
        brief: ValidatedBrief,
        citations: list[Citation],
        scorecard: FairnessScorecard | None,
        published_id: str,
        summary: ResearchSummary,
        reviewer: str,
        attribution: str = DEFAULT_ATTRIBUTION,
        license: str = DEFAULT_LICENSE,
    ) -> str:
        promoted_at = summary.promoted_at or summary.created_at
        promoted_iso = promoted_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        created_iso = brief.created_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Frontmatter — fixed values, deterministic.
        frontmatter_lines = [
            "---",
            f"published_id: {published_id}",
            f"summary_id: {summary.summary_id}",
            f"brief_id: {brief.brief_id}",
            f"published_at: {promoted_iso}",
            f"license: {license}",
            f"attribution: {attribution}",
        ]
        if summary.override_reason:
            frontmatter_lines.append(f'override_reason: "{summary.override_reason}"')
        frontmatter_lines.append("---")

        body_lines = [
            "",
            f"# {brief.title}",
            "",
            f"**Topic:** {brief.topic_id}  ",
            f"**Generated:** {created_iso} | **Promoted:** {promoted_iso} | **Reviewer:** {reviewer}  ",
            f"**License:** [{license}](https://creativecommons.org/licenses/by/4.0/)",
            "",
            "## Key Findings",
            "",
            brief.key_findings.strip(),
            "",
            "## Counter-Arguments",
            "",
            brief.counter_arguments.strip(),
            "",
            "## Practical Implications",
            "",
            brief.practical_implications.strip(),
            "",
            "## Further Reading",
            "",
            brief.further_reading.strip(),
        ]

        if brief.known_gaps and brief.known_gaps.strip():
            body_lines.extend([
                "",
                "## Known Gaps",
                "",
                brief.known_gaps.strip(),
            ])

        body_lines.extend([
            "",
            "## Citations",
            "",
            "| # | Tier | Source | Vendor | Date |",
            "|---|---|---|---|---|",
        ])
        t4_justifications: list[tuple[int, str]] = []
        for i, c in enumerate(citations, start=1):
            vendor = c.vendor_org or "—"
            pub = c.publication_date.isoformat() if c.publication_date else "—"
            body_lines.append(
                f"| {i} | T{c.authority_tier} | "
                f"[{c.title}]({c.url}) | {vendor} | {pub} |"
            )
            if c.authority_tier == 4 and c.t4_justification:
                t4_justifications.append((i, c.t4_justification))

        if t4_justifications:
            body_lines.extend(["", "**Tier 4 justifications:**"])
            for i, just in t4_justifications:
                body_lines.append(f"- Citation {i}: {just}")

        if scorecard is not None:
            body_lines.extend([
                "",
                "## Fairness Scorecard",
                "",
                f"- Source tier T1+T2 share: "
                f"{scorecard.source_tier_t1_pct + scorecard.source_tier_t2_pct:.1f}%",
                f"- Vendor diversity: {scorecard.vendor_diversity_count} distinct",
                f"- Counter-argument ratio: {scorecard.counter_argument_ratio * 100:.1f}%",
                f"- Recency within 18 months: {scorecard.recency_within_18mo_pct:.1f}%",
                f"- Bias checklist: {'complete' if scorecard.bias_check_complete else 'incomplete'}",
                f"- Threshold pass: **{scorecard.threshold_pass}**",
            ])
            if not scorecard.threshold_pass and summary.override_reason:
                body_lines.extend([
                    "",
                    "> **Promotion override.** This brief originally landed as REJECTED "
                    f"with these threshold failures: `{', '.join(scorecard.failed_thresholds)}`. "
                    f"The reviewer promoted it with the stated reason: "
                    f'"{summary.override_reason}"',
                ])

        body_lines.extend([
            "",
            "---",
            "",
            f"*Reviewed by {reviewer} on {promoted_iso}. Generated by "
            f"{brief.agent_version}. Attribution required under {license} — "
            f"please cite `{published_id}` when referencing.*",
            "",
        ])

        return "\n".join(frontmatter_lines + body_lines)


# -----------------------------------------------------------------------------
# Promoter — orchestrates the writes for approve / reject
# -----------------------------------------------------------------------------

class Promoter:
    """Writes the three rows that constitute a promotion (or one row for a
    rejection). Each Delta append fires the integrity chain hook so the
    promotion is captured in audit.integrity_chain.
    """

    def __init__(
        self,
        delta: DeltaTableWriter | None = None,
        chain_writer: Any = None,
        reader: BriefReader | None = None,
        now: datetime | None = None,
    ) -> None:
        self.delta = delta or DeltaTableWriter()
        self.chain_writer = chain_writer  # Phase 8 ChainWriter, or None for stub mode
        self.reader = reader or BriefReader(self.delta)
        self._now = now

    def _utc_now(self) -> datetime:
        return self._now or datetime.now(timezone.utc)

    def _write(self, schema: str, table: str, payload: dict[str, Any], row_id: str) -> None:
        self.delta.append(schema, table, [payload])
        if self.chain_writer is not None:
            self.chain_writer.append_entry(
                operation="INSERT",
                target_table=f"{self.delta.catalog}.{schema}.{table}",
                target_row_id=row_id,
                target_payload_hash=Hasher.hash_canonical(payload),
                author_identity=os.environ.get("REVIEWER_IDENTITY", DEFAULT_REVIEWER),
            )

    def approve(
        self,
        summary_id: str,
        *,
        reviewer: str = DEFAULT_REVIEWER,
        override_reason: str | None = None,
        attribution: str = DEFAULT_ATTRIBUTION,
        license: str = DEFAULT_LICENSE,
    ) -> dict[str, Any]:
        history = self.reader.summary_history(summary_id)
        if not history:
            raise PromotionError(f"summary_id={summary_id} not found")
        current = history[-1]
        if current.status == "PROMOTED":
            raise PromotionError(f"summary_id={summary_id} is already PROMOTED")

        if current.status == "REJECTED" and not override_reason:
            raise PromotionError(
                f"summary_id={summary_id} is REJECTED; --override REASON is required"
            )

        brief = self.reader.get_brief(current.brief_id)
        if brief is None:
            raise PromotionError(
                f"brief_id={current.brief_id} not found in silver.validated_briefs"
            )
        citations = self.reader.citations_for(current.brief_id)
        scorecard = self.reader.latest_scorecard_for(current.brief_id)

        published_id = f"pub-{uuid.uuid4().hex[:12]}"
        promoted_at = self._utc_now()

        # 1) New research_summaries row (append-only): status=PROMOTED
        new_summary = ResearchSummary(
            summary_id=summary_id,
            brief_id=current.brief_id,
            created_at=promoted_at,
            status="PROMOTED",
            rejection_reasons=current.rejection_reasons,
            override_reason=override_reason,
            promoted_at=promoted_at,
            promoted_by=reviewer,
            mlflow_run_id=current.mlflow_run_id,
        )

        # 2) Render markdown deterministically and hash it
        markdown = MarkdownExporter.render(
            brief=brief, citations=citations, scorecard=scorecard,
            published_id=published_id, summary=new_summary,
            reviewer=reviewer, attribution=attribution, license=license,
        )
        content_hash = Hasher.hash_string(markdown)

        published = PublishedBrief(
            published_id=published_id,
            summary_id=summary_id,
            published_at=promoted_at,
            license=license,
            attribution=attribution,
            markdown_export=markdown,
            content_hash=content_hash,
        )

        promotion = PromotionLog(
            promotion_id=str(uuid.uuid4()),
            summary_id=summary_id,
            promoted_at=promoted_at,
            promoted_by=reviewer,
            override_reason=override_reason,
            prior_status=current.status,
            new_status="PROMOTED",
        )

        # Three writes — order matters for the chain (each must reference
        # the prior signature).
        self._write("gold", "research_summaries", new_summary.model_dump(),
                    row_id=new_summary.summary_id)
        self._write("public_archive", "published", published.model_dump(),
                    row_id=published.published_id)
        self._write("audit", "promotion_log", promotion.model_dump(),
                    row_id=promotion.promotion_id)

        return {
            "summary": new_summary,
            "published": published,
            "promotion_log": promotion,
            "markdown": markdown,
        }

    def reject(
        self,
        summary_id: str,
        *,
        reason: str,
        reviewer: str = DEFAULT_REVIEWER,
    ) -> dict[str, Any]:
        history = self.reader.summary_history(summary_id)
        if not history:
            raise PromotionError(f"summary_id={summary_id} not found")
        current = history[-1]
        if current.status == "PROMOTED":
            raise PromotionError(
                f"summary_id={summary_id} is PROMOTED; rejection would invalidate "
                "an already-published brief. Use the retraction mechanism instead."
            )
        if not reason:
            raise PromotionError("--reason is required for reject")

        rejected_at = self._utc_now()
        new_summary = ResearchSummary(
            summary_id=summary_id,
            brief_id=current.brief_id,
            created_at=rejected_at,
            status="REJECTED",
            rejection_reasons=(current.rejection_reasons or []) + [f"reviewer:{reason}"],
            mlflow_run_id=current.mlflow_run_id,
        )

        promotion = PromotionLog(
            promotion_id=str(uuid.uuid4()),
            summary_id=summary_id,
            promoted_at=rejected_at,
            promoted_by=reviewer,
            override_reason=reason,
            prior_status=current.status,
            new_status="REJECTED",
        )

        self._write("gold", "research_summaries", new_summary.model_dump(),
                    row_id=new_summary.summary_id)
        self._write("audit", "promotion_log", promotion.model_dump(),
                    row_id=promotion.promotion_id)

        return {"summary": new_summary, "promotion_log": promotion}


class PromotionError(RuntimeError):
    """Promotion or rejection refused — the operator's request can't be honored."""


# -----------------------------------------------------------------------------
# CLI helpers
# -----------------------------------------------------------------------------

def _build_chain_writer_or_none() -> Any:
    """Build a Phase 8 ChainWriter when SECRET_SALT is set; else return None
    (chain hook becomes a no-op for promotions in that case).
    """
    if not os.environ.get("SECRET_SALT"):
        return None
    try:
        from openclaw.integrity_engine import build_components
        _store, writer, _signer, _salter, _anchor = build_components()
        return writer
    except Exception as e:
        sys.stderr.write(
            f"WARN: SECRET_SALT is set but integrity engine failed to init "
            f"({e}); promotions will not be chained.\n"
        )
        return None


def _summary_one_line(s: ResearchSummary) -> str:
    return (
        f"{s.summary_id}  "
        f"status={s.status:<9}  "
        f"created={s.created_at.strftime('%Y-%m-%d %H:%M')}  "
        f"brief={s.brief_id}"
    )


def _cmd_list(args: argparse.Namespace) -> int:
    reader = BriefReader()
    summaries = reader.latest_summaries(status_filter=args.status)
    if args.limit:
        summaries = summaries[: args.limit]
    if not summaries:
        print("(no summaries)")
        return 0
    for s in summaries:
        print(_summary_one_line(s))
        if s.status == "REJECTED" and s.rejection_reasons:
            print(f"    reasons: {', '.join(s.rejection_reasons)}")
        if s.override_reason:
            print(f"    override: {s.override_reason}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    reader = BriefReader()
    history = reader.summary_history(args.summary_id)
    if not history:
        print(f"summary_id={args.summary_id} not found", file=sys.stderr)
        return 1
    current = history[-1]
    brief = reader.get_brief(current.brief_id)
    citations = reader.citations_for(current.brief_id)
    scorecard = reader.latest_scorecard_for(current.brief_id)

    print(f"summary_id:  {current.summary_id}")
    print(f"brief_id:    {current.brief_id}")
    print(f"status:      {current.status}")
    print(f"created_at:  {current.created_at.isoformat()}")
    if current.rejection_reasons:
        print(f"rejection_reasons: {current.rejection_reasons}")
    if current.override_reason:
        print(f"override_reason:   {current.override_reason}")
    print(f"history ({len(history)} rows):")
    for h in history:
        print(f"  {h.created_at.strftime('%Y-%m-%d %H:%M')}  {h.status}")
    if brief:
        print(f"\ntitle: {brief.title}")
        print(f"topic: {brief.topic_id}")
        print(f"--- key_findings ({len(brief.key_findings)} chars) ---")
        print(brief.key_findings[:600] + ("..." if len(brief.key_findings) > 600 else ""))
        print(f"\n--- counter_arguments ({len(brief.counter_arguments)} chars) ---")
        print(brief.counter_arguments[:600] + ("..." if len(brief.counter_arguments) > 600 else ""))
    if citations:
        print(f"\ncitations: {len(citations)}")
        for i, c in enumerate(citations[:10], 1):
            print(f"  {i}. T{c.authority_tier} {c.vendor_org or '—':<20} {c.url}")
    if scorecard:
        print(f"\nscorecard:")
        print(f"  threshold_pass:    {scorecard.threshold_pass}")
        print(f"  failed_thresholds: {scorecard.failed_thresholds}")
        print(f"  T1+T2 share:       {scorecard.source_tier_t1_pct + scorecard.source_tier_t2_pct:.1f}%")
        print(f"  vendor diversity:  {scorecard.vendor_diversity_count}")
        print(f"  counter-arg ratio: {scorecard.counter_argument_ratio * 100:.1f}%")
        print(f"  recency 18mo:      {scorecard.recency_within_18mo_pct:.1f}%")
        print(f"  bias complete:     {scorecard.bias_check_complete}")
    return 0


def _cmd_approve(args: argparse.Namespace) -> int:
    promoter = Promoter(chain_writer=_build_chain_writer_or_none())
    try:
        result = promoter.approve(
            args.summary_id,
            reviewer=args.reviewer,
            override_reason=args.override,
        )
    except PromotionError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    pub = result["published"]
    print(f"✓ promoted summary_id={args.summary_id}")
    print(f"  published_id:  {pub.published_id}")
    print(f"  published_at:  {pub.published_at.isoformat()}")
    print(f"  content_hash:  {pub.content_hash}")
    print(f"  reviewer:      {args.reviewer}")
    if args.override:
        print(f"  override:      {args.override}")
    if args.export:
        Path(args.export).write_text(pub.markdown_export)
        print(f"  markdown:      written to {args.export}")
    return 0


def _cmd_reject(args: argparse.Namespace) -> int:
    promoter = Promoter(chain_writer=_build_chain_writer_or_none())
    try:
        result = promoter.reject(
            args.summary_id,
            reason=args.reason,
            reviewer=args.reviewer,
        )
    except PromotionError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    summary = result["summary"]
    print(f"✓ rejected summary_id={args.summary_id}")
    print(f"  reviewer:  {args.reviewer}")
    print(f"  reason:    {args.reason}")
    print(f"  recorded:  {summary.created_at.isoformat()}")
    return 0


def _cmd_history(args: argparse.Namespace) -> int:
    reader = BriefReader()
    rows = reader._read_table("audit", "promotion_log")
    rows.sort(key=lambda r: r["promoted_at"], reverse=True)
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        print("(no promotion log entries)")
        return 0
    for r in rows:
        ts = r["promoted_at"]
        ts_str = ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)
        print(
            f"{ts_str}  {r['prior_status']:<9} -> {r['new_status']:<9}  "
            f"by={r['promoted_by']:<12}  summary={r['summary_id']}"
        )
        if r.get("override_reason"):
            print(f"    override: {r['override_reason']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="promote",
        description="Phase 10 promotion CLI — review DRAFT/REJECTED briefs and "
                    "promote approved ones into research.public_archive.published",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    list_p = sub.add_parser("list", help="Show summaries (latest row per summary_id)")
    list_p.add_argument("--status", choices=["DRAFT", "REJECTED", "PROMOTED"])
    list_p.add_argument("--limit", type=int, default=50)

    show_p = sub.add_parser("show", help="Display a summary, brief, citations, scorecard")
    show_p.add_argument("summary_id")

    approve_p = sub.add_parser("approve", help="Promote a summary to public_archive.published")
    approve_p.add_argument("summary_id")
    approve_p.add_argument("--reviewer", default=DEFAULT_REVIEWER)
    approve_p.add_argument("--override", help="Required when promoting a REJECTED summary")
    approve_p.add_argument("--export", help="Optional path to write the markdown to disk")

    reject_p = sub.add_parser("reject", help="Mark a DRAFT as REJECTED with a reason")
    reject_p.add_argument("summary_id")
    reject_p.add_argument("--reason", required=True)
    reject_p.add_argument("--reviewer", default=DEFAULT_REVIEWER)

    history_p = sub.add_parser("history", help="Show audit.promotion_log entries")
    history_p.add_argument("--limit", type=int, default=50)

    args = parser.parse_args(argv)
    handlers = {
        "list": _cmd_list,
        "show": _cmd_show,
        "approve": _cmd_approve,
        "reject": _cmd_reject,
        "history": _cmd_history,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
