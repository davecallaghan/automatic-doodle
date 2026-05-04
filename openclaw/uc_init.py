#!/usr/bin/env python3
"""
uc_init.py — Phase 1 bootstrap for Unity Catalog OSS.

Creates the `research` catalog and its five schemas (bronze, silver, gold,
public_archive, audit) on the local Unity Catalog server. Idempotent —
existing catalog and schemas are left as-is.

Tables are NOT created here. The worker (Phase 2) creates tables on first
write via delta-rs. The canonical table DDL lives in unity_catalog_setup.sql
for human review.

Run on the VM, after sidecars.sh:
    python3 uc_init.py

Environment:
    UC_SERVER_URL   default: http://localhost:8080
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any
from urllib import request, error

UC_SERVER_URL = os.environ.get("UC_SERVER_URL", "http://localhost:8080")
API_BASE = f"{UC_SERVER_URL.rstrip('/')}/api/2.1/unity-catalog"

CATALOG_NAME = "research"
CATALOG_COMMENT = "OpenClaw research datastore — bronze/silver/gold + audit"

SCHEMAS: list[tuple[str, str]] = [
    ("bronze",         "Raw, unfiltered: every agent response and source fetch"),
    ("silver",         "Validated and structured: passes schema and virtue checks"),
    ("gold",           "Curated: research summaries, fairness scorecards, run summaries"),
    ("public_archive", "Human-promoted, CC BY 4.0 — only PROMOTED briefs land here"),
    ("audit",          "Append-only integrity chain — tamper-evident audit trail"),
]


class UCError(RuntimeError):
    pass


def _req(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with request.urlopen(req, timeout=10) as resp:
            payload = resp.read()
            return json.loads(payload) if payload else {}
    except error.HTTPError as e:
        raise UCError(f"{method} {path} → HTTP {e.code}: {e.read().decode('utf-8', 'replace')}")
    except error.URLError as e:
        raise UCError(f"{method} {path} → connection error: {e.reason}")


def wait_for_uc(timeout_seconds: int = 30) -> None:
    deadline = time.time() + timeout_seconds
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            _req("GET", "/catalogs")
            return
        except UCError as e:
            last_err = e
            time.sleep(1)
    raise UCError(f"Unity Catalog at {UC_SERVER_URL} not reachable after {timeout_seconds}s: {last_err}")


def catalog_exists(name: str) -> bool:
    try:
        _req("GET", f"/catalogs/{name}")
        return True
    except UCError as e:
        if "404" in str(e):
            return False
        raise


def schema_exists(catalog: str, name: str) -> bool:
    try:
        _req("GET", f"/schemas/{catalog}.{name}")
        return True
    except UCError as e:
        if "404" in str(e):
            return False
        raise


def create_catalog(name: str, comment: str) -> None:
    if catalog_exists(name):
        print(f"[catalog] {name} already exists")
        return
    _req("POST", "/catalogs", {"name": name, "comment": comment})
    print(f"[catalog] created {name}")


def create_schema(catalog: str, name: str, comment: str) -> None:
    if schema_exists(catalog, name):
        print(f"[schema] {catalog}.{name} already exists")
        return
    _req("POST", "/schemas", {"name": name, "catalog_name": catalog, "comment": comment})
    print(f"[schema] created {catalog}.{name}")


def main() -> int:
    print(f"Phase 1 bootstrap → {UC_SERVER_URL}")
    print()

    print("Waiting for Unity Catalog to be reachable...")
    wait_for_uc()
    print("✓ Unity Catalog responding")
    print()

    create_catalog(CATALOG_NAME, CATALOG_COMMENT)
    for schema_name, schema_comment in SCHEMAS:
        create_schema(CATALOG_NAME, schema_name, schema_comment)

    print()
    print("=== Verification ===")
    catalogs = _req("GET", "/catalogs").get("catalogs", [])
    found = [c for c in catalogs if c.get("name") == CATALOG_NAME]
    if not found:
        print(f"✗ catalog {CATALOG_NAME} not visible in /catalogs listing")
        return 2

    schemas = _req("GET", f"/schemas?catalog_name={CATALOG_NAME}").get("schemas", [])
    schema_names = {s.get("name") for s in schemas}
    expected = {name for name, _ in SCHEMAS}
    missing = expected - schema_names
    if missing:
        print(f"✗ missing schemas: {sorted(missing)}")
        return 2

    print(f"✓ catalog '{CATALOG_NAME}' present")
    print(f"✓ {len(expected)} schemas present: {sorted(expected)}")
    print()
    print("Phase 1 init complete. Tables will be created by the worker on first write.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except UCError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
