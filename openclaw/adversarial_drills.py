#!/usr/bin/env python3
"""
adversarial_drills.py — Phase 11 operator CLI for live-system attack drills.

The pytest suite in tests/test_adversarial.py covers boundary invariants
in isolation. These drills run against the *live* Delta tables on the VM
to confirm the same boundaries hold against real data.

Subcommands:
    boundary-check
        Static analysis of openclaw/*.py: confirm no agent module
        imports promote.py and no module outside promote.py constructs
        status='PROMOTED'.

    markdown-integrity <published_id>
        Read public_archive.published, recompute SHA-256 of the
        markdown_export, compare against stored content_hash.

    chain-audit
        Run integrity_engine.audit. Equivalent to
        `python3 integrity_engine.py audit`, included here so a single
        adversarial_drills run touches every Phase 11 boundary.

    promotion-blocked
        Introspect DatabricksWorker: confirm no public method matches
        the patterns 'promote', 'publish', 'approve'.

    pydantic-invariants
        Confirm the Pydantic models reject the inputs an adversary
        would try (short counter_arguments, T4 without justification).

    all
        Run every drill above. Exit 0 if all pass, 1 otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path
from typing import Callable

# Allow `python3 ~/openclaw/adversarial_drills.py ...` to import sibling
# modules under the openclaw package. Without this, sys.path[0] is the
# openclaw/ directory rather than its parent, and `from openclaw.X` fails.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(msg: str) -> None:
    print(f"  ✗ {msg}")


# -----------------------------------------------------------------------------
# Drill: boundary-check (static)
# -----------------------------------------------------------------------------

def drill_boundary_check() -> bool:
    """Confirm code-level boundaries: agent modules don't reach promote.py,
    and only promote.py constructs status='PROMOTED'.
    """
    print("[boundary-check]")
    openclaw_dir = Path(__file__).resolve().parent
    agent_modules = [
        "databricks_worker.py", "integrity_engine.py",
        "fairness_scorer.py", "mlflow_tracker.py",
    ]
    passed = True

    for mod in agent_modules:
        path = openclaw_dir / mod
        if not path.exists():
            _fail(f"{mod} not found at {path}")
            passed = False
            continue
        content = path.read_text()
        if "from openclaw.promote" in content or "import openclaw.promote" in content:
            _fail(f"{mod} imports openclaw.promote — boundary violated")
            passed = False
        else:
            _ok(f"{mod} has no openclaw.promote import")

    pattern = re.compile(r"""status=["']PROMOTED["']""")
    # promote.py legitimately constructs PROMOTED rows; tests/ asserts on
    # PROMOTED state; this file contains the regex pattern itself. Skip
    # all three.
    self_name = Path(__file__).name
    offenders = []
    for py in openclaw_dir.rglob("*.py"):
        if py.name in ("promote.py", self_name) or "tests" in py.parts:
            continue
        if pattern.search(py.read_text()):
            offenders.append(py.relative_to(openclaw_dir))
    if offenders:
        _fail(f"PROMOTED status constructed outside promote.py: {offenders}")
        passed = False
    else:
        _ok("PROMOTED status only constructed in promote.py")

    return passed


# -----------------------------------------------------------------------------
# Drill: promotion-blocked (introspection)
# -----------------------------------------------------------------------------

def drill_promotion_blocked() -> bool:
    """Confirm DatabricksWorker has no method that suggests promotion."""
    print("[promotion-blocked]")
    from openclaw.databricks_worker import DatabricksWorker

    forbidden = ["promote", "publish", "approve"]
    public = [m for m in dir(DatabricksWorker) if not m.startswith("_")]
    found_offender = False
    for m in public:
        for kw in forbidden:
            if kw in m.lower():
                _fail(f"DatabricksWorker.{m} contains '{kw}'")
                found_offender = True
    if not found_offender:
        _ok(f"none of DatabricksWorker's {len(public)} public methods "
            f"match {forbidden}")
    return not found_offender


# -----------------------------------------------------------------------------
# Drill: pydantic-invariants
# -----------------------------------------------------------------------------

def drill_pydantic_invariants() -> bool:
    """Confirm the Pydantic models reject obvious adversarial inputs."""
    print("[pydantic-invariants]")
    from datetime import datetime, timezone
    from openclaw.databricks_worker import Citation, ValidatedBrief

    passed = True

    # Short counter_arguments
    try:
        ValidatedBrief(
            brief_id="b", response_id="r",
            created_at=datetime.now(timezone.utc),
            topic_id="t", title="x", key_findings="y",
            counter_arguments="too short",
            practical_implications="z", further_reading="w",
            content_hash="h", agent_version="v",
        )
        _fail("ValidatedBrief accepted short counter_arguments — invariant broken")
        passed = False
    except Exception:
        _ok("ValidatedBrief rejects counter_arguments < 100 chars")

    # T4 without justification
    try:
        Citation(
            citation_id="c", brief_id="b", url="https://x", title="t",
            authority_tier=4, is_paid_content=False,
        )
        _fail("Citation accepted tier-4 without justification — invariant broken")
        passed = False
    except Exception:
        _ok("Citation rejects authority_tier=4 without t4_justification")

    return passed


# -----------------------------------------------------------------------------
# Drill: markdown-integrity
# -----------------------------------------------------------------------------

def drill_markdown_integrity(published_id: str) -> bool:
    """Read public_archive.published for the given published_id, recompute
    SHA-256 of markdown_export, compare to stored content_hash.
    """
    print(f"[markdown-integrity published_id={published_id}]")
    from openclaw.databricks_worker import _DELTA_AVAILABLE, DeltaTableWriter

    if not _DELTA_AVAILABLE:
        _fail("deltalake not installed; install via pip install -r requirements.txt")
        return False
    from deltalake import DeltaTable

    delta = DeltaTableWriter()
    path = delta.path_for("public_archive", "published")
    if not Path(path).exists():
        _fail(f"public_archive.published does not exist at {path}")
        return False

    rows = DeltaTable(path).to_pyarrow_table().to_pylist()
    matching = [r for r in rows if r.get("published_id") == published_id]
    if not matching:
        _fail(f"no row with published_id={published_id}")
        return False
    if len(matching) > 1:
        _fail(f"multiple rows for published_id={published_id} — duplicate?")
        return False

    row = matching[0]
    stored = row["content_hash"]
    recomputed = hashlib.sha256(row["markdown_export"].encode("utf-8")).hexdigest()
    if stored != recomputed:
        _fail(f"content_hash MISMATCH — markdown has been tampered")
        _fail(f"  stored:     {stored}")
        _fail(f"  recomputed: {recomputed}")
        return False
    _ok(f"content_hash matches recompute ({stored[:16]}...)")
    return True


# -----------------------------------------------------------------------------
# Drill: chain-audit (delegates to integrity_engine)
# -----------------------------------------------------------------------------

def drill_chain_audit() -> bool:
    """Run the integrity-chain audit; pass if INTACT."""
    print("[chain-audit]")
    from openclaw.integrity_engine import (
        IntegrityAuditor, SaltUnavailableError, build_components,
    )
    try:
        store, _w, signer, _s, anchor = build_components()
    except SaltUnavailableError as e:
        _fail(str(e))
        return False
    verdict = IntegrityAuditor(store, signer, anchor=anchor).audit()
    if verdict.is_intact:
        _ok(f"chain INTACT, {verdict.chain_length} entries verified up to "
            f"sequence_id={verdict.last_verified_sequence_id}")
        return True
    _fail(f"TAMPER at sequence_id={verdict.first_tampered_sequence_id}: "
          f"{verdict.failure_reason}")
    return False


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="adversarial_drills",
        description="Phase 11 operator drills — confirm the boundaries hold "
                    "against the live Delta data on the VM.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("boundary-check", help="Static code-boundary checks")
    sub.add_parser("promotion-blocked", help="Confirm worker has no promotion method")
    sub.add_parser("pydantic-invariants", help="Confirm models reject adversarial inputs")
    md = sub.add_parser("markdown-integrity", help="Recompute content_hash on a published row")
    md.add_argument("published_id")
    sub.add_parser("chain-audit", help="Run the integrity-chain audit")
    sub.add_parser("all", help="Run every drill (markdown-integrity skipped — needs an id)")

    args = parser.parse_args(argv)

    drills_no_arg: list[tuple[str, Callable[[], bool]]] = [
        ("boundary-check", drill_boundary_check),
        ("promotion-blocked", drill_promotion_blocked),
        ("pydantic-invariants", drill_pydantic_invariants),
        ("chain-audit", drill_chain_audit),
    ]

    if args.cmd == "all":
        passed = 0
        failed = 0
        for name, fn in drills_no_arg:
            try:
                ok = fn()
            except Exception as e:
                print(f"[{name}] CRASHED: {e}")
                ok = False
            print()
            if ok:
                passed += 1
            else:
                failed += 1
        print(f"=== summary ===")
        print(f"passed: {passed}/{len(drills_no_arg)}")
        print(f"failed: {failed}/{len(drills_no_arg)}")
        if failed:
            print("\nNote: markdown-integrity is not in 'all' because it needs a")
            print("published_id. Run it explicitly: adversarial_drills markdown-integrity <id>")
        return 0 if failed == 0 else 1

    fn_map: dict[str, Callable[..., bool]] = {
        "boundary-check": drill_boundary_check,
        "promotion-blocked": drill_promotion_blocked,
        "pydantic-invariants": drill_pydantic_invariants,
        "chain-audit": drill_chain_audit,
    }

    if args.cmd == "markdown-integrity":
        ok = drill_markdown_integrity(args.published_id)
    else:
        ok = fn_map[args.cmd]()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
