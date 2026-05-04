# automatic-doodle / OpenClaw Hardened Agency

## What This Project Is

A cost-optimized, security-hardened AI research agent running on Google Cloud Platform. The project deploys an [OpenClaw](https://ghcr.io/openclaw/openclaw) agent container inside a hardened GCP VM with zero public internet exposure. The agent runs 9AM–5PM EST daily (~$14.82/month) and is accessible only via IAP tunnel.

The GitHub repo name ("automatic-doodle") was auto-assigned and kept — it fits.

---

## Architecture Overview

```
Mac (gcloud CLI + IAP tunnel)
  │
  │  IAP-only SSH  (35.235.240.0/20 → port 22)
  ▼
GCP VM: openclaw-secure-node (e2-medium, us-east4-a, NO public IP)
  │
  │  Startup script mounts persistent disk
  ▼
/mnt/disks/research/  (50GB pd-balanced, auto-delete=NO)
  ├── workspace/      → /home/clawuser/workspace
  ├── logs/           → /home/clawuser/logs
  ├── vector_db/      → /home/clawuser/vector_db
  └── .secrets/.env   → /home/clawuser/.env (read-only)
  │
  ▼
Docker container: openclaw (hardened image)
  - Read-only root filesystem
  - Capabilities: CHOWN + DAC_OVERRIDE only (ALL others dropped)
  - Port 127.0.0.1:18789 (localhost only — access via IAP tunnel)
  - RAM: 1.8GB limit  CPU: 1.8 cores  PIDs: 100 max
```

**Outbound internet**: VM uses Cloud NAT (outbound-only, no inbound) for API calls (Telegram, Exa, GitHub, Docker pulls).

---

## GCP Resources

| Resource | Name | Notes |
|---|---|---|
| Project | `orphansinthedesert` | |
| VM | `openclaw-secure-node` | e2-medium, us-east4-a |
| Disk | `openclaw-secure-node` | 50GB pd-balanced, `auto-delete=no` |
| Service Account | `openclaw-agent-v3@orphansinthedesert.iam.gserviceaccount.com` | logWriter + metricWriter only |
| Firewall | `allow-ssh-iap` | IAP range → port 22, targets SA |
| Router | `openclaw-router` | us-east4 |
| NAT | `openclaw-nat` | 64 ports/VM (cost-optimized) |
| Schedule | `openclaw-day-shift` | 9AM–5PM EST, America/New_York |

---

## Directory Structure

```
automatic-doodle/
├── CLAUDE.md                        # This file
├── README.md                        # Full setup guide
├── MANUAL_COMMANDS.md               # 200+ operational commands
├── Deployment_Checklist.md          # Step-by-step deployment checklist
├── init_agency.sh                   # NEW v4.0 master init script (NOT YET RUN)
│
└── openclaw-intel-agency/           # Files from EARLIER run (v3.0-era)
    ├── infra/
    │   ├── provision.sh             # Older, broader permissions (instanceAdmin.v1)
    │   ├── networking.sh
    │   └── schedule.sh
    ├── instance/
    │   ├── Dockerfile.hardened
    │   └── setup.sh
    └── workspace/
        ├── topics.json              # Research interests config
        └── virtue_prompt.md         # Epistemic guidelines (shorter version)
```

### What `init_agency.sh` generates (when run):
Creates fresh v4.0 scripts in the project root:
- `local_hardening.sh` — Mac-side gcloud hardening + aliases
- `infra/provision.sh` — Improved: minimal IAM roles, no instanceAdmin
- `infra/networking.sh` — IAP firewall + Cloud Router + NAT
- `infra/schedule.sh` — 9AM-5PM resource policy
- `instance/Dockerfile.hardened` — Non-root container, no sudo, no Homebrew
- `instance/setup.sh` — Docker CE install + hardened `docker run` command
- `workspace/topics.json` — Research interests (expanded)
- `workspace/virtue_prompt.md` — Full epistemic guidelines

**The `openclaw-intel-agency/` directory was run with an earlier script. The new `init_agency.sh` has NOT been run yet.**

---

## Key Security Improvements (v3.0 → v4.0)

| Area | v3.0 (openclaw-intel-agency/) | v4.0 (init_agency.sh output) |
|---|---|---|
| IAM | `compute.instanceAdmin.v1` (broad) | `logging.logWriter` + `monitoring.metricWriter` only |
| OAuth scopes | `cloud-platform` (full access) | `logging.write` + `monitoring.write` only |
| Firewall targeting | Tag-based (`openclaw-secure`) | Service account targeting |
| Container filesystem | Writable | Read-only (`--read-only`) |
| Capabilities | Not specified | ALL dropped, add back CHOWN + DAC_OVERRIDE |
| Dockerfile | Included Homebrew (~700MB) | No Homebrew, no build tools |
| Secrets mount | Not read-only | `:ro` read-only mount |
| Disk format guard | Always ran `mkfs.ext4 -F` (data loss bug) | Checks `blkid` first, only formats blank disks |

---

## Agent Integrations (Secrets in `/mnt/disks/research/.secrets/.env`)

| Service | Purpose | Key Name |
|---|---|---|
| Telegram | Chat interface to agent | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| Exa | Web search API | `EXA_API_KEY` |
| GitHub | Repo operations | `GITHUB_TOKEN` |

---

## Research Configuration

**Topics** (`workspace/topics.json`):
- AI Safety & Alignment (high priority)
- Databricks & Unity Catalog (medium)
- Small Language Models / on-device AI (medium)
- Developer Tooling & DevX (low)

**Virtue Protocol** (`workspace/virtue_prompt.md`) — enforces:
1. Devil's Advocate: every brief must include counter-arguments
2. Authority Ranking: tier sources (Tier 1 = docs/papers → Tier 4 = SEO spam)
3. Regenerative Export: publish sanitized summaries to `/public_archive` under CC BY 4.0
4. Bias Awareness: check for confirmation bias, vendor incentives, recency bias

---

## Common Operations

```bash
# Access the VM
oc-ssh   # alias from local_hardening.sh
# or:
gcloud compute ssh openclaw-secure-node --project=orphansinthedesert --zone=us-east4-a --tunnel-through-iap

# Create tunnel to web interface (port 18789)
oc-tunnel
# or:
gcloud compute start-iap-tunnel openclaw-secure-node 18789 --local-host-port=localhost:18789 --zone=us-east4-a --project=orphansinthedesert

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

## Deployment State

- **`openclaw-intel-agency/`**: Deployed with an earlier script (v3.0-era). Running infrastructure uses these configs.
- **`init_agency.sh`**: The improved v4.0 master script. Generates all files fresh. **Has not been run yet.**
- When `init_agency.sh` is run, it creates scripts in the project root (alongside itself), not inside `openclaw-intel-agency/`.

---

## Cost Budget

**Target: $14.82/month** (budget alert at $20/month)

| Resource | Monthly Cost |
|---|---|
| e2-medium VM (8h/day) | $8.09 |
| 50GB pd-balanced disk (24/7) | $5.00 |
| Cloud NAT (8h/day) | $1.20 |
| Egress estimate | $0.53 |

Schedule: `openclaw-day-shift` — auto-start 9AM EST, auto-stop 5PM EST, daily.
Override: `gcloud compute instances add-metadata openclaw-secure-node --zone=us-east4-a --metadata=overtime_active=true`
