# Secure Container Sandbox

A hardened cloud environment for running containerised workloads with no public IP, least-privilege IAM, and encrypted-tunnel-only access.

## What this layer provides

- **No public IP** — inbound access only via Identity-Aware Proxy (IAP)
- **Cloud NAT** — outbound-only internet for API calls; VM cannot receive inbound connections
- **Persistent encrypted disk** — survives VM restarts; data not tied to VM lifecycle
- **Minimal service account** — scoped to logging and monitoring only
- **Schedule-based runtime** — VM starts at 9AM and stops at 5PM to minimise cost
- **Docker runtime** — pre-installed, ready to run any compliant container

## Output (what the application layer can assume)

Any application installed into this sandbox can assume:
- Docker available at the system level
- Persistent storage mounted at `/mnt/disks/research/` with subdirectories: `workspace/`, `logs/`, `vector_db/`, `.secrets/`, `.openclaw/`
- SSH access via `oc-ssh` (IAP tunnel)
- Port 18789 reachable locally via `oc-tunnel` (SSH port forward)
- Outbound HTTPS to any external API

## Cloud providers

| Provider | Directory | Status |
|---|---|---|
| Google Cloud Platform | `gcp/` | ✅ Production |

## GCP implementation

```
sandbox/gcp/
├── provision.sh    # Creates VM, service account, persistent disk
├── networking.sh   # IAP firewall rules, Cloud NAT
└── schedule.sh     # 9AM–5PM auto start/stop policy
```

### Usage (standalone)

```bash
cd sandbox/gcp
./provision.sh      # Run once — creates all GCP resources
./networking.sh     # Run once — sets up firewall + NAT
./schedule.sh       # Run once — configures schedule policy
```

### Prerequisites

- `gcloud` CLI authenticated with project `orphansinthedesert`
- Billing enabled on the project
- Required APIs enabled (see provision.sh header)

### Cost

~$8–15/month for an e2-standard-2 VM running 8 hours/day weekdays only.

---

To add a new cloud provider, create a new subdirectory (e.g. `sandbox/aws/`) with the same output contract.
