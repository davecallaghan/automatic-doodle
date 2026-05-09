#!/usr/bin/env python3
"""
integrity_engine.py — Phase 8 cryptographic integrity layer for OpenClaw.

Maintains an append-only, linked-hash chain of every state-changing operation
in the research datastore. Provides:

  - Genesis bootstrap (sequence_id=0 with deterministic public payload)
  - Linked-hash signing: row_signature = SHA-256(payload_hash || prev_hash || salt)
  - Merkle tree builder (pure function, deterministic)
  - Daily seal: a CHECKPOINT entry whose payload commits to a Merkle root
                over all of the day's row_signatures
  - Audit utility that walks the chain and pinpoints the exact sequence_id
    of any tamper event
  - CLI: `init`, `audit`, `seal`, `status`

Reuses CanonicalJSON and Hasher from databricks_worker.py — keep encoding rules
in sync, since chain payloads must be deterministically hashable across both
modules.

Phase 8 v1 supports salt_version=1 only. Salt rotation is deferred to a future
phase with a properly-designed key registry.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path

# Allow `python3 ~/openclaw/integrity_engine.py ...` to import sibling
# modules under the openclaw package regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel, Field, field_validator

from openclaw.databricks_worker import (
    CATALOG,
    CanonicalJSON,
    DeltaTableWriter,
    Hasher,
    _DELTA_AVAILABLE,
)

if _DELTA_AVAILABLE:
    from deltalake import DeltaTable

GENESIS_PREVIOUS_HASH = "0" * 64
DEFAULT_AUTHOR = "system"
GENESIS_ANCHOR_PATH = Path(os.environ.get(
    "GENESIS_ANCHOR_PATH", "/mnt/disks/research/audit/genesis.json"
))


# -----------------------------------------------------------------------------
# Errors
# -----------------------------------------------------------------------------

class IntegrityError(Exception):
    """Base class for integrity-engine errors."""


class SaltUnavailableError(IntegrityError):
    """SECRET_SALT env var missing or invalid."""


class GenesisAlreadyExistsError(IntegrityError):
    """init was called but the chain already has a genesis row."""


# -----------------------------------------------------------------------------
# Operations and entries
# -----------------------------------------------------------------------------

class ChainOperation(str, Enum):
    GENESIS = "GENESIS"
    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    CHECKPOINT = "CHECKPOINT"


HEX_64 = r"^[0-9a-f]{64}$"


class ChainEntry(BaseModel):
    """research.audit.integrity_chain — one row per state-changing operation."""

    model_config = {"frozen": True}

    sequence_id: int = Field(ge=0)
    recorded_at: datetime
    author_identity: str = Field(min_length=1)
    operation: ChainOperation
    target_table: str | None = None
    target_row_id: str | None = None
    target_payload_hash: str = Field(pattern=HEX_64)
    previous_hash: str = Field(pattern=HEX_64)
    row_signature: str = Field(pattern=HEX_64)
    salt_version: int = Field(ge=1)

    @field_validator("recorded_at")
    @classmethod
    def _utc_required(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("recorded_at must be timezone-aware (UTC preferred)")
        return v


# -----------------------------------------------------------------------------
# Salt + signing
# -----------------------------------------------------------------------------

class Salter:
    """Loads SECRET_SALT from env. v1 supports a single salt; rotation deferred."""

    MIN_SALT_LENGTH = 32

    def __init__(self, salt: str | None = None) -> None:
        salt = salt if salt is not None else os.environ.get("SECRET_SALT")
        if not salt:
            raise SaltUnavailableError(
                "SECRET_SALT env var not set. Generate with: "
                "python3 -c \"import secrets; print(secrets.token_hex(32))\""
            )
        if len(salt) < self.MIN_SALT_LENGTH:
            raise SaltUnavailableError(
                f"SECRET_SALT too short ({len(salt)} chars). "
                f"Minimum {self.MIN_SALT_LENGTH}, recommended 64 (hex of 32 bytes)."
            )
        self._salt = salt

    def get_salt(self, version: int = 1) -> str:
        if version != 1:
            raise NotImplementedError(
                f"salt_version={version} not supported; v1 only handles version=1. "
                "Salt rotation is a planned future phase."
            )
        return self._salt

    @property
    def current_version(self) -> int:
        return 1


class Signer:
    """row_signature = SHA-256(payload_hash || previous_hash || salt)."""

    def __init__(self, salter: Salter) -> None:
        self.salter = salter

    def sign(
        self, payload_hash: str, previous_hash: str, salt_version: int = 1
    ) -> str:
        salt = self.salter.get_salt(salt_version)
        material = (payload_hash + previous_hash + salt).encode("utf-8")
        return Hasher.hash_bytes(material)

    def verify(
        self, signature: str, payload_hash: str, previous_hash: str,
        salt_version: int = 1,
    ) -> bool:
        return self.sign(payload_hash, previous_hash, salt_version) == signature


# -----------------------------------------------------------------------------
# Merkle tree
# -----------------------------------------------------------------------------

class MerkleTreeBuilder:
    """Standard binary Merkle tree, duplicate-last padding for odd levels.
    Pure function — same input always yields the same root.
    """

    @staticmethod
    def root(hashes: list[str]) -> str:
        if not hashes:
            return GENESIS_PREVIOUS_HASH
        if len(hashes) == 1:
            return hashes[0]
        # Pad to even
        if len(hashes) % 2 == 1:
            hashes = hashes + [hashes[-1]]
        next_level: list[str] = []
        for i in range(0, len(hashes), 2):
            combined = (hashes[i] + hashes[i + 1]).encode("utf-8")
            next_level.append(Hasher.hash_bytes(combined))
        return MerkleTreeBuilder.root(next_level)


# -----------------------------------------------------------------------------
# Genesis anchor — external pointer that catches "rewrite the genesis" attacks
# -----------------------------------------------------------------------------

class GenesisAnchor:
    """Persists the genesis row's signature outside the chain. The auditor
    cross-checks against this so a tampered genesis is detectable.
    """

    def __init__(self, path: Path = GENESIS_ANCHOR_PATH) -> None:
        self.path = path

    def write(self, entry: ChainEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "sequence_id": entry.sequence_id,
                    "row_signature": entry.row_signature,
                    "target_payload_hash": entry.target_payload_hash,
                    "recorded_at": entry.recorded_at.isoformat(),
                    "salt_version": entry.salt_version,
                },
                indent=2,
            )
            + "\n"
        )

    def read(self) -> dict | None:
        if not self.path.exists():
            return None
        return json.loads(self.path.read_text())


# -----------------------------------------------------------------------------
# Chain reader / writer
# -----------------------------------------------------------------------------

class ChainStore:
    """Reads and appends to research.audit.integrity_chain.

    Phase 8 v1 assumes a single concurrent writer (the agent). delta-rs's
    optimistic concurrency control is enough for that. If multi-writer
    becomes a real concern, switch to a UC SQL-endpoint transaction.
    """

    SCHEMA = "audit"
    TABLE = "integrity_chain"

    def __init__(self, delta: DeltaTableWriter) -> None:
        self.delta = delta

    def append(self, entry: ChainEntry) -> None:
        self.delta.append(self.SCHEMA, self.TABLE, [entry.model_dump(mode="json")])

    def read_all_sorted(self) -> list[ChainEntry]:
        if not _DELTA_AVAILABLE:
            raise RuntimeError(
                "deltalake not installed. Run: pip install -r openclaw/requirements.txt"
            )
        path = self.delta.path_for(self.SCHEMA, self.TABLE)
        if not Path(path).exists():
            return []
        dt = DeltaTable(path)
        df = dt.to_pyarrow_table().sort_by([("sequence_id", "ascending")])
        return [ChainEntry.model_validate(row) for row in df.to_pylist()]

    def read_latest(self) -> ChainEntry | None:
        entries = self.read_all_sorted()
        return entries[-1] if entries else None


class ChainWriter:
    """Constructs a signed chain entry and appends it. Replaces the Phase 7
    `_chain_hook` stub when wired into DatabricksWorker.
    """

    def __init__(
        self,
        store: ChainStore,
        signer: Signer,
        salter: Salter,
        anchor: GenesisAnchor | None = None,
    ) -> None:
        self.store = store
        self.signer = signer
        self.salter = salter
        self.anchor = anchor

    def append_entry(
        self,
        operation: ChainOperation | str,
        target_table: str | None,
        target_row_id: str | None,
        target_payload_hash: str,
        author_identity: str = DEFAULT_AUTHOR,
    ) -> ChainEntry:
        if isinstance(operation, str):
            operation = ChainOperation(operation)

        latest = self.store.read_latest()
        next_seq = (latest.sequence_id + 1) if latest else 0
        prev_hash = latest.row_signature if latest else GENESIS_PREVIOUS_HASH
        salt_version = self.salter.current_version
        signature = self.signer.sign(target_payload_hash, prev_hash, salt_version)

        entry = ChainEntry(
            sequence_id=next_seq,
            recorded_at=datetime.now(timezone.utc),
            author_identity=author_identity,
            operation=operation,
            target_table=target_table,
            target_row_id=target_row_id,
            target_payload_hash=target_payload_hash,
            previous_hash=prev_hash,
            row_signature=signature,
            salt_version=salt_version,
        )
        self.store.append(entry)
        return entry


# -----------------------------------------------------------------------------
# Genesis bootstrap
# -----------------------------------------------------------------------------

class GenesisInitializer:
    """Writes sequence_id=0 with a deterministic, public-safe payload."""

    def __init__(
        self,
        store: ChainStore,
        signer: Signer,
        salter: Salter,
        anchor: GenesisAnchor | None = None,
    ) -> None:
        self.store = store
        self.signer = signer
        self.salter = salter
        self.anchor = anchor or GenesisAnchor()

    def write_genesis(
        self, project_meta: dict, author_identity: str = DEFAULT_AUTHOR,
    ) -> ChainEntry:
        if self.store.read_latest() is not None:
            raise GenesisAlreadyExistsError(
                "Chain already has entries; refusing to write a second genesis."
            )

        payload = {
            **project_meta,
            "salt_version": self.salter.current_version,
        }
        payload_hash = Hasher.hash_canonical(payload)
        signature = self.signer.sign(
            payload_hash, GENESIS_PREVIOUS_HASH, self.salter.current_version
        )

        entry = ChainEntry(
            sequence_id=0,
            recorded_at=datetime.now(timezone.utc),
            author_identity=author_identity,
            operation=ChainOperation.GENESIS,
            target_table=None,
            target_row_id=None,
            target_payload_hash=payload_hash,
            previous_hash=GENESIS_PREVIOUS_HASH,
            row_signature=signature,
            salt_version=self.salter.current_version,
        )
        self.store.append(entry)
        if self.anchor:
            self.anchor.write(entry)
        return entry


# -----------------------------------------------------------------------------
# Daily seal
# -----------------------------------------------------------------------------

class DailySealer:
    """Computes a Merkle root over a day's chain rows and writes a CHECKPOINT
    entry whose target_payload_hash commits to that root.
    """

    def __init__(
        self, store: ChainStore, writer: ChainWriter,
    ) -> None:
        self.store = store
        self.writer = writer

    def seal_day(
        self, target_date: date, author_identity: str = DEFAULT_AUTHOR,
    ) -> ChainEntry | None:
        all_entries = self.store.read_all_sorted()

        day_entries = [
            e for e in all_entries
            if e.recorded_at.date() == target_date
            and e.operation != ChainOperation.CHECKPOINT
        ]
        if not day_entries:
            return None

        signatures = [e.row_signature for e in day_entries]
        merkle_root = MerkleTreeBuilder.root(signatures)

        seal_payload = {
            "seal_date": target_date.isoformat(),
            "sequence_range": [
                day_entries[0].sequence_id,
                day_entries[-1].sequence_id,
            ],
            "merkle_root": merkle_root,
            "salt_version": self.writer.salter.current_version,
        }
        seal_payload_hash = Hasher.hash_canonical(seal_payload)

        # Idempotency: if a CHECKPOINT already exists whose target_payload_hash
        # matches what we'd compute now, return it instead of duplicating.
        # This is robust against retroactive seals (recorded_at.date may differ
        # from target_date) and crash-recovery scenarios on next-day startup.
        existing = next(
            (
                e for e in all_entries
                if e.operation == ChainOperation.CHECKPOINT
                and e.target_payload_hash == seal_payload_hash
            ),
            None,
        )
        if existing:
            return existing

        return self.writer.append_entry(
            operation=ChainOperation.CHECKPOINT,
            target_table=None,
            target_row_id=None,
            target_payload_hash=seal_payload_hash,
            author_identity=author_identity,
        )


# -----------------------------------------------------------------------------
# Auditor — walks the chain, pinpoints tamper sequence_id
# -----------------------------------------------------------------------------

class IntegrityVerdict(BaseModel):
    is_intact: bool
    chain_length: int
    last_verified_sequence_id: int
    first_tampered_sequence_id: int | None = None
    failure_reason: str | None = None


class IntegrityAuditor:
    def __init__(
        self,
        store: ChainStore,
        signer: Signer,
        anchor: GenesisAnchor | None = None,
    ) -> None:
        self.store = store
        self.signer = signer
        self.anchor = anchor

    def audit(self, entries: list[ChainEntry] | None = None) -> IntegrityVerdict:
        """Walk the chain and verify every entry. If `entries` is supplied,
        verify that list in-place (used by tests); otherwise read from store.
        """
        if entries is None:
            entries = self.store.read_all_sorted()

        if not entries:
            return IntegrityVerdict(
                is_intact=True, chain_length=0, last_verified_sequence_id=-1,
            )

        first = entries[0]

        # 1. Chain must start at sequence_id=0
        if first.sequence_id != 0:
            return IntegrityVerdict(
                is_intact=False, chain_length=len(entries),
                last_verified_sequence_id=-1,
                first_tampered_sequence_id=first.sequence_id,
                failure_reason=(
                    f"chain does not start at sequence_id=0 "
                    f"(first entry has sequence_id={first.sequence_id})"
                ),
            )

        # 2. Genesis previous_hash must be the all-zero sentinel
        if first.previous_hash != GENESIS_PREVIOUS_HASH:
            return IntegrityVerdict(
                is_intact=False, chain_length=len(entries),
                last_verified_sequence_id=-1,
                first_tampered_sequence_id=0,
                failure_reason="genesis previous_hash != all-zero sentinel",
            )

        # 3. Genesis must match external anchor if one exists
        if self.anchor:
            recorded = self.anchor.read()
            if recorded and recorded.get("row_signature") != first.row_signature:
                return IntegrityVerdict(
                    is_intact=False, chain_length=len(entries),
                    last_verified_sequence_id=-1,
                    first_tampered_sequence_id=0,
                    failure_reason=(
                        "genesis row_signature does not match external anchor "
                        f"({self.anchor.path}); chain has been rewritten"
                    ),
                )

        # 4. Walk every entry: monotonic seq, prev_hash linkage, signature recompute
        last_verified = -1
        prev_signature = GENESIS_PREVIOUS_HASH
        prev_seq = -1

        for entry in entries:
            if entry.sequence_id != prev_seq + 1:
                return IntegrityVerdict(
                    is_intact=False, chain_length=len(entries),
                    last_verified_sequence_id=last_verified,
                    first_tampered_sequence_id=entry.sequence_id,
                    failure_reason=(
                        f"non-monotonic sequence at recorded_at={entry.recorded_at.isoformat()}: "
                        f"expected sequence_id={prev_seq + 1}, got {entry.sequence_id}"
                    ),
                )

            if entry.previous_hash != prev_signature:
                return IntegrityVerdict(
                    is_intact=False, chain_length=len(entries),
                    last_verified_sequence_id=last_verified,
                    first_tampered_sequence_id=entry.sequence_id,
                    failure_reason=(
                        "previous_hash does not match prior row's row_signature; "
                        "chain link broken"
                    ),
                )

            try:
                recomputed = self.signer.sign(
                    entry.target_payload_hash,
                    entry.previous_hash,
                    entry.salt_version,
                )
            except NotImplementedError as e:
                return IntegrityVerdict(
                    is_intact=False, chain_length=len(entries),
                    last_verified_sequence_id=last_verified,
                    first_tampered_sequence_id=entry.sequence_id,
                    failure_reason=f"cannot verify (salt unavailable): {e}",
                )

            if recomputed != entry.row_signature:
                return IntegrityVerdict(
                    is_intact=False, chain_length=len(entries),
                    last_verified_sequence_id=last_verified,
                    first_tampered_sequence_id=entry.sequence_id,
                    failure_reason=(
                        f"row_signature mismatch at sequence_id={entry.sequence_id}: "
                        f"stored={entry.row_signature[:16]}... "
                        f"recomputed={recomputed[:16]}..."
                    ),
                )

            last_verified = entry.sequence_id
            prev_signature = entry.row_signature
            prev_seq = entry.sequence_id

        return IntegrityVerdict(
            is_intact=True,
            chain_length=len(entries),
            last_verified_sequence_id=last_verified,
        )


# -----------------------------------------------------------------------------
# Factory
# -----------------------------------------------------------------------------

def build_components(salt: str | None = None) -> tuple[ChainStore, ChainWriter, Signer, Salter, GenesisAnchor]:
    """Wire the full integrity-engine stack from env. Used by the CLI and by
    DatabricksWorker when SECRET_SALT is present.
    """
    salter = Salter(salt=salt)
    signer = Signer(salter)
    delta = DeltaTableWriter()
    store = ChainStore(delta)
    anchor = GenesisAnchor()
    writer = ChainWriter(store, signer, salter, anchor=anchor)
    return store, writer, signer, salter, anchor


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _cmd_init(args: argparse.Namespace) -> int:
    store, writer, signer, salter, anchor = build_components()
    initializer = GenesisInitializer(store, signer, salter, anchor=anchor)

    started_at = (
        args.started_at if args.started_at
        else datetime.now(timezone.utc).isoformat()
    )
    project_meta = {
        "project": args.project,
        "started_at": started_at,
        "agent_version": args.agent_version,
    }
    try:
        entry = initializer.write_genesis(project_meta)
    except GenesisAlreadyExistsError:
        print("genesis already exists; nothing to do", file=sys.stderr)
        return 0

    print(f"✓ genesis written")
    print(f"  sequence_id:         {entry.sequence_id}")
    print(f"  target_payload_hash: {entry.target_payload_hash}")
    print(f"  row_signature:       {entry.row_signature}")
    print(f"  anchor written to:   {anchor.path}")
    return 0


def _cmd_audit(_args: argparse.Namespace) -> int:
    store, _writer, signer, _salter, anchor = build_components()
    auditor = IntegrityAuditor(store, signer, anchor=anchor)
    verdict = auditor.audit()

    if verdict.is_intact:
        if verdict.chain_length == 0:
            print("✓ Chain is empty — no entries to verify yet")
        else:
            print(
                f"✓ Chain INTACT: {verdict.chain_length} entries verified up to "
                f"sequence_id={verdict.last_verified_sequence_id}"
            )
        return 0

    print(
        f"✗ TAMPER DETECTED at sequence_id={verdict.first_tampered_sequence_id}"
    )
    print(f"  Last verified: sequence_id={verdict.last_verified_sequence_id}")
    print(f"  Reason: {verdict.failure_reason}")
    return 1


def _cmd_seal(args: argparse.Namespace) -> int:
    store, writer, _signer, _salter, _anchor = build_components()
    sealer = DailySealer(store, writer)

    target_date = (
        date.fromisoformat(args.date) if args.date
        else datetime.now(timezone.utc).date()
    )
    result = sealer.seal_day(target_date)
    if result is None:
        print(f"no chain entries on {target_date.isoformat()}; nothing to seal")
        return 0

    if result.recorded_at.date() < target_date or result.recorded_at.date() > target_date:
        # idempotent re-seal returns the existing checkpoint
        print(f"checkpoint for {target_date.isoformat()} already exists at "
              f"sequence_id={result.sequence_id}")
    else:
        print(f"✓ daily seal written")
        print(f"  date:                {target_date.isoformat()}")
        print(f"  sequence_id:         {result.sequence_id}")
        print(f"  target_payload_hash: {result.target_payload_hash}")
        print(f"  row_signature:       {result.row_signature}")
    return 0


def _cmd_status(_args: argparse.Namespace) -> int:
    store, _writer, _signer, _salter, anchor = build_components()
    entries = store.read_all_sorted()
    print(f"chain length:    {len(entries)}")
    if entries:
        print(f"first sequence:  {entries[0].sequence_id}")
        print(f"last sequence:   {entries[-1].sequence_id}")
        print(f"genesis row:     {entries[0].row_signature[:32]}...")
        last_seal = next(
            (e for e in reversed(entries) if e.operation == ChainOperation.CHECKPOINT),
            None,
        )
        if last_seal:
            print(f"latest seal:     sequence_id={last_seal.sequence_id} "
                  f"date={last_seal.recorded_at.date().isoformat()}")
        else:
            print("latest seal:     (none yet)")
    rec = anchor.read()
    print(f"genesis anchor:  {anchor.path} {'present' if rec else 'absent'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="integrity_engine",
        description="Phase 8 cryptographic integrity engine for OpenClaw",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    init_p = sub.add_parser("init", help="Write the genesis block")
    init_p.add_argument("--project", required=True, help="e.g. openclaw-public-commons")
    init_p.add_argument("--started-at", help="ISO 8601 timestamp; defaults to now")
    init_p.add_argument("--agent-version", default="openclaw-v4.0")

    sub.add_parser("audit", help="Verify chain integrity")

    seal_p = sub.add_parser("seal", help="Compute and write the daily Merkle seal")
    seal_p.add_argument("--date", help="YYYY-MM-DD; defaults to today (UTC)")

    sub.add_parser("status", help="Show chain length, latest seal, anchor presence")

    args = parser.parse_args(argv)
    handlers = {
        "init": _cmd_init,
        "audit": _cmd_audit,
        "seal": _cmd_seal,
        "status": _cmd_status,
    }
    try:
        return handlers[args.cmd](args)
    except SaltUnavailableError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
