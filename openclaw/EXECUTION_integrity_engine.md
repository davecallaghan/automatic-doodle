# Execution Plan: Cryptographic Integrity Engine

**Companion to:** `EXECUTION_databricks_integration.md` and `EXECUTION_public_commons.md`
**Branch:** `databricks-integration`

---

## Why This Layer Exists

The base plan (`EXECUTION_databricks_integration.md`) already gives every research brief a `content_hash` column — SHA-256 of the brief's content. That's *row-local* tamper detection: alter the brief text, the hash no longer matches, the change is caught.

But row-local hashing has three blind spots:

1. **Deletion.** Drop a row entirely; nothing flags it. The remaining rows still verify.
2. **Reordering.** Re-sequence history; row hashes still match individually, but the timeline is fiction.
3. **Insertion.** Add a row dated last week; its individual hash is valid; nothing connects it to actual history.

A **linked-hash chain** closes all three. Every row's signature includes the prior row's signature, so any historical edit, deletion, or insertion invalidates every signature downstream — and the *exact* tamper point is identifiable.

Combined with **daily Merkle seals** (a single hash committing to all of that day's signatures, optionally published externally), the chain becomes externally auditable: a third party with only the published seal can verify that the agent's history hasn't been retroactively rewritten — without trusting our infrastructure.

This is the technical implementation of the Virtue Protocol's "Epistemic Accountability" principle. The system proves mathematically that research wasn't post-facto edited to fit a different narrative.

---

## The Chain Model

### Genesis Block

The first entry of the chain. Deterministic, publishable, sets the project's cryptographic origin.

```
sequence_id        = 0
operation          = 'GENESIS'
target_payload     = canonical_json({
                       project: "openclaw-public-commons",
                       started_at: "<ISO timestamp>",
                       agent_version: "openclaw-v4.0",
                       salt_version: 1
                     })
target_payload_hash = SHA-256(target_payload)
previous_hash      = "0" * 64                        -- sentinel, all zeros
row_signature      = SHA-256(target_payload_hash || previous_hash || SECRET_SALT)
```

One genesis per project (per Unity Catalog catalog). The genesis row's `target_payload_hash` is **public** — it's published in the project's README so anyone can verify the chain starts where we say it does.

### Linked Entry

Every state-changing operation in the system. Each links to the previous entry by including its signature.

```
sequence_id        = N (monotonic)
operation          = 'INSERT' | 'UPDATE' | 'DELETE'
target_table       = 'research.gold.research_summaries' (e.g.)
target_row_id      = '<UUID of the affected row>'
target_payload     = canonical_json(<full state of the target row after the operation>)
target_payload_hash = SHA-256(target_payload)              -- streaming for large payloads
previous_hash      = <row_signature of sequence_id N-1>
row_signature      = SHA-256(target_payload_hash || previous_hash || SECRET_SALT)
```

The chain is global across all tables — there is one canonical sequence covering every write to bronze, silver, gold, public_archive, or audit.

### Daily Seal (Checkpoint)

At the end of every research day, a single Merkle root commits to all of that day's signatures.

```
sequence_id        = M
operation          = 'CHECKPOINT'
target_table       = NULL
target_row_id      = NULL
target_payload     = canonical_json({
                       seal_date: "2026-05-04",
                       sequence_range: [first_seq_of_day, last_seq_of_day],
                       merkle_root: "<hex>",
                       salt_version: 1
                     })
target_payload_hash = SHA-256(target_payload)
previous_hash      = <row_signature of sequence_id M-1>
row_signature      = SHA-256(target_payload_hash || previous_hash || SECRET_SALT)
```

The Merkle root is computed over the `row_signature` values of every chain row from that day (sorted by `sequence_id`). The checkpoint's own `row_signature` is then the seal value for that day.

The seal value is **published externally** (see "Public Verifiability" below). Once published, anyone holding the seal can detect retroactive edits to that day's chain — without access to `SECRET_SALT`.

---

## Schedule Alignment with Existing Infrastructure

The Cryptographer's brief specifies **8 PM EDT** for the daily seal. The existing infrastructure auto-stops the VM at **5 PM EDT** to control cost. Three options:

| Option | Mechanism | Tradeoff |
|---|---|---|
| **A — 4:55 PM EDT seal** (recommended) | Final scheduled action of the research day; agent computes and writes the checkpoint as its last operation before VM stop | Aligns with existing schedule; zero extra cost; "research day" closes 3h earlier than spec but matches actual operating window |
| B — 8 PM EDT, VM extended | Schedule changes to keep VM running until 8 PM | +$3.20/month compute; honors literal spec |
| C — 8 PM EDT, Cloud Function | Tiny Cloud Function fires at 8 PM independent of VM; reads Delta state from persistent disk via a service account | More moving parts, ~$0/month; honors literal spec but introduces a second compute path |

**Recommendation: Option A.** The 8 PM time was the spec author's framing, but in this system the research day genuinely ends at 5 PM. The seal sealing "today's actual research" is more meaningful than the seal sealing "an arbitrary cutoff time." If you'd rather honor the literal spec, Option C is the cleanest add — note the operational burden and pick deliberately.

---

## Schema

The chain replaces `research.audit.write_log` from the base plan. One canonical audit ledger, with full integrity properties.

```sql
CREATE TABLE research.audit.integrity_chain (
  sequence_id          BIGINT NOT NULL,
  recorded_at          TIMESTAMP NOT NULL,
  author_identity      STRING NOT NULL,           -- UC principal that wrote
  operation            STRING NOT NULL,           -- GENESIS|INSERT|UPDATE|DELETE|CHECKPOINT
  target_table         STRING,                    -- NULL for GENESIS, CHECKPOINT
  target_row_id        STRING,                    -- NULL for GENESIS, CHECKPOINT
  target_payload_hash  STRING NOT NULL,           -- SHA-256 of canonical payload
  previous_hash        STRING NOT NULL,           -- prior row's row_signature
  row_signature        STRING NOT NULL,           -- chain link
  salt_version         INT NOT NULL,
  CONSTRAINT operation_values CHECK (operation IN
    ('GENESIS', 'INSERT', 'UPDATE', 'DELETE', 'CHECKPOINT')),
  CONSTRAINT sequence_monotonic CHECK (sequence_id >= 0),
  CONSTRAINT salt_version_positive CHECK (salt_version >= 1)
) USING DELTA
  PARTITIONED BY (DATE(recorded_at));
```

`PARTITIONED BY (DATE)` keeps daily seal computations cheap — one day's worth of rows is one partition.

The chain is **append-only by policy**. UC grants on this table are INSERT-only for the agent service principal; no UPDATE, no DELETE, ever. Schema enforcement plus permission enforcement.

---

## Pydantic Model

Per the Cryptographer's spec, with the same field names mapped to OSS-realistic types:

```python
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class ChainOperation(str, Enum):
    GENESIS = "GENESIS"
    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    CHECKPOINT = "CHECKPOINT"


class ChainEntry(BaseModel):
    sequence_id: int = Field(ge=0)
    recorded_at: datetime
    author_identity: str = Field(min_length=1)
    operation: ChainOperation
    target_table: Optional[str] = None
    target_row_id: Optional[str] = None
    target_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    row_signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    salt_version: int = Field(ge=1)

    @field_validator("target_table", "target_row_id")
    @classmethod
    def genesis_and_checkpoint_have_no_target(cls, v, info):
        op = info.data.get("operation")
        if op in (ChainOperation.GENESIS, ChainOperation.CHECKPOINT) and v is not None:
            raise ValueError(f"{op.value} entries must not have target_table or target_row_id")
        if op in (ChainOperation.INSERT, ChainOperation.UPDATE, ChainOperation.DELETE) and v is None:
            raise ValueError(f"{op.value} entries must specify target_table and target_row_id")
        return v
```

The Cryptographer's brief named the model `ResearchEntry` with fields `sequence_id, timestamp, content, author_identity, previous_hash, row_signature`. This `ChainEntry` is the same idea generalized — the audit ledger covers all writes, not just research entries. The original "ResearchEntry" maps to a row in `research.gold.research_summaries`, and that row's full state is what gets hashed and recorded as the chain entry's `target_payload_hash`. The chain is one level of abstraction above the domain models.

---

## Core Module: `openclaw/integrity_engine.py`

```python
"""
Cryptographic integrity engine for the OpenClaw research agent.

Maintains an append-only, linked-hash chain of every state-changing operation
in the research datastore. Provides daily Merkle seals and a standalone
verification utility that pinpoints the exact sequence_id of any tamper event.
"""

import hashlib
import json
import os
from datetime import datetime, date, timezone
from typing import Iterable
# ... (full implementation follows the structure below)
```

### Class structure

| Class | Responsibility |
|---|---|
| `CanonicalJSON` | Deterministic JSON serialization — `json.dumps(obj, sort_keys=True, separators=(",",":"))` plus type discipline (datetimes as ISO 8601 UTC, no NaN, no -0.0) |
| `Hasher` | SHA-256 wrapper. Streaming `update()` interface for large payloads; `finalize_hex()` returns lowercase hex |
| `Salter` | Loads `SECRET_SALT` from env. Maintains `salt_version` mapping (in `.secrets/.env-salts.json`, chmod 600). For v1: only version 1 supported. Salt rotation deferred. |
| `Signer` | Produces `row_signature = SHA-256(payload_hash \|\| previous_hash \|\| salt)`. Single-purpose, easy to unit-test against known vectors |
| `ChainWriter` | Atomic verify-then-insert: read latest chain row, compute new entry, attempt append; on optimistic-concurrency conflict, retry from read step. Backed by `delta-rs`. |
| `GenesisInitializer` | One-shot routine that writes `sequence_id = 0`. Refuses to run if any chain entries exist. Stores genesis payload hash in `.secrets/genesis.json` for permanent reference. |
| `MerkleTreeBuilder` | Pure function: takes a sorted list of hex hashes, returns the Merkle root hex. Standard binary tree, duplicate-last-leaf padding for odd counts. |
| `DailySealer` | Reads all `INSERT/UPDATE/DELETE` chain entries for a given date, builds Merkle tree over their `row_signature` values, writes the `CHECKPOINT` entry. Idempotent — second invocation on same date is a no-op. |
| `IntegrityAuditor` | The verification utility. Walks the chain from genesis, recomputes every signature, returns the first failing `sequence_id` or `None` if intact. Yields progress so it can verify large chains. |

### Atomic Verify-then-Insert (OSS path)

The Cryptographer's brief says `databricks-sql-python`. Our OSS path uses `delta-rs`. The semantic ("read latest, verify, insert in a single atomic step or retry") translates as follows:

```python
def append_chain_entry(self, partial_entry: dict) -> ChainEntry:
    """Read last entry, link, attempt append with optimistic concurrency."""
    for attempt in range(self.MAX_RETRIES):
        latest = self._read_latest_entry()  # Delta read at current version
        next_seq = (latest.sequence_id + 1) if latest else 0
        prev_hash = latest.row_signature if latest else "0" * 64

        entry = self._build_signed_entry(partial_entry, next_seq, prev_hash)

        try:
            self._delta_append_with_version_check(entry, expected_version=latest.delta_version)
            return entry
        except DeltaConcurrencyError:
            continue  # another writer beat us; retry from read
    raise IntegrityWriteFailure("max retries exceeded")
```

`_delta_append_with_version_check` uses delta-rs's `DeltaTable.create_checkpoint` + transactional write semantics. delta-rs has supported optimistic concurrency for appends since 0.13; verify the current release's exact API before implementation. If the API surface is still flux, fallback path is to issue the append via Unity Catalog's SQL endpoint (`INSERT INTO ... WHERE NOT EXISTS`-style guard with explicit transaction).

### Streaming hashes for large payloads

Source PDFs and long research artifacts can be megabytes. Loading them into memory just to hash is wasteful and bounded by container memory. The `Hasher` interface is streaming-first:

```python
class Hasher:
    def __init__(self):
        self._h = hashlib.sha256()

    def update(self, chunk: bytes) -> None:
        self._h.update(chunk)

    @classmethod
    def hash_bytes(cls, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @classmethod
    def hash_file_streaming(cls, path: str, chunk_size: int = 65536) -> str:
        h = cls()
        with open(path, "rb") as f:
            while chunk := f.read(chunk_size):
                h.update(chunk)
        return h._h.hexdigest()

    @classmethod
    def hash_canonical_json(cls, payload: dict) -> str:
        # Canonical JSON is bounded for typical research entries;
        # for huge payloads, hash the source artifact separately and
        # include only the artifact_hash in the canonical payload.
        return cls.hash_bytes(CanonicalJSON.encode(payload).encode("utf-8"))
```

Convention: any payload > 1 MB is **not** stored inline in the canonical JSON. Instead, the artifact is stored separately (on persistent disk under `/mnt/disks/research/artifacts/<artifact_hash>`), hashed via `hash_file_streaming`, and only its hash appears in the canonical payload. This keeps `target_payload_hash` computable without ever loading the full artifact into memory.

---

## Validation Utility: `audit_trail_integrity_check()`

Standalone, runs against a live Delta table. Produces a verifiable report.

```python
class IntegrityVerdict(BaseModel):
    is_intact: bool
    chain_length: int
    last_verified_sequence_id: int
    first_tampered_sequence_id: Optional[int] = None
    failure_reason: Optional[str] = None
    daily_seals_verified: int
    daily_seals_failed: list[date]


def audit_trail_integrity_check(
    chain_table: str = "research.audit.integrity_chain",
    expected_genesis_hash: str = None,  # from .secrets/genesis.json
) -> IntegrityVerdict:
    """
    Walk the chain from sequence_id=0, recompute every signature, return
    the first sequence_id where the chain breaks (or None if fully intact).

    Verification per row:
      1. Re-read target row from operational table (if INSERT/UPDATE)
      2. Re-canonicalize, recompute target_payload_hash
      3. Recompute row_signature using stored previous_hash + salt by version
      4. Compare against stored row_signature

    Verification of daily seals:
      1. For each CHECKPOINT entry, gather all chain rows from that date
      2. Recompute the Merkle root
      3. Compare against the seal's target_payload_hash
    """
```

Report format (CLI-friendly):

```
$ python -m openclaw.integrity audit
Integrity check started at 2026-05-15T12:00:00Z
Genesis verified: ✓ (hash matches .secrets/genesis.json)
Walking 4,832 chain entries...
  [████████████████████] 100%
Chain status: INTACT
Daily seals verified: 89 / 89
Last verified sequence_id: 4831
Last verified seal date: 2026-05-14

✓ Audit trail is cryptographically intact.
```

Or, on failure:

```
$ python -m openclaw.integrity audit
Integrity check started at 2026-05-15T12:00:00Z
Genesis verified: ✓
Walking 4,832 chain entries...
  [██████░░░░░░░░░░░░░░] 32%

✗ TAMPER DETECTED at sequence_id = 1547
  Recorded operation: UPDATE on research.gold.research_summaries
  Recorded target_row_id: 7e2f-...
  Failure: target_payload_hash mismatch
    Stored:    a3f2c1...
    Recomputed: 8b91d4...
  Implication: the row at research.gold.research_summaries with id=7e2f-...
               has been modified since this chain entry was written.
  All entries from sequence_id=1547 onward have unverifiable lineage.
```

The exact-sequence-id identification is the brief's core requirement and is what makes this useful in practice — you find the tamper, you read the chain entry's `recorded_at` and `author_identity`, and you have the forensic starting point.

---

## Public Verifiability — The External Anchor

A chain that only verifies against itself isn't externally trustworthy. The trust comes from publishing the daily seal somewhere we cannot retroactively edit.

### Surface for daily seals

After each daily checkpoint is written, the seal value (the checkpoint's `row_signature`) is appended to a public log at:

```
github.com/davecallaghan/openclaw-public-commons/SEALS.md
```

One line per day:
```
2026-05-14  seq=4791..4831  seal=8b91d4f3a2c1...  salt_v=1
```

This file is committed and pushed by the bidirectional GitHub publisher (Phase 6) on the same nightly run. Once it's in git history, the seal can't be silently changed — git's own hash chain catches that.

### What this proves

A third party who downloads `SEALS.md` and the chain (via the Delta Sharing endpoint, which exposes a `published_chain_seals` view) can:

1. Verify each day's seal by recomputing the Merkle root over that day's chain rows
2. Confirm the seal matches what was published in `SEALS.md` at the time
3. Confirm `SEALS.md` itself wasn't recently rewritten by checking the git commit history

If any past chain entry has been altered, the recomputed Merkle root will differ from the published seal — and the public seal log makes that detectable without access to `SECRET_SALT` or our infrastructure.

### Optional further hardening (not blocking)

- Publish daily seals to a public timestamping service (OpenTimestamps, RFC 3161) — Bitcoin-anchored timestamps for stronger external proof.
- Cross-publish seals to a second venue (e.g., a public Mastodon post, an IPFS pin) so a GitHub takedown doesn't erase the verifiable history.

These are upgrades. The GitHub-anchored approach is sufficient for v1.

---

## Permission Model Update

Extending the Phase 4 + Phase 6 permission tables:

| Principal | Capability on `audit.integrity_chain` |
|---|---|
| `openclaw-agent` | INSERT only. Cannot UPDATE, DELETE, or read entries other than `latest()` for chain linking. |
| `david` | SELECT only. No write access — humans cannot rewrite history either, by design. |
| `delta-share-server` | SELECT only on the `published_chain_seals` view (not the full chain). |
| Public via Delta Sharing | SELECT on `published_chain_seals` view. |

`SECRET_SALT` is **not** required to verify the chain's *external consistency* against the seal log — that verification uses only public data (chain row signatures + published seals). `SECRET_SALT` is required to verify any *individual* entry's signature against its constituent payload hash. Both are valuable; the public verification is the stronger property.

---

## Build Phases

This work slots in as Phase 2.5 of the master plan — between bronze/silver writes (Phase 2) and the fairness scorecard (Phase 3). The chain must exist before any other table writes, because every other write generates a chain entry.

### Phase 2.5a — Genesis & core chain (~2 days)

1. `openclaw/integrity_engine.py` — `CanonicalJSON`, `Hasher`, `Salter`, `Signer`, `ChainWriter` classes.
2. `openclaw/unity_catalog_setup.sql` — `CREATE TABLE research.audit.integrity_chain` (and remove the old `audit.write_log`).
3. UC grants: `openclaw-agent` gets INSERT only; nobody else gets write.
4. `openclaw/integrity init` CLI command — runs `GenesisInitializer`, writes sequence_id=0, stores genesis hash in `.secrets/genesis.json`.
5. **Validation:** can write a no-op INSERT entry; cannot UPDATE; cannot run init twice.

### Phase 2.5b — Chain integration in the worker (~2 days)

1. Wire `ChainWriter.append()` into every Delta write in `databricks_worker.py`. Each operational write happens in two phases: write to the operational table, then append the chain entry referencing the new row's hash. (Or vice versa with rollback semantics — pick based on delta-rs transaction support.)
2. Failure handling: if the chain append fails, the operational write is rolled back. Atomicity of the pair is the integrity invariant.
3. **Validation:** simulate a chain-write failure; confirm the operational row does not appear; confirm error path is logged and surfaced.

### Phase 2.5c — Daily seal (~2 days)

1. `MerkleTreeBuilder` and `DailySealer` classes.
2. Schedule the seal as the agent's last action of the research day (~4:55 PM EDT). Implementation: cron entry inside the container or a final-step in the agent's main loop.
3. The seal append uses the same `ChainWriter` — it's a CHECKPOINT chain entry like any other.
4. **Validation:** ten synthetic days of chain entries; run sealer for each day; verify Merkle root recomputable independently.

### Phase 2.5d — Audit utility (~2 days)

1. `IntegrityAuditor.audit_trail_integrity_check()`.
2. CLI: `python -m openclaw.integrity audit [--from SEQ] [--to SEQ]`.
3. **Validation:** corrupt a known chain entry (in a test instance); confirm the auditor flags the exact sequence_id; confirm intact chains report intact.

### Phase 2.5e — Public seal log (~1 day, slots into Phase 6)

1. Bidirectional GitHub publisher pushes `SEALS.md` updates daily.
2. `published_chain_seals` view added to Delta Share.
3. **Validation:** download seal log + share endpoint as an external observer; verify a known-intact chain externally.

---

## Adversarial Validation Suite (additions to Phase 5)

Extending the master plan's Phase 5 list:

1. **Tamper test — content edit.** Modify a row in `research.gold.research_summaries` directly via Delta. Run audit. Expect: exact sequence_id of the corresponding chain entry flagged.
2. **Tamper test — row deletion.** Delete a row from `research.silver.validated_briefs` directly. Run audit. Expect: chain entry references a now-missing target row; flagged with clear error.
3. **Tamper test — chain entry edit.** Attempt to UPDATE a row in `audit.integrity_chain`. Expect: UC permission denied at the agent and human principal; even if forced via direct Delta write, audit recomputes signatures and flags the modified row.
4. **Tamper test — chain entry deletion.** Same as above for DELETE.
5. **Tamper test — sequence reorder.** Change `sequence_id` values to swap two entries' order. Expect: `previous_hash` linkage broken; audit flags the first reordered entry.
6. **Tamper test — daily seal forge.** Modify a CHECKPOINT entry's `target_payload_hash`. Expect: recomputed Merkle root doesn't match; audit flags the seal.
7. **Public seal verification.** External observer with only `SEALS.md` and the share endpoint can detect the above tampers (those covering past days' data) without `SECRET_SALT`.
8. **Genesis tamper.** Modify the genesis row. Expect: stored genesis hash in `.secrets/genesis.json` doesn't match; audit refuses to start, surfaces the genesis mismatch.
9. **Salt unavailable.** Run audit with `SECRET_SALT` env var unset. Expect: clean error; auditor distinguishes "I cannot verify" from "I verified and it failed."
10. **Replay attack.** Submit the same canonical payload twice. Expect: each gets a unique chain entry with a different `sequence_id` and `previous_hash`; chain remains valid; audit passes.

---

## Open Risks Worth Flagging

1. **delta-rs concurrency semantics.** The chain's atomicity depends on optimistic concurrency control behaving correctly under contention. Under the current single-agent design, there's effectively one writer, so contention is rare — but worth verifying the exact API behavior before relying on it. Fallback path (UC SQL endpoint with explicit transactions) is documented but slower.

2. **Rebuild cost on long chains.** Verifying a chain of 100K entries means 100K hash recomputations. At ~1µs per SHA-256, that's still sub-second — not a real concern for years. But: if any audit must be done over a multi-million-row chain, partition-aware verification (verify partition-by-day, only re-verify partitions that changed) is the upgrade path.

3. **Salt rotation.** v1 supports salt_version=1 only. Designing salt rotation correctly requires a salt registry, careful re-verification semantics for old entries, and a clear rotation policy. Deferred to a future phase. Document loudly that the current SECRET_SALT must not be lost or rotated until rotation is properly designed.

4. **Privacy of the salt.** If `SECRET_SALT` leaks, an attacker can forge plausible-looking chain entries (compute valid signatures). They still cannot retroactively forge a seal that's already published, but they can write valid-looking entries going forward. Mitigation: same as any secret — env var only, never logged, never in git. Compromise response: rotate salt (when supported) and publicly note the salt-version transition with the date.

5. **Operational complexity.** This adds three new maintenance burdens: the salt secret, the genesis file, and the audit utility itself. Document each. The audit utility should run on a schedule (weekly?) so a tamper is detected early, not at the next manual review.

---

## Summary in One Paragraph

Every state-changing write in the system goes through `audit.integrity_chain` as an append-only entry. Each entry's `row_signature` includes the prior entry's signature, so any historical edit, deletion, insertion, or reorder is detectable by walking the chain. At the end of each research day, a Merkle root over that day's signatures is committed as a CHECKPOINT entry, and the seal value is published to a public GitHub file. A third party with only the public seal log and the publicly-shared chain-seal view can detect any retroactive rewrite of the agent's history without trusting our infrastructure or knowing `SECRET_SALT`. This is what "epistemic accountability" looks like in code: not a promise that the research wasn't post-facto edited, but a mathematically verifiable property of the data.
