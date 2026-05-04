# automatic-doodle / OpenClaw Hardened Agency

## What This Project Is

A cost-optimized, security-hardened AI research agent running on Google Cloud Platform. The project deploys an [OpenClaw](https://ghcr.io/openclaw/openclaw) agent container inside a hardened GCP VM with zero public internet exposure. The agent runs 9AM–5PM EST daily (~$14.82/month) and is accessible only via IAP tunnel.

The GitHub repo name ("automatic-doodle") was auto-assigned and kept — it fits.

---

## Architecture Overview

```
Mac (gcloud CLI + IAP SSH tunnel)
  │
  │  IAP-only SSH  (35.235.240.0/20 → port 22)
  ▼
GCP VM: openclaw-secure-node (e2-medium, us-east4-a, NO public IP)
  │
  │  Startup script mounts persistent disk
  ▼
/mnt/disks/research/  (50GB pd-balanced, auto-delete=NO)
  ├── .openclaw/      → /home/clawuser/.openclaw  (agent config + model auth)
  ├── workspace/      → /home/clawuser/workspace
  ├── logs/           → /home/clawuser/logs
  ├── vector_db/      → /home/clawuser/vector_db
  └── .secrets/.env   → loaded via --env-file (read-only)
  │
  ▼
Docker container: openclaw (hardened image)
  - Read-only root filesystem
  - Non-root user: 1000:1000 (clawuser), HOME=/home/clawuser
  - Capabilities: CHOWN + DAC_OVERRIDE only (ALL others dropped)
  - Port 127.0.0.1:18789 (localhost only — access via SSH port forward)
  - RAM: 1.8GB limit  CPU: 1.8 cores  PIDs: 100 max
```

**Outbound internet**: VM uses Cloud NAT (outbound-only, no inbound) for API calls (Telegram, Exa, GitHub, Docker pulls).

---

## Two-Layer Structure

This repo is split into two composable layers:

| Layer | Directory | Purpose |
|---|---|---|
| Sandbox | `sandbox/gcp/` | GCP cloud infrastructure — VM, disk, IAP, NAT, schedule |
| Application | `openclaw/` | Docker container, agent setup, workspace config |

Each layer has its own README with its interface contract. The sandbox layer is cloud-provider-agnostic by design; `sandbox/README.md` defines what any cloud implementation must provide.

---

## GCP Resources

| Resource | Name | Notes |
|---|---|---|
| Project | `orphansinthedesert` | |
| VM | `openclaw-secure-node` | e2-medium, us-east4-a |
| Disk | `openclaw-secure-node` | 50GB pd-balanced, `auto-delete=no` |
| Service Account | `openclaw-agent-v3@orphansinthedesert.iam.gserviceaccount.com` | logWriter + metricWriter only |
| Firewall | `allow-ssh-iap` | IAP range → port 22, targets SA |
| Firewall | `allow-openclaw-web-iap` | IAP range → port 18789, tag-based |
| Router | `openclaw-router` | us-east4 |
| NAT | `openclaw-nat` | 64 ports/VM (cost-optimized) |
| Schedule | `openclaw-day-shift` | 9AM–5PM EST, America/New_York |

---

## Directory Structure

```
automatic-doodle/
├── CLAUDE.md                        # This file
├── README.md                        # Full setup guide
├── MANUAL_COMMANDS.md               # Operational commands reference
├── Deployment_Checklist.md          # Step-by-step deployment checklist
├── init_agency.sh                   # Master init script — source of truth for all generated files
├── local_hardening.sh               # Mac-side gcloud hardening + shell aliases
│
├── sandbox/                         # Cloud infrastructure layer (host-agnostic)
│   ├── README.md                    # Sandbox interface contract
│   └── gcp/                         # GCP implementation
│       ├── provision.sh             # Creates VM, service account, persistent disk
│       ├── networking.sh            # IAP firewall rules, Cloud NAT
│       └── schedule.sh              # 9AM–5PM auto start/stop policy
│
└── openclaw/                        # Application layer (Docker-agnostic)
    ├── README.md                    # OpenClaw interface contract
    ├── Dockerfile.hardened          # Hardened container image
    ├── setup.sh                     # In-VM: builds image, writes env, starts container
    └── workspace/
        ├── topics.json              # Research topic configuration
        └── virtue_prompt.md         # Epistemic guidelines for the agent
```

### Source of truth

`init_agency.sh` generates all files under `sandbox/` and `openclaw/`. **If a bug exists in a generated file, fix it in `init_agency.sh` and regenerate — never patch the generated file directly.**

---

## Key Security Properties

| Area | Implementation |
|---|---|
| IAM | `logging.logWriter` + `monitoring.metricWriter` only |
| OAuth scopes | `logging.write` + `monitoring.write` only |
| Firewall targeting | SSH rule targets service account; web rule is tag-based |
| Container filesystem | Read-only (`--read-only`) |
| Capabilities | ALL dropped, add back CHOWN + DAC_OVERRIDE |
| User | 1000:1000 (non-root), explicit HOME=/home/clawuser |
| Secrets mount | Read-only (`:ro`) |
| Disk format guard | Checks `blkid` first, only formats blank disks |

---

## Agent Integrations (Secrets in `/mnt/disks/research/.secrets/.env`)

| Service | Purpose | Key Name |
|---|---|---|
| Telegram | Chat interface to agent | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| Gemini | AI model provider | `GEMINI_API_KEY` |
| Exa | Web search API | `EXA_API_KEY` |
| GitHub | Repo read access | `GITHUB_PAT` |

**Model**: `google/gemini-2.5-flash` — configured via `openclaw models auth login --provider google` (auto-detects `GEMINI_API_KEY`).

---

## Research Configuration

**Topics** (`openclaw/workspace/topics.json`):
- AI Safety & Alignment (high priority)
- Databricks & Unity Catalog (medium)
- Small Language Models / on-device AI (medium)
- Developer Tooling & DevX (low)

**Virtue Protocol** (`openclaw/workspace/virtue_prompt.md`) — enforces:
1. Devil's Advocate: every brief must include counter-arguments
2. Authority Ranking: tier sources (Tier 1 = docs/papers → Tier 4 = SEO spam)
3. Regenerative Export: publish sanitized summaries to `/public_archive` under CC BY 4.0
4. Bias Awareness: check for confirmation bias, vendor incentives, recency bias

---

## Common Operations

```bash
# SSH into VM
oc-ssh
# or:
gcloud compute ssh openclaw-secure-node --project=orphansinthedesert --zone=us-east4-a --tunnel-through-iap

# Web UI tunnel (SSH port forward — IAP TCP tunnel doesn't work with 127.0.0.1 bind)
oc-tunnel
# or:
gcloud compute ssh openclaw-secure-node --tunnel-through-iap --zone=us-east4-a --project=orphansinthedesert -- -L 18789:localhost:18789 -N
# Then visit: http://localhost:18789

# VM start/stop
oc-start  /  oc-stop  /  oc-status

# Inside VM: container management
docker ps
docker logs openclaw -f
docker restart openclaw
docker stats openclaw --no-stream

# Emergency VM stop (cost bleed)
gcloud compute instances stop openclaw-secure-node --zone=us-east4-a --project=orphansinthedesert

# Create snapshot backup
oc-snapshot
```

---

## Cost Budget

**Target: ~$14.82/month** (budget alert at $20/month)

| Resource | Monthly Cost |
|---|---|
| e2-medium VM (8h/day, weekdays) | $8.09 |
| 50GB pd-balanced disk (24/7) | $5.00 |
| Cloud NAT (8h/day) | $1.20 |
| Egress estimate | $0.53 |

Schedule: `openclaw-day-shift` — auto-start 9AM EST, auto-stop 5PM EST, daily.
Override: `gcloud compute instances add-metadata openclaw-secure-node --zone=us-east4-a --metadata=overtime_active=true`
