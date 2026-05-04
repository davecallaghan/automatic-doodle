# Plan: Open-Source Databricks Stack Integration

## Architecture Philosophy

This integration uses the **open-source Databricks components only** — no paid Databricks workspace required. All services run self-hosted on GCP inside the existing sandbox. The goal is enterprise-grade governance and auditability for a lightweight system that can safely contain a potential threat actor like OpenClaw.

The three pillars:

| Component | OSS Repo | Role |
|---|---|---|
| **Unity Catalog** | `unitycatalog/unitycatalog` | Governance: access control, lineage, audit trail |
| **Delta Lake** | `delta-io/delta-rs` | Storage: ACID tables, schema enforcement, time travel |
| **MLflow** | `mlflow/mlflow` | Ops: research run tracking, artifact logging, experiment history |

All three write to the **50GB persistent disk** (`/mnt/disks/research/`) — data survives container and VM restarts.

---

## Why This Stack for an AI Agent

- **Unity Catalog governance** controls what the agent can read/write at the catalog level — a threat actor that escapes the container still hits catalog-enforced access rules
- **Delta Lake audit trail** — every write is a transaction; time travel lets you reconstruct exactly what the agent wrote and when, even if it tries to overwrite
- **MLflow experiment tracking** — each research "run" is a logged experiment: prompt, response, sources, latency, model version — reproducible and auditable
- **No cloud vendor lock-in** — runs on GCP today, portable to any host with Docker

---

## Self-Hosted Architecture on GCP

```
/mnt/disks/research/          (50GB persistent disk)
  ├── delta/                  ← Delta tables (Unity Catalog manages these)
  │   ├── research_gold/      ← Gold layer: curated research summaries
  │   └── audit_log/          ← Immutable append-only audit trail
  ├── mlflow/                 ← MLflow tracking server data
  │   ├── mlruns/             ← Experiment runs
  │   └── artifacts/          ← Run artifacts (full text, sources)
  ├── unity_catalog/          ← Unity Catalog server metadata + config
  └── workspace/research_logs/ ← Local write buffer (connection failsafe)
```

Three sidecar containers run alongside `openclaw`:

```
openclaw-secure-node (VM)
  ├── openclaw          (existing — the agent)
  ├── unity-catalog     (new — UC OSS server, REST API on :8080)
  ├── mlflow-server     (new — MLflow tracking UI + API on :5000)
  └── [all share bridge network, no external exposure]
```

---

## Deliverables

### New files to create:

1. **`openclaw/databricks_worker.py`** — Python integration module
2. **`openclaw/requirements.txt`** — updated dependencies (OSS stack, no `databricks-sql-python`)
3. **`openclaw/unity_catalog_setup.sql`** — DDL for Delta Tables and UC namespace setup
4. **`sandbox/gcp/sidecars.sh`** — script to start Unity Catalog and MLflow sidecar containers on the VM

### Files to update:
5. **`init_agency.sh`** — add UC + MLflow env vars to `.env` template, add sidecar startup to VM startup script

---

## Python Module: `databricks_worker.py`

### Connector: `delta-rs` (not `databricks-sql-python`)

For the OSS path, Delta tables are read/written via **`delta-rs`** — the Rust-backed Python library. No Spark, no JVM, very lightweight. Unity Catalog is accessed via its **REST API**.

```
# requirements additions
delta-rs>=0.18.0         # Delta Lake read/write (no Spark required)
mlflow>=2.13.0           # MLflow tracking SDK
requests>=2.31.0         # Unity Catalog REST API calls
pyarrow>=15.0.0          # Arrow columnar format (delta-rs dependency)
```

### Class structure:

```
DatabricksWorker          (orchestrator — called by OpenClaw agent)
  ├── DeltaTableWriter    — writes ResearchEntry to Delta table via delta-rs
  ├── AuditHasher         — SHA-256 of entry content before every write
  ├── UnityCatalogClient  — REST API calls to UC server (register tables, check lineage, query permissions)
  ├── MLflowTracker       — logs each research run as an MLflow experiment
  └── LocalBuffer         — writes to /workspace/research_logs/ if UC/Delta connection fails; retries on next run
```

### Delta Table Schema (Gold Layer)

| Column | Type | Notes |
|---|---|---|
| `entry_id` | STRING | UUID, agent-generated |
| `created_at` | TIMESTAMP | UTC |
| `topic` | STRING | Research topic label |
| `summary` | STRING | Full research summary |
| `content_hash` | STRING | SHA-256 of `summary` — immutable audit trail |
| `status` | STRING | `DRAFT` (agent) or `PUBLISHED` (human-approved only) |
| `source_agent` | STRING | e.g. `openclaw-v4` |
| `mlflow_run_id` | STRING | Links entry to MLflow experiment run |
| `metadata` | MAP<STRING,STRING> | Flexible extra context |

**Every agent INSERT hardcodes `status = 'DRAFT'`. No code path may write `PUBLISHED` — that is a manual human step only.**

---

## Human-in-the-Loop: Promotion Workflow

David reviews DRAFT entries and promotes them:

```sql
-- Review drafts
SELECT entry_id, topic, summary, created_at
FROM research.gold.research_summaries
WHERE status = 'DRAFT'
ORDER BY created_at DESC;

-- Promote a specific entry to the archive
UPDATE research.gold.research_summaries
SET status = 'PUBLISHED'
WHERE entry_id = '<uuid>';
```

Delta Lake enforces ACID on this update — the history is preserved via time travel even after promotion.

---

## Unity Catalog OSS Setup

Unity Catalog server runs as a Docker container on the VM:

```bash
docker run -d \
  --name unity-catalog \
  --network=bridge \
  -p 127.0.0.1:8080:8080 \
  -v /mnt/disks/research/unity_catalog:/opt/unitycatalog/etc/db \
  -v /mnt/disks/research/delta:/opt/unitycatalog/etc/data \
  --memory=512m \
  --restart unless-stopped \
  unitycatalog/unitycatalog:latest server
```

Catalog namespace:
- Catalog: `openclaw`
- Schema: `research`
- Tables: `research_summaries` (Gold), `audit_log`

---

## MLflow Setup

MLflow tracking server runs as a Docker container:

```bash
docker run -d \
  --name mlflow-server \
  --network=bridge \
  -p 127.0.0.1:5000:5000 \
  -v /mnt/disks/research/mlflow:/mlflow \
  --memory=256m \
  --restart unless-stopped \
  ghcr.io/mlflow/mlflow:latest \
  mlflow server \
    --backend-store-uri /mlflow/mlruns \
    --default-artifact-root /mlflow/artifacts \
    --host 0.0.0.0
```

Each OpenClaw research run logs:
- Parameters: topic, model, prompt hash
- Metrics: latency, source count, confidence score (if available)
- Artifacts: full source text, raw agent response

MLflow UI accessible via `oc-tunnel` extension (add port 5000 forward alongside 18789).

---

## Zero-Trust Authentication

Service Principal only — no user login for a headless agent.

`.env` additions:
```bash
# Unity Catalog OSS
UC_SERVER_URL=http://localhost:8080     # Internal — UC runs on the same VM
UC_AUTH_TOKEN=                          # UC server auth token (set during UC init)
UC_CATALOG=openclaw
UC_SCHEMA=research
UC_TABLE_SUMMARIES=research_summaries
UC_TABLE_AUDIT=audit_log

# MLflow
MLFLOW_TRACKING_URI=http://localhost:5000
MLFLOW_EXPERIMENT_NAME=openclaw-research
```

No external credentials needed for OSS UC — it runs inside the VM, on the same Docker bridge network. The UC auth token is generated during `sidecars.sh` init and stored in `.secrets/.env`.

---

## Security: Containment Properties

Running this stack specifically for a potential threat actor:

| Control | Implementation |
|---|---|
| Agent cannot self-promote entries | `PUBLISHED` write path does not exist in agent code |
| Delta time travel | Every version of every row is recoverable — agent cannot destroy history |
| UC lineage | Every read/write the agent performs is recorded in UC lineage |
| UC permissions | Agent service principal has WRITE on `research_summaries`, READ-ONLY on all other tables |
| Network isolation | UC and MLflow bind to `127.0.0.1` — not reachable from outside VM |
| Local buffer on persistent disk | If agent tries to flood the buffer, it hits the 50GB disk limit, not ephemeral container storage |

---

## Quality Checklist (Review Before Accepting Code)

1. **Service Principal logic** — no `dbutils`, no interactive auth; connection uses token from env var only

2. **Volume mount path** — local buffer writes to `/home/clawuser/workspace/research_logs/` (the host-mounted persistent disk, not container ephemeral storage)

3. **DRAFT status on insert** — every INSERT hardcodes `status = 'DRAFT'`; no code path writes `PUBLISHED`

4. **`delta-rs` not `databricks-sql-python`** — OSS path only; reject any code that imports `databricks.sdk` or `databricks-sql-connector` (those are paid-service connectors)

5. **MLflow run linked to Delta entry** — `mlflow_run_id` must be written to the Delta table row so research entries and experiment runs are cross-referenceable

---

## Implementation Notes

- Implement and test Unity Catalog + Delta + MLflow sidecar setup first (`sidecars.sh`), before writing the Python module
- Unity Catalog OSS was released 2024 — verify the image tag at `ghcr.io/unitycatalog/unitycatalog` before implementing
- `delta-rs` Python bindings (`deltalake` on PyPI) are the correct package name — not `delta-spark`
- This is a future enhancement — do not modify running infrastructure until this plan is executed
