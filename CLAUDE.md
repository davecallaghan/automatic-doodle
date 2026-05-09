# automatic-doodle / OpenClaw Hardened Agency

## What This Project Is

A cost-optimized, security-hardened AI research agent on GCP, built up in twelve phases. Phases 1–6 are deployed and running; Phases 7–9 have code shipped on dedicated branches and a runbook for deployment; Phases 10–12 are designed but not yet coded. The agent runs 9 AM–5 PM EST weekdays, accessible only via IAP tunnel.

The GitHub repo name ("automatic-doodle") was auto-assigned and kept — it fits.

---

## Phase Overview

| # | Phase | Status |
|---|---|---|
| 1 | Local setup (init_agency.sh, local_hardening.sh) | ✅ deployed |
| 2 | GCP infra (provision, networking, schedule) | ✅ deployed |
| 3 | VM configuration (disk mount, setup.sh) | ✅ deployed |
| 4 | Secrets configuration (.env populated) | ✅ deployed |
| 5 | OpenClaw access & testing (Telegram + Gemini) | ✅ deployed |
| 6 | Databricks sidecars + Unity Catalog bootstrap | ✅ deployed |
| 7 | Databricks worker (bronze + silver Delta writes) | 🟢 code on `databricks_worker` branch |
| 8 | Integrity engine (linked-hash chain + Merkle seal) | 🟢 code on `databricks_integrity_engine` branch |
| 9 | Fairness scorecard + MLflow tracking | 🟢 code on main |
| 10 | Promotion CLI (DRAFT → PROMOTED) | 🟢 code on main |
| 11 | Adversarial validation suite | 🟢 code on main |
| 12 | Public commons (local export + seal log) | 🟢 code on main |
| 12.5 | Delta Sharing server sidecar | 📋 follow-up |
| 12.6 | Public distribution (GitHub / API / MCP / etc.) | 📋 follow-up |

The single source of truth for deployment is [Deployment_Checklist.md](Deployment_Checklist.md) — top-to-bottom runbook covering every component in deployment order.

---

## Architecture Overview

```
Mac (gcloud CLI + IAP SSH tunnels)
  │  IAP-only SSH (35.235.240.0/20 → port 22)
  ▼
GCP VM: openclaw-secure-node (e2-standard-2, us-east4-a, NO public IP)
  │
  │  Persistent boot disk = research disk (50 GB pd-balanced, auto-delete=no)
  ▼
/mnt/disks/research/   ← single persistent disk, NOT a separate mount
  ├── workspace/                 → /home/clawuser/workspace (research outputs)
  │   └── research_logs/         → LocalBuffer JSONL failsafe (Phase 7+)
  ├── logs/, vector_db/, .openclaw/, .secrets/.env
  ├── delta/                     ← Delta tables under research catalog (Phase 7+)
  │   └── research/{bronze,silver,gold,public_archive,audit}/...
  ├── unity_catalog/db/          ← UC OSS H2 metadata (Phase 6)
  ├── mlflow/{mlruns,artifacts,mlflow.db}  ← MLflow tracking (Phase 6)
  └── audit/genesis.json         ← integrity chain anchor (Phase 8)
  │
  ▼
Docker containers on `openclaw-net` bridge network:
  - openclaw           ← hardened: read-only FS, non-root 1000:1000, port 18789
  - unity-catalog      ← UC OSS sidecar, port 8080 (127.0.0.1 only)
  - mlflow-server      ← MLflow v2.16.0 + sqlite, port 5000 (127.0.0.1 only)
```

**Outbound internet**: Cloud NAT (outbound-only). Sidecars are 127.0.0.1-bound; access from Mac via SSH port forward.

---

## Two-Layer + Application-Code Structure

```
automatic-doodle/
├── CLAUDE.md, README.md, Deployment_Checklist.md, MANUAL_COMMANDS.md
├── init_agency.sh                 ← source-of-truth generator for shell + small Python
├── local_hardening.sh
│
├── sandbox/                       ← Cloud infra (host-agnostic)
│   ├── README.md
│   └── gcp/                       ← GCP implementation
│       ├── provision.sh           Phase 1
│       ├── networking.sh          Phase 1
│       ├── schedule.sh            Phase 1
│       └── sidecars.sh            Phase 6 (UC + MLflow + openclaw-net)
│
└── openclaw/                      ← Application code
    ├── README.md, Dockerfile.hardened, setup.sh
    ├── workspace/{topics.json, virtue_prompt.md}
    │
    ├── PLAN_databricks_integration.md         ← original spec
    ├── EXECUTION_databricks_integration.md    ← master execution plan (Phases 1–6)
    ├── EXECUTION_integrity_engine.md          ← Phase 8 design
    ├── EXECUTION_public_commons.md            ← Phase 12 design
    │
    ├── unity_catalog_setup.sql    ← Phase 6: canonical schema reference
    ├── uc_init.py                 ← Phase 6: bootstrap catalog + 5 schemas
    ├── databricks_worker.py       ← Phase 7: bronze/silver writes + (Phase 9) gold
    ├── integrity_engine.py        ← Phase 8: chain + genesis + Merkle seal + audit CLI
    ├── fairness_scorer.py         ← Phase 9: deterministic 6-metric scoring
    ├── mlflow_tracker.py          ← Phase 9: MLflow client wrapper
    ├── promote.py                 ← Phase 10: review + promote CLI (CC BY 4.0 markdown)
    ├── adversarial_drills.py      ← Phase 11: live-system boundary verification CLI
    ├── commons_publisher.py       ← Phase 12: ledger rollup, brief export, seal log
    ├── requirements.txt           ← Python deps (deltalake, pydantic, pyarrow, mlflow)
    ├── __init__.py
    └── tests/                     ← pytest suite, 147 tests, all pure-Python
        ├── test_databricks_worker.py
        ├── test_integrity_engine.py
        ├── test_fairness_scorer.py
        ├── test_mlflow_tracker.py
        ├── test_promote.py
        ├── test_adversarial.py    ← Phase 11: cross-cutting attack scenarios
        └── test_commons_publisher.py
```

### Source of truth

`init_agency.sh` is the source of truth for **shell scripts and small config files** (`sidecars.sh`, `unity_catalog_setup.sql`, `uc_init.py`). It writes them via heredocs. **If a bug exists in those generated files, fix it in `init_agency.sh` and regenerate — never patch the generated file directly.**

For the larger Python application code (Phases 7–10 — `databricks_worker.py`, `integrity_engine.py`, `fairness_scorer.py`, `mlflow_tracker.py`, `promote.py` and their tests), the heredoc pattern is brittle for ~300+ line files; those live as ordinary source files and are maintained directly.

---

## GCP Resources

| Resource | Name | Notes |
|---|---|---|
| Project | `orphansinthedesert` | |
| VM | `openclaw-secure-node` | **e2-standard-2** (bumped from e2-medium for Phase 6 sidecar headroom) |
| Disk | `openclaw-secure-node` | 50 GB pd-balanced, `auto-delete=no` — single disk doubles as boot + research |
| Service Account | `openclaw-agent-v3@orphansinthedesert.iam.gserviceaccount.com` | logWriter + metricWriter only |
| Firewall | `allow-ssh-iap`, `allow-openclaw-web-iap` | IAP range only |
| Router | `openclaw-router` (us-east4) | |
| NAT | `openclaw-nat` | 64 ports/VM (cost-optimized) |
| Schedule | `openclaw-day-shift` | 9 AM–5 PM EST, weekdays |
| Snapshot | latest: `openclaw-20260507-2108-phase6` | post-Phase-6 baseline |

---

## Key Security Properties

| Area | Implementation |
|---|---|
| IAM | `logging.logWriter` + `monitoring.metricWriter` only |
| OAuth scopes | `logging.write` + `monitoring.write` only |
| Firewall targeting | SSH rule targets service account; web rule is tag-based |
| Container filesystem | Read-only (`--read-only`) on openclaw |
| Capabilities | ALL dropped on openclaw, plus CHOWN + DAC_OVERRIDE |
| User | openclaw runs 1000:1000 (non-root); UC sidecar runs uid 100 (unitycatalog) |
| Secrets mount | Read-only (`:ro`) |
| Public network exposure | None — sidecars and gateway all bind 127.0.0.1 |
| **Integrity chain (Phase 8)** | Every state-changing write produces an append-only chain entry whose row_signature = SHA-256(payload_hash ‖ prev_hash ‖ SECRET_SALT). Tamper detection is precise to the exact `sequence_id`. |
| **Fairness gating (Phase 9)** | Briefs failing any of six thresholds land as `status='REJECTED'` with the failure list recorded. Agent cannot self-promote. |
| **Promotion boundary (Phase 10)** | `status='PROMOTED'` is unreachable from agent code — only `promote.py` produces it. Each promotion writes 3 append-only rows (summary, published, log) and a chain entry tagged with the reviewer's `author_identity`. Override of a REJECTED brief requires an explicit `--override REASON`. |
| **Public commons (Phase 12)** | A nightly export produces durable markdown copies of every PROMOTED brief, a daily AI-usage rollup with `give_back_ratio`, and a `SEALS.md` log of daily Merkle seals. External observers can verify chain integrity from `SEALS.md` without holding `SECRET_SALT`. Distribution channel is deferred. |

---

## Agent Integrations (`/mnt/disks/research/.secrets/.env`)

| Service | Purpose | Key |
|---|---|---|
| Telegram | Chat interface | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| Gemini | AI model provider | `GEMINI_API_KEY` |
| Exa | Web search | `EXA_API_KEY` |
| GitHub | Repo read access | `GITHUB_PAT` |
| Unity Catalog | Catalog server URL | `UC_SERVER_URL=http://unity-catalog:8080` |
| MLflow | Tracking server URL | `MLFLOW_TRACKING_URI=http://mlflow-server:5000` |
| Integrity chain | Cryptographic salt | `SECRET_SALT` (64 hex chars; **back up to password manager**) |

**Model**: `google/gemini-2.5-flash`.

---

## Research Configuration

**Topics** (`openclaw/workspace/topics.json`): AI Safety & Alignment (high), Databricks & Unity Catalog (medium), Small Language Models (medium), Developer Tooling (low).

**Virtue Protocol** (`openclaw/workspace/virtue_prompt.md`):
1. Devil's Advocate: counter-arguments mandatory
2. Authority Ranking: tier 1 (docs/papers) → tier 4 (SEO spam)
3. Regenerative Export: CC BY 4.0 publishing to `/public_archive`
4. Bias Awareness: 5-question checklist

Phase 9 turns each of these from advisory text into measurable, queryable properties — `gold.fairness_scorecards` records the score per brief; `gold.research_summaries.status` blocks promotion if any threshold fails.

---

## Common Operations

```bash
# SSH + tunnels
oc-ssh
gcloud compute ssh openclaw-secure-node --tunnel-through-iap --zone=us-east4-a \
  --project=orphansinthedesert -- -L 18789:localhost:18789 -L 5000:localhost:5000 -N
# Then: http://localhost:18789 (gateway), http://localhost:5000 (MLflow UI)

# Container management (inside VM)
docker ps                            # all three containers
docker logs openclaw -f
docker logs unity-catalog -f
docker logs mlflow-server -f

# Sidecars (Phase 6, idempotent)
~/openclaw/sidecars.sh

# Catalog state (Phase 6)
python3 ~/openclaw/uc_init.py

# Worker CLI (Phase 7+)
python3 ~/openclaw/databricks_worker.py read-recent --table bronze.raw_responses
python3 ~/openclaw/databricks_worker.py drain-buffer

# Integrity engine (Phase 8)
python3 ~/openclaw/integrity_engine.py status
python3 ~/openclaw/integrity_engine.py audit
python3 ~/openclaw/integrity_engine.py seal     # daily 4:55 PM EDT via cron

# VM lifecycle
oc-start  /  oc-stop  /  oc-status  /  oc-snapshot
```

---

## Cost Budget

**Target: ~$22.91/month** (budget alert at $30/month).

| Resource | Monthly |
|---|---|
| e2-standard-2 VM (8 h/day weekdays) | $16.18 |
| 50 GB pd-balanced disk (24/7) | $5.00 |
| Cloud NAT (8 h/day) | $1.20 |
| Egress estimate | $0.53 |

The +$8/mo over the original v4 plan is the e2-medium → e2-standard-2 bump made for Phase 6 sidecar memory headroom. Phases 7–9 add no infrastructure cost (pure code on existing VM + disk).
