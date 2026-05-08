"""Unit tests for integrity_engine.

All tests are pure Python — no Delta dependency. The Delta-write path is
exercised on the VM via the Phase 8 deployment runbook (`integrity_engine.py
init` then `audit`).
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from pathlib import Path

import pytest


# A salt long enough to satisfy Salter's MIN_SALT_LENGTH (32 chars).
TEST_SALT = "0" * 64


# -----------------------------------------------------------------------------
# Salter
# -----------------------------------------------------------------------------

def test_salter_missing_env_raises(monkeypatch):
    from openclaw.integrity_engine import Salter, SaltUnavailableError
    monkeypatch.delenv("SECRET_SALT", raising=False)
    with pytest.raises(SaltUnavailableError):
        Salter()


def test_salter_short_salt_raises(monkeypatch):
    from openclaw.integrity_engine import Salter, SaltUnavailableError
    monkeypatch.setenv("SECRET_SALT", "tooshort")
    with pytest.raises(SaltUnavailableError):
        Salter()


def test_salter_explicit_salt_overrides_env(monkeypatch):
    from openclaw.integrity_engine import Salter
    monkeypatch.setenv("SECRET_SALT", "ignored_because_explicit_salt_is_passed")
    s = Salter(salt=TEST_SALT)
    assert s.get_salt(1) == TEST_SALT


def test_salter_v2_not_supported():
    from openclaw.integrity_engine import Salter
    s = Salter(salt=TEST_SALT)
    with pytest.raises(NotImplementedError):
        s.get_salt(2)


# -----------------------------------------------------------------------------
# Signer — known-vector verification
# -----------------------------------------------------------------------------

def test_signer_known_vector():
    """row_signature = SHA-256(payload_hash || previous_hash || salt)."""
    from openclaw.integrity_engine import Salter, Signer

    payload_hash = "a" * 64
    previous_hash = "b" * 64
    salt = "c" * 64
    expected = hashlib.sha256(
        (payload_hash + previous_hash + salt).encode("utf-8")
    ).hexdigest()

    signer = Signer(Salter(salt=salt))
    assert signer.sign(payload_hash, previous_hash) == expected


def test_signer_verify_roundtrip():
    from openclaw.integrity_engine import Salter, Signer
    signer = Signer(Salter(salt=TEST_SALT))
    sig = signer.sign("a" * 64, "b" * 64)
    assert signer.verify(sig, "a" * 64, "b" * 64)
    assert not signer.verify(sig, "a" * 64, "c" * 64)
    assert not signer.verify(sig, "d" * 64, "b" * 64)


def test_signer_changes_with_salt():
    from openclaw.integrity_engine import Salter, Signer
    s1 = Signer(Salter(salt="0" * 64))
    s2 = Signer(Salter(salt="1" * 64))
    sig1 = s1.sign("a" * 64, "b" * 64)
    sig2 = s2.sign("a" * 64, "b" * 64)
    assert sig1 != sig2


# -----------------------------------------------------------------------------
# Merkle tree
# -----------------------------------------------------------------------------

def test_merkle_empty():
    from openclaw.integrity_engine import GENESIS_PREVIOUS_HASH, MerkleTreeBuilder
    assert MerkleTreeBuilder.root([]) == GENESIS_PREVIOUS_HASH


def test_merkle_single_returns_self():
    from openclaw.integrity_engine import MerkleTreeBuilder
    h = "a" * 64
    assert MerkleTreeBuilder.root([h]) == h


def test_merkle_pair_known_vector():
    from openclaw.integrity_engine import MerkleTreeBuilder
    a, b = "a" * 64, "b" * 64
    expected = hashlib.sha256((a + b).encode("utf-8")).hexdigest()
    assert MerkleTreeBuilder.root([a, b]) == expected


def test_merkle_odd_pads_last():
    """Odd-length levels duplicate the last element before pairing."""
    from openclaw.integrity_engine import MerkleTreeBuilder
    a, b, c = "a" * 64, "b" * 64, "c" * 64
    # First level: [hash(a||b), hash(c||c)]; root = hash of those two.
    pair_ab = hashlib.sha256((a + b).encode("utf-8")).hexdigest()
    pair_cc = hashlib.sha256((c + c).encode("utf-8")).hexdigest()
    expected = hashlib.sha256((pair_ab + pair_cc).encode("utf-8")).hexdigest()
    assert MerkleTreeBuilder.root([a, b, c]) == expected


def test_merkle_deterministic_across_calls():
    from openclaw.integrity_engine import MerkleTreeBuilder
    hashes = [f"{i:064x}" for i in range(7)]
    r1 = MerkleTreeBuilder.root(hashes)
    r2 = MerkleTreeBuilder.root(hashes)
    assert r1 == r2


# -----------------------------------------------------------------------------
# Test fixtures: an in-memory ChainStore for auditor tests
# -----------------------------------------------------------------------------

class InMemoryChainStore:
    """Stand-in for ChainStore that doesn't require Delta. Used only in tests."""

    def __init__(self) -> None:
        self.entries: list = []

    def append(self, entry) -> None:
        self.entries.append(entry)

    def read_all_sorted(self):
        return sorted(self.entries, key=lambda e: e.sequence_id)

    def read_latest(self):
        return self.read_all_sorted()[-1] if self.entries else None


def _build_chain(store, signer, salter, count: int, start: int = 0) -> None:
    """Append `count` valid INSERT chain entries starting at sequence_id=start.
    Caller is responsible for genesis if start=0.
    """
    from openclaw.integrity_engine import (
        GENESIS_PREVIOUS_HASH, ChainEntry, ChainOperation,
    )
    prev = (
        store.entries[-1].row_signature if store.entries else GENESIS_PREVIOUS_HASH
    )
    for i in range(count):
        seq = start + i
        op = ChainOperation.GENESIS if seq == 0 else ChainOperation.INSERT
        target_table = None if op == ChainOperation.GENESIS else f"research.bronze.t_{seq}"
        target_row = None if op == ChainOperation.GENESIS else f"row-{seq}"
        payload_hash = hashlib.sha256(f"payload-{seq}".encode()).hexdigest()
        sig = signer.sign(payload_hash, prev, salter.current_version)
        store.append(ChainEntry(
            sequence_id=seq,
            recorded_at=datetime(2026, 5, 7, 12, 0, seq % 60, tzinfo=timezone.utc),
            author_identity="test",
            operation=op,
            target_table=target_table,
            target_row_id=target_row,
            target_payload_hash=payload_hash,
            previous_hash=prev,
            row_signature=sig,
            salt_version=salter.current_version,
        ))
        prev = sig


# -----------------------------------------------------------------------------
# IntegrityAuditor — the heart of Phase 8
# -----------------------------------------------------------------------------

def test_audit_empty_chain_intact():
    from openclaw.integrity_engine import IntegrityAuditor, Salter, Signer
    store = InMemoryChainStore()
    auditor = IntegrityAuditor(store, Signer(Salter(salt=TEST_SALT)))
    v = auditor.audit()
    assert v.is_intact
    assert v.chain_length == 0


def test_audit_intact_chain():
    from openclaw.integrity_engine import IntegrityAuditor, Salter, Signer
    salter = Salter(salt=TEST_SALT)
    signer = Signer(salter)
    store = InMemoryChainStore()
    _build_chain(store, signer, salter, count=10)
    v = IntegrityAuditor(store, signer).audit()
    assert v.is_intact
    assert v.chain_length == 10
    assert v.last_verified_sequence_id == 9


def test_audit_detects_payload_tamper_at_exact_seq():
    """If a row's target_payload_hash is altered, audit flags that exact sequence."""
    from openclaw.integrity_engine import IntegrityAuditor, Salter, Signer
    salter = Salter(salt=TEST_SALT)
    signer = Signer(salter)
    store = InMemoryChainStore()
    _build_chain(store, signer, salter, count=10)

    # Tamper with sequence_id=5: change its target_payload_hash via dict path
    # (ChainEntry is frozen, so build a new one with the same fields except hash)
    bad = store.entries[5].model_copy(update={
        "target_payload_hash": "f" * 64,
    })
    store.entries[5] = bad

    v = IntegrityAuditor(store, signer).audit()
    assert not v.is_intact
    assert v.first_tampered_sequence_id == 5
    assert v.last_verified_sequence_id == 4
    assert "row_signature mismatch" in v.failure_reason


def test_audit_detects_broken_link():
    """If previous_hash is altered (chain link broken), audit flags that row."""
    from openclaw.integrity_engine import IntegrityAuditor, Salter, Signer
    salter = Salter(salt=TEST_SALT)
    signer = Signer(salter)
    store = InMemoryChainStore()
    _build_chain(store, signer, salter, count=8)

    bad = store.entries[3].model_copy(update={"previous_hash": "0" * 64})
    store.entries[3] = bad

    v = IntegrityAuditor(store, signer).audit()
    assert not v.is_intact
    assert v.first_tampered_sequence_id == 3
    assert "previous_hash" in v.failure_reason


def test_audit_detects_sequence_skip():
    """If a row is deleted, the next row's sequence_id becomes non-monotonic."""
    from openclaw.integrity_engine import IntegrityAuditor, Salter, Signer
    salter = Salter(salt=TEST_SALT)
    signer = Signer(salter)
    store = InMemoryChainStore()
    _build_chain(store, signer, salter, count=10)

    # Delete sequence_id=5 — leaves seq=4 then seq=6
    del store.entries[5]

    v = IntegrityAuditor(store, signer).audit()
    assert not v.is_intact
    assert v.first_tampered_sequence_id == 6
    assert "non-monotonic" in v.failure_reason


def test_audit_detects_genesis_not_at_zero():
    """If sequence_id=0 is missing, audit refuses to start."""
    from openclaw.integrity_engine import IntegrityAuditor, Salter, Signer
    salter = Salter(salt=TEST_SALT)
    signer = Signer(salter)
    store = InMemoryChainStore()
    _build_chain(store, signer, salter, count=5, start=1)  # No sequence_id=0

    v = IntegrityAuditor(store, signer).audit()
    assert not v.is_intact
    assert v.first_tampered_sequence_id == 1
    assert "sequence_id=0" in v.failure_reason


def test_audit_detects_bad_genesis_previous_hash():
    """Genesis must have previous_hash = '0' * 64."""
    from openclaw.integrity_engine import (
        ChainEntry, ChainOperation, IntegrityAuditor, Salter, Signer,
    )
    salter = Salter(salt=TEST_SALT)
    signer = Signer(salter)
    store = InMemoryChainStore()

    # Build a genesis with an INVALID previous_hash (not all zeros).
    payload_hash = hashlib.sha256(b"genesis-payload").hexdigest()
    fake_prev = "f" * 64
    sig = signer.sign(payload_hash, fake_prev, 1)  # signs the bad version
    store.append(ChainEntry(
        sequence_id=0,
        recorded_at=datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc),
        author_identity="test",
        operation=ChainOperation.GENESIS,
        target_table=None,
        target_row_id=None,
        target_payload_hash=payload_hash,
        previous_hash=fake_prev,
        row_signature=sig,
        salt_version=1,
    ))

    v = IntegrityAuditor(store, signer).audit()
    assert not v.is_intact
    assert v.first_tampered_sequence_id == 0
    assert "sentinel" in v.failure_reason


# -----------------------------------------------------------------------------
# Genesis anchor cross-check
# -----------------------------------------------------------------------------

def test_anchor_mismatch_detected(tmp_path):
    """If the genesis anchor file disagrees with the chain's genesis, audit flags it."""
    from openclaw.integrity_engine import (
        GenesisAnchor, IntegrityAuditor, Salter, Signer,
    )
    salter = Salter(salt=TEST_SALT)
    signer = Signer(salter)
    store = InMemoryChainStore()
    _build_chain(store, signer, salter, count=3)

    anchor_file = tmp_path / "genesis.json"
    anchor_file.write_text(
        '{"sequence_id":0,"row_signature":"' + ("0" * 64) + '",'
        '"target_payload_hash":"' + ("0" * 64) + '","recorded_at":"2026-05-07T12:00:00+00:00","salt_version":1}'
    )
    anchor = GenesisAnchor(path=anchor_file)

    v = IntegrityAuditor(store, signer, anchor=anchor).audit()
    assert not v.is_intact
    assert "anchor" in v.failure_reason


def test_anchor_match_passes(tmp_path):
    from openclaw.integrity_engine import (
        GenesisAnchor, IntegrityAuditor, Salter, Signer,
    )
    salter = Salter(salt=TEST_SALT)
    signer = Signer(salter)
    store = InMemoryChainStore()
    _build_chain(store, signer, salter, count=3)

    anchor = GenesisAnchor(path=tmp_path / "genesis.json")
    anchor.write(store.entries[0])

    v = IntegrityAuditor(store, signer, anchor=anchor).audit()
    assert v.is_intact


# -----------------------------------------------------------------------------
# DailySealer
# -----------------------------------------------------------------------------

def test_daily_seal_writes_checkpoint():
    from openclaw.integrity_engine import (
        ChainOperation, ChainWriter, DailySealer, GenesisAnchor,
        Salter, Signer,
    )
    salter = Salter(salt=TEST_SALT)
    signer = Signer(salter)
    store = InMemoryChainStore()
    _build_chain(store, signer, salter, count=5)
    writer = ChainWriter(store, signer, salter)

    sealer = DailySealer(store, writer)
    seal_date = store.entries[-1].recorded_at.date()
    result = sealer.seal_day(seal_date)

    assert result is not None
    assert result.operation == ChainOperation.CHECKPOINT
    assert result.sequence_id == 5  # appended after the existing 5 entries


def test_daily_seal_idempotent():
    from openclaw.integrity_engine import (
        ChainWriter, DailySealer, Salter, Signer,
    )
    salter = Salter(salt=TEST_SALT)
    signer = Signer(salter)
    store = InMemoryChainStore()
    _build_chain(store, signer, salter, count=3)
    writer = ChainWriter(store, signer, salter)

    sealer = DailySealer(store, writer)
    seal_date = store.entries[-1].recorded_at.date()
    first = sealer.seal_day(seal_date)
    chain_length_after_first = len(store.entries)
    second = sealer.seal_day(seal_date)

    assert first.row_signature == second.row_signature
    assert len(store.entries) == chain_length_after_first


def test_daily_seal_no_entries_returns_none():
    from openclaw.integrity_engine import (
        ChainWriter, DailySealer, Salter, Signer,
    )
    salter = Salter(salt=TEST_SALT)
    signer = Signer(salter)
    store = InMemoryChainStore()
    writer = ChainWriter(store, signer, salter)
    sealer = DailySealer(store, writer)
    result = sealer.seal_day(date(2026, 5, 7))
    assert result is None


# -----------------------------------------------------------------------------
# ChainEntry validation
# -----------------------------------------------------------------------------

def test_chain_entry_requires_aware_datetime():
    from openclaw.integrity_engine import ChainEntry, ChainOperation
    with pytest.raises(Exception):
        ChainEntry(
            sequence_id=0,
            recorded_at=datetime(2026, 5, 7, 12, 0, 0),  # naive — should fail
            author_identity="test",
            operation=ChainOperation.GENESIS,
            target_payload_hash="0" * 64,
            previous_hash="0" * 64,
            row_signature="0" * 64,
            salt_version=1,
        )


def test_chain_entry_hex_64_pattern():
    from openclaw.integrity_engine import ChainEntry, ChainOperation
    base = dict(
        sequence_id=0,
        recorded_at=datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc),
        author_identity="t",
        operation=ChainOperation.GENESIS,
        previous_hash="0" * 64,
        row_signature="0" * 64,
        salt_version=1,
    )
    # uppercase hex is rejected (canonical lowercase only)
    with pytest.raises(Exception):
        ChainEntry(**base, target_payload_hash="A" * 64)
    # short hash is rejected
    with pytest.raises(Exception):
        ChainEntry(**base, target_payload_hash="a" * 32)
