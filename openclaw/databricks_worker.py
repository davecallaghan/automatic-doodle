#!/usr/bin/env python3
"""
databricks_worker.py — Phase 7 worker for OpenClaw bronze + silver writes.

Writes research responses, source fetches, validated briefs, and citations to
Delta tables under /mnt/disks/research/delta/research/{bronze,silver}/...

Phase 7 scope:
  - Runs standalone on the VM host (not yet wired into the openclaw container).
  - Tables are auto-created by deltalake on first write.
  - The chain hook is a STUB — Phase 8 (integrity engine) replaces it with
    a real append to research.audit.integrity_chain.

Library + CLI in one module. Run as:
    python3 databricks_worker.py {record-response|record-fetch|record-brief
                                  |drain-buffer|read-recent}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel, Field, model_validator

# Heavy native deps — guarded so the rest of the module (Pydantic models,
# hashing, local buffer) is importable without them. Required for any actual
# Delta read/write; install via openclaw/requirements.txt on the target VM.
try:
    import pyarrow as pa
    from deltalake import DeltaTable, write_deltalake
    _DELTA_AVAILABLE = True
except ImportError:
    pa = None  # type: ignore[assignment]
    DeltaTable = None  # type: ignore[assignment]
    write_deltalake = None  # type: ignore[assignment]
    _DELTA_AVAILABLE = False


# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

DELTA_ROOT = Path(os.environ.get("DELTA_ROOT", "/mnt/disks/research/delta"))
LOCAL_BUFFER_ROOT = Path(os.environ.get(
    "LOCAL_BUFFER_ROOT", "/mnt/disks/research/workspace/research_logs"
))
CATALOG = "research"


# -----------------------------------------------------------------------------
# Canonical JSON — deterministic encoding for hashing
# -----------------------------------------------------------------------------

class CanonicalJSON:
    """Deterministic JSON serialization. Phase 8 reuses this exact behavior
    for the integrity chain — keep the rules stable.
    """

    @staticmethod
    def encode(payload: Any) -> str:
        return json.dumps(
            payload,
            default=CanonicalJSON._default,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @staticmethod
    def _default(obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        if isinstance(obj, date):
            return obj.isoformat()
        if isinstance(obj, uuid.UUID):
            return str(obj)
        raise TypeError(f"Type {type(obj).__name__} is not JSON-serializable")


# -----------------------------------------------------------------------------
# Hashing
# -----------------------------------------------------------------------------

class Hasher:
    """SHA-256, with streaming support for large payloads."""

    @staticmethod
    def hash_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def hash_string(s: str) -> str:
        return hashlib.sha256(s.encode("utf-8")).hexdigest()

    @staticmethod
    def hash_canonical(payload: Any) -> str:
        return Hasher.hash_string(CanonicalJSON.encode(payload))

    @staticmethod
    def hash_file_streaming(path: Path, chunk_size: int = 65536) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            while chunk := f.read(chunk_size):
                h.update(chunk)
        return h.hexdigest()


# -----------------------------------------------------------------------------
# Pydantic models — must mirror unity_catalog_setup.sql
# -----------------------------------------------------------------------------

class RawResponse(BaseModel):
    """research.bronze.raw_responses"""
    # `model_id` collides with Pydantic v2's `model_` protected namespace; opt out.
    model_config = {"protected_namespaces": ()}

    response_id: str
    created_at: datetime
    agent_version: str
    model_id: str
    prompt_hash: str
    response_text: str
    response_hash: str
    mlflow_run_id: str
    topic_id: str | None = None


class SourceFetch(BaseModel):
    """research.bronze.source_fetches"""
    fetch_id: str
    response_id: str
    fetched_at: datetime
    url: str
    http_status: int
    body_hash: str
    body_bytes: int | None = None
    content_type: str | None = None
    used_in_brief: bool


class ValidatedBrief(BaseModel):
    """research.silver.validated_briefs"""
    brief_id: str
    response_id: str
    created_at: datetime
    topic_id: str
    title: str
    key_findings: str
    # Schema CHECK constraint: counter_arguments length >= 100. Mirror it here.
    counter_arguments: str = Field(min_length=100)
    practical_implications: str
    further_reading: str
    known_gaps: str | None = None
    content_hash: str
    agent_version: str


class Citation(BaseModel):
    """research.silver.citations"""
    citation_id: str
    brief_id: str
    fetch_id: str | None = None
    url: str
    title: str
    authority_tier: int = Field(ge=1, le=4)
    vendor_org: str | None = None
    publication_date: date | None = None
    is_paid_content: bool
    t4_justification: str | None = None

    @model_validator(mode="after")
    def _t4_requires_justification(self) -> "Citation":
        if self.authority_tier == 4 and not self.t4_justification:
            raise ValueError("authority_tier=4 requires a non-empty t4_justification")
        return self


# -----------------------------------------------------------------------------
# Local buffer — JSONL failsafe when Delta writes fail
# -----------------------------------------------------------------------------

class LocalBuffer:
    """Writes failed Delta operations to JSONL files for retry."""

    def __init__(self, root: Path = LOCAL_BUFFER_ROOT) -> None:
        # Lazy mkdir — only created on first append. Lets tests instantiate
        # cheaply with the default path even where /mnt isn't writable.
        self.root = root

    def _path_for(self, table: str, when: datetime | None = None) -> Path:
        when = when or datetime.now(timezone.utc)
        d = self.root / when.strftime("%Y-%m-%d")
        d.mkdir(parents=True, exist_ok=True)
        safe = table.replace("/", "_").replace(".", "__")
        return d / f"{safe}.jsonl"

    def append(self, table: str, payload: dict[str, Any]) -> Path:
        path = self._path_for(table)
        with path.open("a", encoding="utf-8") as f:
            f.write(CanonicalJSON.encode(payload) + "\n")
        return path

    def iter_pending(self) -> Iterator[tuple[Path, str, dict[str, Any]]]:
        """Yield (file, table_name, payload) for every line in every buffer file."""
        if not self.root.exists():
            return
        for day_dir in sorted(self.root.iterdir()):
            if not day_dir.is_dir():
                continue
            for jsonl in sorted(day_dir.glob("*.jsonl")):
                table = jsonl.stem.replace("__", ".", 1)
                with jsonl.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        yield jsonl, table, json.loads(line)


# -----------------------------------------------------------------------------
# Delta writer — thin wrapper over deltalake
# -----------------------------------------------------------------------------

class DeltaTableWriter:
    """Writes Delta tables under <root>/<catalog>/<schema>/<table>/. Tables
    are auto-created by deltalake on first write.
    """

    def __init__(self, root: Path = DELTA_ROOT, catalog: str = CATALOG) -> None:
        self.root = root
        self.catalog = catalog

    def path_for(self, schema: str, table: str) -> str:
        return str(self.root / self.catalog / schema / table)

    def append(self, schema: str, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        if not _DELTA_AVAILABLE:
            raise RuntimeError(
                "deltalake/pyarrow not installed. Run: pip install -r openclaw/requirements.txt"
            )
        path = self.path_for(schema, table)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        pa_table = pa.Table.from_pylist(rows)
        write_deltalake(path, pa_table, mode="append")

    def read_recent(self, schema: str, table: str, limit: int = 10) -> list[dict[str, Any]]:
        if not _DELTA_AVAILABLE:
            raise RuntimeError(
                "deltalake/pyarrow not installed. Run: pip install -r openclaw/requirements.txt"
            )
        path = self.path_for(schema, table)
        if not Path(path).exists():
            return []
        dt = DeltaTable(path)
        sliced = dt.to_pyarrow_table().slice(0, limit)
        return sliced.to_pylist()


# -----------------------------------------------------------------------------
# Worker orchestrator
# -----------------------------------------------------------------------------

class DatabricksWorker:
    def __init__(
        self,
        delta_writer: DeltaTableWriter | None = None,
        local_buffer: LocalBuffer | None = None,
    ) -> None:
        self.delta = delta_writer or DeltaTableWriter()
        self.buffer = local_buffer or LocalBuffer()

    def record_raw_response(self, resp: RawResponse) -> None:
        self._write("bronze", "raw_responses", resp.model_dump(), row_id=resp.response_id)

    def record_source_fetch(self, fetch: SourceFetch) -> None:
        self._write("bronze", "source_fetches", fetch.model_dump(), row_id=fetch.fetch_id)

    def record_validated_brief(
        self, brief: ValidatedBrief, citations: list[Citation]
    ) -> None:
        self._write("silver", "validated_briefs", brief.model_dump(), row_id=brief.brief_id)
        for c in citations:
            self._write("silver", "citations", c.model_dump(), row_id=c.citation_id)

    def drain_buffer(self) -> int:
        """Replay buffered writes against Delta. Returns count drained."""
        drained = 0
        for _path, table, payload in list(self.buffer.iter_pending()):
            try:
                schema, tname = table.split(".", 1)
                self.delta.append(schema, tname, [payload])
                row_id = payload.get("response_id") or payload.get("brief_id") \
                    or payload.get("fetch_id") or payload.get("citation_id") or "?"
                self._chain_hook(
                    "INSERT",
                    f"{self.delta.catalog}.{table}",
                    row_id,
                    Hasher.hash_canonical(payload),
                )
                drained += 1
            except Exception as e:
                print(f"WARN: drain failed for {table}: {e}", file=sys.stderr)
        return drained

    def _write(self, schema: str, table: str, payload: dict[str, Any], row_id: str) -> None:
        full_table = f"{schema}.{table}"
        payload_hash = Hasher.hash_canonical(payload)
        try:
            self.delta.append(schema, table, [payload])
            self._chain_hook(
                "INSERT", f"{self.delta.catalog}.{full_table}", row_id, payload_hash
            )
        except Exception as e:
            print(
                f"WARN: Delta write failed for {full_table} row={row_id}: {e}",
                file=sys.stderr,
            )
            print(
                f"      buffering to {self.buffer._path_for(full_table)}",
                file=sys.stderr,
            )
            self.buffer.append(full_table, payload)

    def _chain_hook(
        self, operation: str, table: str, row_id: str, payload_hash: str
    ) -> None:
        """Phase 7 STUB. Phase 8 will replace this with a real append to
        research.audit.integrity_chain. For now, log the intent so deployment
        validation can confirm the right hook firings happened.
        """
        sys.stderr.write(
            f"CHAIN_HOOK operation={operation} table={table} "
            f"row_id={row_id} payload_hash={payload_hash}\n"
        )


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _read_stdin_json() -> Any:
    try:
        return json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON on stdin: {e}", file=sys.stderr)
        sys.exit(2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="databricks_worker",
        description="Phase 7 OpenClaw worker — Delta writes for bronze + silver",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("record-response", help="Read RawResponse JSON from stdin")
    sub.add_parser("record-fetch", help="Read SourceFetch JSON from stdin")
    sub.add_parser(
        "record-brief",
        help='Read {"brief":{}, "citations":[]} JSON from stdin',
    )
    sub.add_parser("drain-buffer", help="Replay buffered writes against Delta")
    read = sub.add_parser("read-recent", help="Read recent rows from a table")
    read.add_argument("--table", required=True, help="e.g. bronze.raw_responses")
    read.add_argument("--limit", type=int, default=5)

    args = parser.parse_args(argv)
    worker = DatabricksWorker()

    if args.cmd == "record-response":
        worker.record_raw_response(RawResponse.model_validate(_read_stdin_json()))
        return 0
    if args.cmd == "record-fetch":
        worker.record_source_fetch(SourceFetch.model_validate(_read_stdin_json()))
        return 0
    if args.cmd == "record-brief":
        payload = _read_stdin_json()
        brief = ValidatedBrief.model_validate(payload["brief"])
        citations = [Citation.model_validate(c) for c in payload.get("citations", [])]
        worker.record_validated_brief(brief, citations)
        return 0
    if args.cmd == "drain-buffer":
        n = worker.drain_buffer()
        print(f"drained {n} rows")
        return 0
    if args.cmd == "read-recent":
        schema, tname = args.table.split(".", 1)
        rows = worker.delta.read_recent(schema, tname, args.limit)
        print(json.dumps(rows, indent=2, default=str))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
