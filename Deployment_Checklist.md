# OpenClaw Hardened Agency v4.0 - Deployment Checklist

End-to-end runbook covering every component of the platform in the order you would deploy them. The first five phases get OpenClaw running on a hardened GCP VM with Telegram + Gemini. Phases 6 onward layer the Databricks OSS stack (Unity Catalog, MLflow, Delta, integrity chain, public commons) on top of that working agent.

Phases 1–6 are deployable today. Phases 7–12 are planned and have full design specs in `openclaw/EXECUTION_*.md`; their runbook sections will replace the placeholders here as each phase is implemented.

---

## ☑️ PRE-DEPLOYMENT (On Your Mac)

### 1. Prerequisites
- [ ] GCP account with billing enabled
- [ ] Project created: `orphansinthedesert`
- [ ] Owner or Editor role on project
- [ ] `gcloud` CLI installed ([install guide](https://cloud.google.com/sdk/docs/install))
- [ ] Authenticated: `gcloud auth login`

Ensure that you are pointed to the correct project, your using the Application Default Credentials, and your componenets are up-to-date.
```bash
gcloud config set project orphansinthedesert
gcloud auth application-default set-quota-project orphansinthedesert
gcloud auth login
gcloud components update
```

### 2. Enable Required APIs
```bash
gcloud services enable compute.googleapis.com
gcloud services enable iap.googleapis.com
gcloud services enable logging.googleapis.com
gcloud services enable monitoring.googleapis.com
```

### 3. Verify Current Billing
```bash
gcloud services enable billingbudgets.googleapis.com --project=orphansinthedesert
gcloud beta billing projects describe orphansinthedesert
# Should show: billingEnabled: true
```
## ☑️ PHASE 1: Local Setup (10 minutes)
### Step 1: Initialize Project
```bash
cd ~/automatic-doodle   # or wherever you cloned the repo
./init_agency.sh
```
Expected output: "ALL SCRIPTS GENERATED SUCCESSFULLY"

### Step 2: Harden Local Environment
```bash
./local_hardening.sh
# When prompted, enter your email for budget alerts
```

### Step 3: Apply Shell Changes
```bash
source ~/.zshrc   # or restart terminal
oc-help           # Verify aliases work
```

Verification:

 Aliases available (oc-help shows command list)
 Budget alert email received (check spam folder)
 ~/.config/gcloud permissions are 700

## ☑️ PHASE 2: GCP Infrastructure (15 minutes)
### Step 4: Provision VM
```bash
cd sandbox/gcp
./provision.sh
```
Expected output: "VM provisioning complete"

Verification:
```bash
gcloud compute instances list
# Should show: openclaw-secure-node | RUNNING | INTERNAL_IP (no external IP)
```

### Step 5: Configure Networking
```bash
./networking.sh
```

Expected output: "Networking configuration complete"

Verification:
```bash
gcloud compute firewall-rules list | grep allow-ssh-iap
gcloud compute routers nats list --router=openclaw-router --region=us-east4
```

### Step 6: Set Schedule
```bash
./schedule.sh
```
Expected output: "Scheduling configuration complete"

Verification:
```bash
gcloud compute resource-policies list
# Should show: openclaw-day-shift | READY
```

##  PHASE 3: VM Configuration (20 minutes)
### Step 7: Copy Files to VM
```bash
gcloud compute ssh openclaw-secure-node --zone=us-east4-a --tunnel-through-iap \
    --command="mkdir -p ~/openclaw/workspace"

gcloud compute scp openclaw/Dockerfile.hardened openclaw-secure-node:~/openclaw/ \
  --zone=us-east4-a \
  --tunnel-through-iap

gcloud compute scp openclaw/setup.sh openclaw-secure-node:~/openclaw/ \
  --zone=us-east4-a \
  --tunnel-through-iap

gcloud compute scp openclaw/workspace/topics.json openclaw-secure-node:~/openclaw/workspace/ \
  --zone=us-east4-a \
  --tunnel-through-iap

gcloud compute scp openclaw/workspace/virtue_prompt.md openclaw-secure-node:~/openclaw/workspace/ \
  --zone=us-east4-a \
  --tunnel-through-iap
```
Alternative: SSH and paste content manually
```bash
oc-ssh
mkdir -p ~/openclaw/workspace
nano ~/openclaw/Dockerfile.hardened  # Paste content
nano ~/openclaw/setup.sh             # Paste content
chmod +x ~/openclaw/setup.sh
```

### Step 8: SSH into VM
```bash
oc-ssh  # Uses alias from local_hardening.sh
# Or: gcloud compute ssh openclaw-secure-node --zone=us-east4-a --tunnel-through-iap
```

### Step 9: Verify Disk Mount
```bash
# Inside VM:
df -h | grep research
# Expected: /dev/sdb mounted at /mnt/disks/research with ~50GB

ls -la /mnt/disks/research
# Expected: workspace/ logs/ vector_db/ .secrets/ directories
```

If disk not mounted:
```bash
sudo cat /var/log/startup-script.log
# Check for errors in mount process
```

### Step 10: Run Setup Script
```bash
# Inside VM:
cd ~/openclaw
./setup.sh
```
Expected duration: 5-8 minutes (Docker install + image build)

Verification:
```bash
docker ps
# Should show: openclaw container with STATUS "Up X seconds"

docker logs openclaw --tail 20
# Should NOT show errors
```

## ☑️ PHASE 4: Secrets Configuration (5 minutes)
### Step 11: Gather API Keys
Telegram Bot Token:

Open Telegram, message @BotFather
Send: /newbot
Follow prompts, copy token
Telegram Chat ID:

Message your bot: "Hello"
Visit: https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
Find "chat":{"id":XXXXXXX}
Gemini API:

Sign up: https://aistudio.google.com/app/apikey
Generate API key from dashboard
Exa API:

Sign up: https://exa.ai
Generate API key from dashboard
GitHub Token:

Visit: https://github.com/settings/tokens
Generate new token (classic)
Scopes: repo, read:org
Expiration: 90 days

### Step 12: Edit .env File
```bash
# Inside VM:
nano /mnt/disks/research/.secrets/.env

# Replace placeholders:
# TELEGRAM_BOT_TOKEN=123456789:ABC...
# TELEGRAM_CHAT_ID=987654321
# GEMINI_API_KEY=AIza...
# EXA_API_KEY=your_exa_api_key_here
# GITHUB_TOKEN=ghp_...

# Save: Ctrl+X, Y, Enter
```

### Step 13: Verify Permissions
```bash
ls -la /mnt/disks/research/.secrets/.env
# Expected: -rw------- (600 permissions, owner only)
```

### Step 14: Recreate Container to Pick Up New .env Values
```bash
# NOTE: docker restart does NOT re-read --env-file. Must stop+rm+rerun to pick up .env changes.
docker stop openclaw && docker rm openclaw && \
docker run -d --name openclaw --restart unless-stopped --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=512m \
  --cap-drop=ALL --cap-add=CHOWN --cap-add=DAC_OVERRIDE \
  --security-opt=no-new-privileges:true --pids-limit=100 \
  --memory=1800m --cpus=1.8 \
  -p 127.0.0.1:18789:18789 \
  -v /mnt/disks/research/workspace:/home/clawuser/workspace:rw \
  -v /mnt/disks/research/logs:/home/clawuser/logs:rw \
  -v /mnt/disks/research/vector_db:/home/clawuser/vector_db:rw \
  -v /mnt/disks/research/.openclaw:/home/clawuser/.openclaw:rw \
  -v /mnt/disks/research/.secrets/.env:/home/clawuser/.env:ro \
  --user 1000:1000 \
  -e HOME=/home/clawuser \
  --env-file /mnt/disks/research/.secrets/.env \
  openclaw-hardened
sleep 10
docker exec openclaw env | grep -i telegram
docker logs openclaw | grep -i "authenticated\|connected\|error"
```
Expected: Real token values visible, no authentication errors for Telegram/Exa/GitHub

## ☑️ PHASE 5: Access & Testing (10 minutes)
### Step 15: Create Tunnel
```bash
# On your Mac (new terminal window — keep it open):
oc-tunnel
# Or explicitly:
# gcloud compute ssh openclaw-secure-node --tunnel-through-iap \
#   --zone=us-east4-a --project=orphansinthedesert -- -L 18789:localhost:18789 -N
```
Note: `oc-tunnel` uses SSH port forwarding (not IAP TCP forwarding) because the
gateway binds to 127.0.0.1 on the VM. Keep this terminal open while using the UI.

### Step 16: Access Web Interface
```bash
# On your Mac:
oc-web
# Or: open http://localhost:18789
```
Expected: OpenClaw web interface loads (may take 10-15 seconds on first access)

### Step 17: Test Telegram Integration
Send a message to your bot from your iPhone/Telegram app:
```text
/start
```
Expected response from bot within 30 seconds

### Step 18: Test Search Capability
Via web interface or Telegram, try:
```text
Search for "latest developments in AI safety research"
```
Expected: Response with sources cited, counter-arguments included

### Step 19: Verify Resource Limits
```bash
# Inside VM (via oc-ssh):
docker stats openclaw --no-stream
```
Expected:

MEM USAGE: < 1.8GB
CPU %: < 180%
PIDS: < 100

## ECURITY AUDIT (5 minutes)
### Step 20: Verify Security Posture
Run these commands inside the VM:
```bash
# 1. Verify no Docker socket mounted
docker inspect openclaw --format='{{json .Mounts}}' | grep docker.sock
# Expected: (no output)

# 2. Verify read-only filesystem
docker exec openclaw touch /test.txt
# Expected: "Read-only file system" error

# 3. Verify capabilities dropped
docker exec openclaw capsh --print | grep Current
# Expected: Only cap_chown, cap_dac_override

# 4. Verify secrets are read-only
docker exec openclaw ls -la /home/clawuser/.env
# Expected: -r--r--r-- (444 permissions)

# 5. Verify no sudo
docker exec openclaw which sudo
# Expected: (no output)

# 6. Verify user is not root
docker exec openclaw id
# Expected: uid=1000(clawuser) gid=1000(clawgroup)
```
All checks MUST pass before production use

## ☑️ COST VERIFICATION (5 minutes)
### Step 21: Check Current Spend
```bash
# On your Mac:
oc-cost
```
Expected daily cost (8 hours):

Compute: ~$0.27/day
Disk: ~$0.17/day
NAT: ~$0.04/day
Total: ~$0.48/day = $14.40/month

### Step 22: Verify Schedule is Active
```bash
gcloud compute instances describe openclaw-secure-node \
  --zone=us-east4-a \
  --format="get(resourcePolicies)"
```
Expected: Should include openclaw-day-shift policy

### Step 23: Test Schedule Override
```bash
# Set overtime flag (prevents auto-stop today)
gcloud compute instances add-metadata openclaw-secure-node \
  --zone=us-east4-a \
  --metadata=overtime_active=true

# Verify it was set
gcloud compute instances describe openclaw-secure-node \
  --zone=us-east4-a \
  --format="get(metadata.items[overtime_active])"

# Reset for normal schedule
gcloud compute instances add-metadata openclaw-secure-node \
  --zone=us-east4-a \
  --metadata=overtime_active=false
```

## ☑️ BACKUP & DISASTER RECOVERY (5 minutes)
### Step 24: Create Initial Snapshot
```bash
# On your Mac:
oc-snapshot
```

Verification:
```bash
gcloud compute snapshots list --filter="sourceDisk:openclaw-secure-node"
# Should show newly created snapshot
```

### Step 25: Document Snapshot Schedule (continued)
Add to calendar/reminders:
- [ ] **Weekly:** Create snapshot every Sunday
- [ ] **Monthly:** Keep one snapshot per month, delete older weeklies
- [ ] **Quarterly:** Rotate all secrets (Telegram, GitHub, Exa tokens)

### Step 26: Test Restore Process (Optional but Recommended)
```bash
# Create test snapshot
gcloud compute disks snapshot openclaw-secure-node \
  --zone=us-east4-a \
  --snapshot-names=test-restore-$(date +%Y%m%d)

# Create new disk from snapshot
gcloud compute disks create openclaw-test-restore \
  --source-snapshot=test-restore-$(date +%Y%m%d) \
  --zone=us-east4-a

# Verify restore worked
gcloud compute disks describe openclaw-test-restore --zone=us-east4-a

# Clean up test resources
gcloud compute disks delete openclaw-test-restore --zone=us-east4-a --quiet
gcloud compute snapshots delete test-restore-$(date +%Y%m%d) --quiet
)
```

## ☑️ OPERATIONAL READINESS (5 minutes)
### Step 27: Create Incident Response Plan
Save this in your password manager or secure notes:
```text
OPENCLAW EMERGENCY CONTACTS
============================
Project ID: orphansinthedesert
VM Name: openclaw-secure-node
Zone: us-east4-a

EMERGENCY SHUTDOWN:
gcloud compute instances stop openclaw-secure-node --zone=us-east4-a

EMERGENCY SNAPSHOT:
gcloud compute disks snapshot openclaw-secure-node --zone=us-east4-a --snapshot-names=emergency-$(date +%Y%m%d-%H%M)

BILLING ALERT EMAIL: [your-email@example.com]

SECRETS LOCATION (INSIDE VM):
/mnt/disks/research/.secrets/.env

BACKUP TOKENS (ENCRYPTED, OFFLINE STORAGE):
- Telegram Bot Token: [stored in 1Password/BitWarden]
- Gemini API Key: [stored in 1Password/BitWarden]
- GitHub PAT: [stored in 1Password/BitWarden]
- Exa API Key: [stored in 1Password/BitWarden]
```

### Step 28: Test Emergency Stop
```bash
# On your Mac:
oc-stop

# Wait 60 seconds, then verify
oc-status
# Expected: TERMINATED

# Restart
oc-start

# Wait 2-3 minutes for boot, then verify
oc-ssh
docker ps  # Container should be running (auto-restart on boot)
```

### Step 29: Bookmark Critical URLs
Add these to your browser bookmarks:

 GCP Console: https://console.cloud.google.com/compute/instances?project=orphansinthedesert
 Billing: https://console.cloud.google.com/billing
 Cloud Logging: https://console.cloud.google.com/logs/query?project=orphansinthedesert
 IAM: https://console.cloud.google.com/iam-admin/iam?project=orphansinthedesert

## ☑️ DOCUMENTATION & HANDOFF
### Step 30: Create Project Wiki/Notes
Document in your knowledge base:

 Why this project was created (research automation, cost-effective AI agent)
 Monthly cost budget: $14.82
 Daily operational hours: 9 AM - 5 PM EST
 Link to this GitHub repo (if you forked it)
 Date deployed: [YYYY-MM-DD]

### Step 31: Knowledge Transfer
If multiple people will manage this:

 Share oc-ssh alias setup with team
 Grant IAM roles to team members (minimum: Compute Viewer + IAP User)
 Share emergency contact info
 Schedule quarterly secrets rotation with team calenda

---

## ☑️ PHASE 6: DATABRICKS OSS STACK — SIDECARS + CATALOG BOOTSTRAP (15 minutes)

This phase adds Unity Catalog OSS and MLflow tracking server as Docker sidecars on the existing VM, and bootstraps the `research` catalog. **Purely additive** — the running OpenClaw container is not touched. Phase 7 will join the agent to the new sidecar network and wire the worker.

**Plan refs:** `openclaw/EXECUTION_databricks_integration.md` (master), Phase 1 of that document.

### Prerequisites
- [ ] Phases 1–5 complete; OpenClaw container is up and Telegram bot is responding
- [ ] VM is `e2-standard-2` (or larger). Verify with:
      ```bash
      gcloud compute instances describe openclaw-secure-node \
        --zone=us-east4-a --format="value(machineType.basename())"
      ```
      If still on `e2-medium`, bump first:
      ```bash
      oc-stop
      gcloud compute instances set-machine-type openclaw-secure-node \
        --machine-type=e2-standard-2 --zone=us-east4-a --project=orphansinthedesert
      oc-start
      ```
- [ ] GCP budget alert raised from $20 → $30 (the VM bump adds ~$8/month if applicable):
      ```bash
      # Find the billing account and budget ID
      BILLING=$(gcloud beta billing projects describe orphansinthedesert \
        --format="value(billingAccountName)" | sed 's|billingAccounts/||')
      BUDGET_ID=$(gcloud beta billing budgets list --billing-account="$BILLING" \
        --filter="displayName:'OpenClaw Monthly Budget'" \
        --format="value(name.basename())")

      # Raise the cap to $30 (threshold rules at 50%/90%/100% are preserved)
      gcloud beta billing budgets update "$BUDGET_ID" \
        --billing-account="$BILLING" --budget-amount=30USD
      ```

### Step 32: Pull the Databricks scripts onto the VM
From your Mac, push the three Phase 6 artifacts:
```bash
gcloud compute scp \
  --zone=us-east4-a --tunnel-through-iap --project=orphansinthedesert \
  sandbox/gcp/sidecars.sh \
  openclaw/uc_init.py \
  openclaw/unity_catalog_setup.sql \
  openclaw-secure-node:~/openclaw/
```
Or, if the repo is cloned on the VM:
```bash
oc-ssh
cd ~/automatic-doodle && git fetch origin && git checkout databrick_init
```

### Step 33: Start the sidecars
On the VM:
```bash
~/openclaw/sidecars.sh
# Or: ~/automatic-doodle/sandbox/gcp/sidecars.sh
```
Expected final lines:
```
✓ Unity Catalog responding at http://localhost:8080
✓ MLflow responding at http://localhost:5000
```

If either fails, inspect logs and re-run after fixing:
```bash
docker logs unity-catalog
docker logs mlflow-server
```
Common gotchas:
- **UC image tag drift.** `unitycatalog/unitycatalog:latest` and the `bin/start-uc-server` entrypoint are the current convention but should be cross-checked against the upstream release if startup fails.
- **Memory pressure on `e2-medium`.** Sidecars use ~768 MB combined; the agent uses 1.8 GB. Bump to `e2-standard-2` if you skipped the prerequisite.

### Step 34: Bootstrap the research catalog
On the VM:
```bash
python3 ~/openclaw/uc_init.py
```
Expected:
```
[catalog] created research
[schema] created research.bronze
[schema] created research.silver
[schema] created research.gold
[schema] created research.public_archive
[schema] created research.audit
✓ catalog 'research' present
✓ 5 schemas present: ['audit', 'bronze', 'gold', 'public_archive', 'silver']
Phase 1 init complete. Tables will be created by the worker on first write.
```
Re-running is safe — every step is idempotent.

### Step 35: (Optional) Open the MLflow UI
```bash
# From your Mac, alongside oc-tunnel on 18789:
gcloud compute ssh openclaw-secure-node \
  --tunnel-through-iap --zone=us-east4-a --project=orphansinthedesert \
  -- -L 5000:localhost:5000 -N
# Visit: http://localhost:5000
```
The UI will be empty until Phase 9 begins logging runs. This step just confirms the server renders.

### Step 36: Capture state and snapshot
```bash
# On the VM: capture catalog state for the operational record
docker exec unity-catalog \
  curl -s http://localhost:8080/api/2.1/unity-catalog/catalogs | python3 -m json.tool

# From your Mac: snapshot the disk now that UC + MLflow data exists on it
oc-snapshot
```

### Phase 6 Sign-off
- [ ] `sidecars.sh` ran cleanly; both health checks pass
- [ ] `uc_init.py` reports the `research` catalog and 5 schemas present
- [ ] MLflow UI loads via SSH port forward
- [ ] OpenClaw container still responsive (Telegram ping returns)
- [ ] Disk snapshot taken
- [ ] Budget alert raised to $30/month

When all six are checked, Phase 6 is complete. Open a Phase 7 branch (`databricks_integrity_engine`) for the next build.

### Phase 6 rollback
```bash
docker stop unity-catalog mlflow-server
docker rm unity-catalog mlflow-server
docker network rm openclaw-net
# Persistent disk data is preserved at /mnt/disks/research/{unity_catalog,mlflow,delta}/.
# Delete those dirs only for a clean slate; otherwise sidecars.sh picks them back up.
```

### Phase 6 cost impact
| Item | Before | After | Delta |
|---|---|---|---|
| VM compute (8h/day weekdays) | $8.09/mo (e2-medium) | $16.18/mo (e2-standard-2) | +$8.09 |
| Persistent disk (50 GB) | $5.00/mo | $5.00/mo | — |
| Cloud NAT + egress | ~$1.73/mo | ~$1.73/mo | — |
| **Total target** | **~$14.82/mo** | **~$22.91/mo** | **+$8.09/mo** |

If the VM was already on `e2-standard-2` (current Phase 1 checkpoint says it is), Phase 6 is **+$0/mo** — sidecars run on RAM and disk you've already paid for.

---

## ☑️ PHASE 7: DATABRICKS WORKER — BRONZE/SILVER WRITES (15 minutes)

**Plan ref:** `openclaw/EXECUTION_databricks_integration.md` Phase 2.

What this phase does: ships `openclaw/databricks_worker.py` — Python that takes a `RawResponse` / `SourceFetch` / `ValidatedBrief` / `Citation` (Pydantic-validated) and writes it to the corresponding Delta table under `/mnt/disks/research/delta/research/{bronze,silver}/...`. Includes a `LocalBuffer` failsafe (writes JSONL when Delta is unreachable) and a stub `_chain_hook` that logs to stderr for now and will be replaced by Phase 8's real `ChainWriter`.

**Phase 7 explicitly defers:**
- **Agent integration.** The worker runs on the VM host, invoked via CLI. Wiring the Node.js OpenClaw gateway to call into Python is a separate architectural decision (add Python to the openclaw container, vs. a separate worker container, vs. a webhook bridge). That decision belongs to a future phase, not Phase 7.
- **UC table registration.** The worker writes Delta files; UC doesn't yet know the tables exist. SQL access via UC is a Phase 7.5 concern.

### Prerequisites
- [ ] Phase 6 complete (UC + MLflow sidecars healthy; `research` catalog + 5 schemas registered)
- [ ] VM has outbound internet (for `pip install`)

### Step 37: Pull Phase 7 files to the VM
```bash
gcloud compute scp \
  --zone=us-east4-a --tunnel-through-iap --project=orphansinthedesert \
  openclaw/databricks_worker.py \
  openclaw/requirements.txt \
  openclaw/__init__.py \
  openclaw-secure-node:~/openclaw/

gcloud compute scp --recurse \
  --zone=us-east4-a --tunnel-through-iap --project=orphansinthedesert \
  openclaw/tests \
  openclaw-secure-node:~/openclaw/
```

### Step 38: Install Python deps on the VM
```bash
oc-ssh
pip install --user -r ~/openclaw/requirements.txt

# Verify:
python3 -c "import deltalake, pydantic, pyarrow; print('deltalake', deltalake.__version__, '| pydantic', pydantic.__version__, '| pyarrow', pyarrow.__version__)"
```
First install pulls ~80 MB of wheels (deltalake's Rust binary + pyarrow). Subsequent runs are cached.

### Step 39: Run the unit test suite on the VM
```bash
pip install --user pytest
cd ~ && python3 -m pytest openclaw/tests/test_databricks_worker.py -v
```
Expected: 14 passing.

### Step 40: Ensure the Delta data root is writable
The worker writes to `/mnt/disks/research/delta/`. UC's data dir is the same path bind-mounted into its container. Confirm the agent's host user can write there:
```bash
sudo chown -R 1000:1000 /mnt/disks/research/delta/research 2>/dev/null || true
sudo mkdir -p /mnt/disks/research/workspace/research_logs
sudo chown -R "$(id -u):$(id -g)" /mnt/disks/research/workspace/research_logs
```

### Step 41: Smoke-test bronze write
```bash
cat <<'EOF' | python3 ~/openclaw/databricks_worker.py record-response
{
  "response_id": "test-resp-001",
  "created_at": "2026-05-07T22:45:00Z",
  "agent_version": "openclaw-v4.0",
  "model_id": "google/gemini-2.5-flash",
  "prompt_hash": "0000000000000000000000000000000000000000000000000000000000000000",
  "response_text": "Phase 7 smoke test response.",
  "response_hash": "1111111111111111111111111111111111111111111111111111111111111111",
  "mlflow_run_id": "smoke-run-001",
  "topic_id": "ai-safety"
}
EOF
```
Expected stderr line: `CHAIN_HOOK operation=INSERT table=research.bronze.raw_responses row_id=test-resp-001 payload_hash=<64 hex>`

### Step 42: Verify Delta files and round-trip read
```bash
ls -la /mnt/disks/research/delta/research/bronze/raw_responses/
# Expect: _delta_log/ directory plus one or more .parquet files

python3 ~/openclaw/databricks_worker.py read-recent --table bronze.raw_responses --limit 5
# Expect: JSON array containing the test row written above
```

### Step 43: LocalBuffer failsafe sanity check
Force a write failure by pointing the worker at a bad Delta root, then drain:
```bash
DELTA_ROOT=/dev/null/intentionally-bad python3 ~/openclaw/databricks_worker.py record-response <<'EOF'
{"response_id":"buf-001","created_at":"2026-05-07T22:46:00Z","agent_version":"v4.0","model_id":"google/gemini-2.5-flash","prompt_hash":"00...","response_text":"buffered","response_hash":"11...","mlflow_run_id":"r"}
EOF
# Expect stderr: "WARN: Delta write failed... buffering to /mnt/disks/research/workspace/research_logs/<date>/..."

ls /mnt/disks/research/workspace/research_logs/$(date -u +%Y-%m-%d)/
# Expect: bronze__raw_responses.jsonl present

python3 ~/openclaw/databricks_worker.py drain-buffer
# Expect: "drained 1 rows"
```

### Phase 7 Sign-off
- [ ] `pip install -r requirements.txt` completes without errors
- [ ] All 14 unit tests pass on the VM
- [ ] Smoke `record-response` writes succeed and emit a `CHAIN_HOOK` line
- [ ] Delta files visible at `/mnt/disks/research/delta/research/bronze/raw_responses/`
- [ ] `read-recent` returns the smoke-test row
- [ ] `LocalBuffer` round-trip works (write-while-broken → drain when fixed)
- [ ] OpenClaw container still responsive (`docker ps` shows it healthy)

When all seven are checked, Phase 7 is complete. Open a Phase 8 branch (`databricks_integrity_engine`) for the next build — the chain hook stub gets replaced with a real append to `research.audit.integrity_chain`.

### Phase 7 rollback
The worker is purely additive — running it does not modify the OpenClaw container or any Phase 6 sidecar. To roll back:
```bash
# Optionally remove smoke-test rows by deleting the test Delta tables
sudo rm -rf /mnt/disks/research/delta/research/bronze/raw_responses
sudo rm -rf /mnt/disks/research/workspace/research_logs/$(date -u +%Y-%m-%d)
# Uninstall Python deps (rarely needed):
pip uninstall -y deltalake pyarrow pydantic
```

### Phase 7 cost impact
**$0/mo.** Worker runs on existing VM compute; Delta files share the existing 50 GB persistent disk. No new GCP resources.

---

## ☑️ PHASE 8: INTEGRITY ENGINE — GENESIS + CHAIN + DAILY SEAL (20 minutes)

**Plan ref:** `openclaw/EXECUTION_integrity_engine.md`.

What this phase does: ships `openclaw/integrity_engine.py` — a linked-hash chain (SHA-256, salted), genesis bootstrap, daily Merkle seal, and an audit utility that pinpoints the exact `sequence_id` of any tamper. When `SECRET_SALT` is present in the agent's environment, Phase 7's `DatabricksWorker._chain_hook` automatically delegates to the real `ChainWriter` instead of logging a stub line — every bronze and silver write now produces an append-only chain entry whose `row_signature` includes the prior entry's signature.

### Prerequisites
- [ ] Phase 7 complete on the VM (`databricks_worker.py` installed, smoke-tested)
- [ ] You have ~5 minutes of focus to generate and store `SECRET_SALT` carefully — it cannot be rotated in v1, and losing it makes existing chain entries unverifiable

### Step 44: Pull Phase 8 files to the VM
```bash
gcloud compute scp \
  --zone=us-east4-a --tunnel-through-iap --project=orphansinthedesert \
  openclaw/integrity_engine.py \
  openclaw-secure-node:~/openclaw/

gcloud compute scp \
  --zone=us-east4-a --tunnel-through-iap --project=orphansinthedesert \
  openclaw/tests/test_integrity_engine.py \
  openclaw-secure-node:~/openclaw/tests/
```

### Step 45: Generate and store `SECRET_SALT`
```bash
oc-ssh

# Generate a 64-char hex salt (32 bytes of entropy)
NEW_SALT=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# Append to the secrets .env (read-only mounted into openclaw container)
sudo bash -c "grep -q '^SECRET_SALT=' /mnt/disks/research/.secrets/.env && \
  sed -i 's|^SECRET_SALT=.*|SECRET_SALT=$NEW_SALT|' /mnt/disks/research/.secrets/.env || \
  echo 'SECRET_SALT=$NEW_SALT' >> /mnt/disks/research/.secrets/.env"

# Confirm it's there:
sudo grep '^SECRET_SALT=' /mnt/disks/research/.secrets/.env
```

**Critical:** Back up this salt to your password manager NOW. Losing it means
historical chain entries can't be verified individually (though external seal
verification via Phase 12's public seal log will still work).

### Step 46: Run unit tests on the VM
```bash
cd ~ && python3 -m pytest openclaw/tests/test_integrity_engine.py -v
```
Expected: 22 passing.

### Step 47: Bootstrap the chain — write the genesis block
```bash
# Export salt for the CLI invocation (env var in this shell only).
export SECRET_SALT=$(sudo grep '^SECRET_SALT=' /mnt/disks/research/.secrets/.env | cut -d= -f2)

# Make sure the genesis-anchor directory is writable
sudo mkdir -p /mnt/disks/research/audit
sudo chown -R "$(id -u):$(id -g)" /mnt/disks/research/audit

python3 ~/openclaw/integrity_engine.py init \
  --project openclaw-public-commons \
  --agent-version openclaw-v4.0
```
Expected output:
```
✓ genesis written
  sequence_id:         0
  target_payload_hash: <64 hex>
  row_signature:       <64 hex>
  anchor written to:   /mnt/disks/research/audit/genesis.json
```

### Step 48: Verify the chain is intact
```bash
python3 ~/openclaw/integrity_engine.py audit
# Expected: ✓ Chain INTACT: 1 entries verified up to sequence_id=0

python3 ~/openclaw/integrity_engine.py status
# Expected: chain length: 1 / first sequence: 0 / last sequence: 0 / genesis row: ...
```

### Step 49: Smoke-test wired chain hook (Phase 7 + Phase 8)
With `SECRET_SALT` exported, the worker now writes real chain entries on every Delta write:
```bash
cat <<'EOF' | python3 ~/openclaw/databricks_worker.py record-response
{
  "response_id": "phase8-smoke-001",
  "created_at": "2026-05-07T22:55:00Z",
  "agent_version": "openclaw-v4.0",
  "model_id": "google/gemini-2.5-flash",
  "prompt_hash": "0000000000000000000000000000000000000000000000000000000000000000",
  "response_text": "Phase 8 wired-chain smoke test.",
  "response_hash": "1111111111111111111111111111111111111111111111111111111111111111",
  "mlflow_run_id": "phase8-run-001",
  "topic_id": "ai-safety"
}
EOF
# Note: NO "CHAIN_HOOK ..." line on stderr now — the hook wrote a real entry instead.

python3 ~/openclaw/integrity_engine.py status
# Expected: chain length: 2 (genesis + the new INSERT)

python3 ~/openclaw/integrity_engine.py audit
# Expected: ✓ Chain INTACT: 2 entries verified up to sequence_id=1

python3 ~/openclaw/databricks_worker.py read-recent --table audit.integrity_chain --limit 5
# Expected: JSON with the genesis row (sequence_id=0, operation=GENESIS) and
# the INSERT row (sequence_id=1, operation=INSERT, target_table=research.bronze.raw_responses)
```

### Step 50: Tamper-detection drill
A sanity check that the auditor actually catches what it's supposed to.
```bash
# Read out an existing INSERT entry's sequence_id and write a deliberately
# bad row directly to the chain (bypassing the worker), then audit:
python3 - <<'PY'
import os
from datetime import datetime, timezone
from openclaw.integrity_engine import (
    Salter, Signer, ChainStore, ChainEntry, ChainOperation,
)
from openclaw.databricks_worker import DeltaTableWriter

salter = Salter()
signer = Signer(salter)
store = ChainStore(DeltaTableWriter())
latest = store.read_latest()
print(f"current chain length: {latest.sequence_id + 1}")

# Forge an entry with a valid signature relative to the WRONG previous_hash
# (i.e., it claims the chain history is something it isn't).
bad = ChainEntry(
    sequence_id=latest.sequence_id + 1,
    recorded_at=datetime.now(timezone.utc),
    author_identity="adversarial-test",
    operation=ChainOperation.INSERT,
    target_table="research.bronze.raw_responses",
    target_row_id="forged-001",
    target_payload_hash="f"*64,
    previous_hash="0"*64,  # WRONG — should be latest.row_signature
    row_signature=signer.sign("f"*64, "0"*64, salter.current_version),
    salt_version=salter.current_version,
)
store.append(bad)
print(f"forged entry appended at sequence_id={bad.sequence_id}")
PY

python3 ~/openclaw/integrity_engine.py audit
# Expected: ✗ TAMPER DETECTED at sequence_id=<the forged seq>
#           Reason: previous_hash does not match prior row's row_signature
```

**If this prints `Chain INTACT`, something is wrong with the auditor — investigate before proceeding.**

Clean up the forged entry by restoring from the snapshot taken in Step 36, or by deleting the `audit/integrity_chain/` Delta table and re-running `init`. (For Phase 8 v1, there's no built-in chain truncation — re-init is the recovery path during testing.)

### Step 51: Daily seal smoke test
```bash
python3 ~/openclaw/integrity_engine.py seal
# If today's chain has at least one INSERT/UPDATE/DELETE row:
#   ✓ daily seal written
#   sequence_id: <N+1>, target_payload_hash: <hex>, row_signature: <hex>
# If today has only the GENESIS or no rows:
#   no chain entries on YYYY-MM-DD; nothing to seal

python3 ~/openclaw/integrity_engine.py audit
# Expected: ✓ Chain INTACT (the new CHECKPOINT row verifies cleanly)

# Idempotency check — running seal again is a no-op.
python3 ~/openclaw/integrity_engine.py seal
# Expected: same output as first run; chain length unchanged
```

### Step 52: Wire the seal into the daily schedule (4:55 PM EDT)
The architectural decision (per `EXECUTION_integrity_engine.md`) is to fire the seal as the agent's last action before VM auto-stop at 5 PM EDT. For Phase 8 v1 deployment, schedule a host-cron entry:
```bash
oc-ssh
crontab -e
# Add this line (4:55 PM EDT = 20:55 UTC during DST, 21:55 UTC during EST):
55 20 * * 1-5 SECRET_SALT=$(grep '^SECRET_SALT=' /mnt/disks/research/.secrets/.env | cut -d= -f2) /usr/bin/python3 /home/$(whoami)/openclaw/integrity_engine.py seal >> /mnt/disks/research/logs/seal.log 2>&1
```
Verify after 4:55 PM by checking `/mnt/disks/research/logs/seal.log` and re-running `audit`.

### Phase 8 Sign-off
- [ ] `SECRET_SALT` generated, stored in `.env`, **backed up to password manager**
- [ ] All 22 integrity-engine unit tests pass on the VM
- [ ] `init` succeeds; genesis row visible at `sequence_id=0`
- [ ] `audit` reports `Chain INTACT`
- [ ] `databricks_worker.py record-response` no longer prints `CHAIN_HOOK ...` on stderr (hook is real now)
- [ ] Worker writes show up as INSERT entries in the chain (`read-recent --table audit.integrity_chain`)
- [ ] Tamper-detection drill succeeds (forged entry → audit flags exact sequence_id)
- [ ] Daily seal smoke test succeeds + idempotent on second invocation
- [ ] Cron entry added for 4:55 PM EDT daily seal
- [ ] `genesis.json` anchor file present at `/mnt/disks/research/audit/genesis.json` (also worth backing up alongside the salt)
- [ ] OpenClaw container still healthy

When all 11 are checked, Phase 8 is complete. Next branch: `databricks_fairness_scorecard` (Phase 9 — fairness metrics + MLflow run logging).

### Phase 8 rollback
**Important:** rolling back Phase 8 invalidates any chain entries already written.
```bash
# Option A: leave SECRET_SALT in env, just disable the worker's chain wiring
unset SECRET_SALT  # for current shell only — agent reads from .env on each run

# Option B: full reset (loses chain history)
sudo rm -rf /mnt/disks/research/delta/research/audit/integrity_chain
sudo rm /mnt/disks/research/audit/genesis.json
# Then re-run init when ready to restart.
```

### Phase 8 cost impact
**$0/mo.** Pure code on existing VM compute and persistent disk. Chain entries are tiny (a few KB per row); even 10K writes/day is under 50 MB/year of additional disk usage.

---

## ☑️ PHASE 9: FAIRNESS SCORECARD + MLflow TRACKING (15 minutes)

**Plan ref:** `openclaw/EXECUTION_databricks_integration.md` Phase 3 + fairness threshold table.

What this phase does: ships `openclaw/fairness_scorer.py` (six metrics, deterministic, pure Python) and `openclaw/mlflow_tracker.py` (thin facade over the MLflow client started in Phase 6). Wires both into `DatabricksWorker.record_validated_brief()` so calling it now produces the full bronze→silver→gold pipeline:

1. Silver writes (Phase 7) — `validated_briefs` + `citations`
2. Fairness scorecard computed (Phase 9 — this phase)
3. `gold.fairness_scorecards` written
4. Status decided (`DRAFT` if all six thresholds pass, `REJECTED` otherwise — never `PROMOTED`; that's Phase 10)
5. `gold.research_summaries` written with the status + failed_thresholds
6. `gold.run_summaries` written when run_summary supplied
7. MLflow run logged when tracker is wired
8. Each write generates an integrity-chain entry (Phase 8)

Threshold defaults (overridable via `FAIRNESS_*` env vars):
| Metric | Default | Notes |
|---|---|---|
| `t1_t2_min_share` | 0.60 | T1+T2 citations as share of total |
| `vendor_diversity_min` | 3 | Distinct vendor_orgs; bypassable with `single_vendor_justification` topic flag |
| `counter_argument_min_ratio` | 0.15 | counter-arg words / total brief words |
| `recency_min_share` | 0.50 | Citations within 18 months; bypassable with `historical_topic` topic flag |
| `bias_check_complete` | all 5 answered | Free-text answers to the virtue prompt's checklist |
| `paid_content_disclosed` | informational | Mirrors the bias-check question 4 answer |

### Prerequisites
- [ ] Phase 7 complete (worker installed; bronze/silver smoke test passes)
- [ ] Phase 8 complete (chain wired; `audit` reports intact)
- [ ] Phase 6 sidecars healthy (UC + MLflow responding)

### Step 53: Pull Phase 9 files to the VM
```bash
gcloud compute scp \
  --zone=us-east4-a --tunnel-through-iap --project=orphansinthedesert \
  openclaw/fairness_scorer.py \
  openclaw/mlflow_tracker.py \
  openclaw/requirements.txt \
  openclaw-secure-node:~/openclaw/

gcloud compute scp \
  --zone=us-east4-a --tunnel-through-iap --project=orphansinthedesert \
  openclaw/tests/test_fairness_scorer.py \
  openclaw/tests/test_mlflow_tracker.py \
  openclaw-secure-node:~/openclaw/tests/
```
Phase 9 also modifies `databricks_worker.py` (adds `fairness_scorer` and `mlflow_tracker` constructor args, expands `record_validated_brief` to write gold tables). Push the updated worker too:
```bash
gcloud compute scp \
  --zone=us-east4-a --tunnel-through-iap --project=orphansinthedesert \
  openclaw/databricks_worker.py \
  openclaw-secure-node:~/openclaw/
```

### Step 54: Install MLflow client on the VM
```bash
oc-ssh
pip install --user -r ~/openclaw/requirements.txt
# Already installs deltalake/pydantic/pyarrow; mlflow is the new entry.

python3 -c "import mlflow; print('mlflow', mlflow.__version__)"
# Expected: mlflow 2.16.x (must match the server pinned in sidecars.sh)
```

### Step 55: Run unit tests on the VM
```bash
cd ~ && python3 -m pytest openclaw/tests/test_fairness_scorer.py openclaw/tests/test_mlflow_tracker.py -v
```
Expected: 35 passing (27 fairness + 8 MLflow tracker).

### Step 56: Smoke-test the wired pipeline
End-to-end: bronze → silver → fairness → gold → MLflow → chain. Run as a single Python invocation so the MLflow run lifecycle is correct:
```bash
oc-ssh
export SECRET_SALT=$(grep '^SECRET_SALT=' /mnt/disks/research/.secrets/.env | cut -d= -f2)
export MLFLOW_TRACKING_URI=http://localhost:5000

python3 - <<'PY'
import uuid
from datetime import date, datetime, timezone

from openclaw.databricks_worker import (
    Citation, DatabricksWorker, ValidatedBrief, RawResponse, SourceFetch,
)
from openclaw.fairness_scorer import BiasChecklist, FairnessScorer, RunSummary
from openclaw.mlflow_tracker import MLflowTracker

now = datetime.now(timezone.utc)

worker = DatabricksWorker(
    fairness_scorer=FairnessScorer(),
    mlflow_tracker=MLflowTracker(),
)

brief_id = str(uuid.uuid4())
response_id = str(uuid.uuid4())

# Bronze writes
worker.record_raw_response(RawResponse(
    response_id=response_id, created_at=now,
    agent_version="openclaw-v4.0",
    model_id="google/gemini-2.5-flash",
    prompt_hash="0"*64, response_text="phase 9 smoke",
    response_hash="1"*64, mlflow_run_id="placeholder",
    topic_id="ai-safety",
))

# Silver + fairness + gold + MLflow
brief = ValidatedBrief(
    brief_id=brief_id, response_id=response_id, created_at=now,
    topic_id="ai-safety", title="Phase 9 smoke test",
    key_findings="x " * 200,
    counter_arguments="counter " * 50,
    practical_implications="x", further_reading="x",
    content_hash="2"*64, agent_version="openclaw-v4.0",
)
citations = [
    Citation(
        citation_id=str(uuid.uuid4()), brief_id=brief_id,
        url=f"https://example.com/{i}", title="src",
        authority_tier=t, vendor_org=v,
        publication_date=date.today(), is_paid_content=False,
    )
    for i, (t, v) in enumerate([(1, "Anthropic"), (1, "Google"), (2, "Databricks")])
]
bias = BiasChecklist(
    only_confirming_sources="No, searched broadly.",
    competing_perspectives_included="Yes, two competitors covered.",
    limitations_acknowledged="Yes, dataset gaps noted.",
    financial_incentive_disclosed="Yes, vendor blog flagged.",
    publication_date_checked="Yes, all within 12 months.",
)

# Open a single MLflow run to wrap the writes
with worker.mlflow_tracker.run(
    topic="ai-safety",
    model_id="google/gemini-2.5-flash",
    prompt_version="virtue-prompt-v1",
    agent_version="openclaw-v4.0",
) as mlflow_run_id:
    run_summary = RunSummary(
        run_summary_id=str(uuid.uuid4()),
        mlflow_run_id=mlflow_run_id,
        brief_id=brief_id,
        started_at=now, ended_at=now,
        model_id="google/gemini-2.5-flash",
        prompt_version="virtue-prompt-v1",
        agent_version="openclaw-v4.0",
        input_tokens=1500, output_tokens=600, total_cost_usd=0.002,
        source_count=3, unique_domain_count=3,
        latency_seconds=12.0, retry_count=0,
    )
    result = worker.record_validated_brief(
        brief, citations, bias_check=bias, run_summary=run_summary,
    )

print(f"status:            {result['status']}")
print(f"threshold_pass:    {result['scorecard'].threshold_pass}")
print(f"failed_thresholds: {result['scorecard'].failed_thresholds}")
print(f"mlflow_run_id:     {mlflow_run_id}")
PY
```
Expected output:
```
status:            DRAFT
threshold_pass:    True
failed_thresholds: []
mlflow_run_id:     <uuid>
```

### Step 57: Verify the writes round-trip
```bash
python3 ~/openclaw/databricks_worker.py read-recent --table gold.fairness_scorecards --limit 3
python3 ~/openclaw/databricks_worker.py read-recent --table gold.research_summaries --limit 3
python3 ~/openclaw/databricks_worker.py read-recent --table gold.run_summaries --limit 3

# Chain entries for these writes also exist:
python3 ~/openclaw/integrity_engine.py audit
# Expected: ✓ Chain INTACT — count grew by ~6 entries (1 silver brief +
#                            citations + 1 scorecard + 1 summary + 1 run_summary)
```

### Step 58: Verify in MLflow UI
With your existing port-forward (`-L 5000:localhost:5000`) open, visit
`http://localhost:5000`. You should see an `openclaw-research` experiment with the smoke-test run, including:

- Params: `topic`, `model_id`, `prompt_version`, `agent_version`
- Metrics: `source_tier_t1_pct`, `vendor_diversity_count`, `counter_argument_ratio`, `recency_within_18mo_pct`, `input_tokens`, `output_tokens`, `total_cost_usd`, `latency_seconds`
- Tags: `threshold_pass=True`, `bias_check_complete=True`

### Step 59: REJECTED-path drill
Run the same script but with a brief that fails several thresholds, and confirm it lands as REJECTED with the right failure list:
```bash
python3 - <<'PY'
import uuid
from datetime import date, datetime, timezone
from openclaw.databricks_worker import Citation, DatabricksWorker, ValidatedBrief
from openclaw.fairness_scorer import FairnessScorer

worker = DatabricksWorker(fairness_scorer=FairnessScorer(), mlflow_tracker=None)
brief_id = str(uuid.uuid4())
brief = ValidatedBrief(
    brief_id=brief_id, response_id=str(uuid.uuid4()),
    created_at=datetime.now(timezone.utc),
    topic_id="db-tech", title="Failure drill",
    key_findings="x " * 800,
    counter_arguments="x"*100,  # technically passes the silver length check but ratio is tiny
    practical_implications="x", further_reading="x",
    content_hash="3"*64, agent_version="openclaw-v4.0",
)
# All T4 sources from one vendor with old dates
citations = [
    Citation(
        citation_id=str(uuid.uuid4()), brief_id=brief_id,
        url=f"https://blog.example/{i}", title="t",
        authority_tier=4, vendor_org="OnlyVendor",
        publication_date=date(2024, 1, 1),
        is_paid_content=True, t4_justification="testing",
    ) for i in range(3)
]
result = worker.record_validated_brief(brief, citations, bias_check=None)
print(f"status:            {result['status']}")
print(f"failed_thresholds: {result['scorecard'].failed_thresholds}")
PY
```
Expected:
```
status:            REJECTED
failed_thresholds: ['source_tier_t1_t2_share', 'vendor_diversity', 'counter_argument_ratio', 'recency', 'bias_check_complete']
```

The brief is still recorded — `gold.research_summaries` has it with `status=REJECTED` and `rejection_reasons` filled in. A human can override in Phase 10.

### Phase 9 Sign-off
- [ ] `pip install` adds mlflow without errors
- [ ] All 35 fairness + MLflow tests pass on the VM
- [ ] Smoke test completes; result reports `status=DRAFT, threshold_pass=True`
- [ ] `read-recent --table gold.fairness_scorecards` returns the smoke row
- [ ] `read-recent --table gold.research_summaries` shows the DRAFT entry
- [ ] `read-recent --table gold.run_summaries` shows the operational metrics
- [ ] MLflow UI shows the run with all six fairness metrics + token counts
- [ ] Integrity chain audit still reports `INTACT` (Phase 8 invariant preserved)
- [ ] REJECTED-path drill produces a multi-failure entry with correct `failed_thresholds`

When all 9 are checked, Phase 9 is complete. Next branch: `databricks_promotion_cli` (Phase 10 — David promotes DRAFT briefs to `public_archive.published`).

### Phase 9 cost impact
**$0/mo.** Pure code on existing VM compute and persistent disk. MLflow's sqlite backend grows ~10 KB per run; even 50 runs/day is under 200 MB/year.

---

## ☑️ PHASE 10: PROMOTION CLI (15 minutes)

**Plan ref:** `openclaw/EXECUTION_databricks_integration.md` Phase 4.

What this phase does: ships `openclaw/promote.py` — David's CLI for reviewing and promoting briefs. Phase 10 is the **only** code path that produces `status='PROMOTED'`; the agent has no way to reach this surface. Promotion writes three rows in append-only fashion (the integrity chain stays consistent and `delta-rs` UPDATE limitations are bypassed):

1. New `gold.research_summaries` row with `status=PROMOTED`
2. New `public_archive.published` row with the rendered markdown + content_hash
3. New `audit.promotion_log` row with prior/new status + override reason if any

Each write fires the Phase 8 chain hook tagged with the reviewer's identity, so the integrity audit can distinguish `author_identity=openclaw-agent` from `author_identity=david` in the chain.

**Phase 10 explicitly defers:**
- **Physical principal separation.** Phase 10 v1 trusts the operator's `--reviewer` arg; the chain records it but doesn't enforce that promotions actually came from a separate UC token. A future phase introduces a separate `SECRET_SALT` / token for human reviewers, with mismatch detection.
- **`UPDATE` semantics.** Append-only is the design choice (every status change is a new row, the latest by `created_at` wins). Cleaner with the integrity chain and avoids `delta-rs`'s Spark-only UPDATE.

### Subcommands
```
promote list [--status DRAFT|REJECTED|PROMOTED] [--limit N]
promote show <summary_id>
promote approve <summary_id> [--reviewer NAME] [--override REASON] [--export PATH]
promote reject  <summary_id> --reason TEXT [--reviewer NAME]
promote history [--limit N]
```

### Prerequisites
- [ ] Phase 9 complete (gold tables exist; DRAFT/REJECTED briefs visible)
- [ ] `SECRET_SALT` exported (so promotions get chained per Phase 8)

### Step 60: Pull Phase 10 files to the VM
```bash
gcloud compute scp \
  --zone=us-east4-a --tunnel-through-iap --project=orphansinthedesert \
  openclaw/promote.py \
  openclaw-secure-node:~/openclaw/

gcloud compute scp \
  --zone=us-east4-a --tunnel-through-iap --project=orphansinthedesert \
  openclaw/tests/test_promote.py \
  openclaw-secure-node:~/openclaw/tests/
```

### Step 61: Run unit tests on the VM
```bash
oc-ssh
cd ~ && python3 -m pytest openclaw/tests/test_promote.py -v
```
Expected: 16 passing.

### Step 62: List DRAFT briefs awaiting review
```bash
export SECRET_SALT=$(grep '^SECRET_SALT=' /mnt/disks/research/.secrets/.env | cut -d= -f2)

python3 ~/openclaw/promote.py list --status DRAFT
# Expected: one or more rows from the Phase 9 smoke tests
```
Show one in detail:
```bash
python3 ~/openclaw/promote.py show <summary_id_from_list>
```

### Step 63: Approve a DRAFT and export the markdown
```bash
python3 ~/openclaw/promote.py approve <summary_id> \
  --reviewer david \
  --export /tmp/published-brief.md

# Expected:
#   ✓ promoted summary_id=<id>
#     published_id:  pub-<12 hex>
#     content_hash:  <64 hex>
#     reviewer:      david
#     markdown:      written to /tmp/published-brief.md

# Sanity-check the content_hash
python3 -c "import hashlib; print(hashlib.sha256(open('/tmp/published-brief.md','rb').read()).hexdigest())"
# Should match the printed content_hash above.
```

### Step 64: Override-promote a REJECTED brief
Use a REJECTED summary from the Phase 9 REJECTED-path drill:
```bash
python3 ~/openclaw/promote.py list --status REJECTED

python3 ~/openclaw/promote.py approve <rejected_summary_id> \
  --reviewer david \
  --override "niche topic — single-vendor citations are appropriate here"
```

Without `--override`, the CLI refuses with `--override REASON is required`. With it, the promotion proceeds and the override reason is recorded in `audit.promotion_log` and embedded in the published markdown's frontmatter.

### Step 65: Reject a DRAFT explicitly
```bash
python3 ~/openclaw/promote.py reject <summary_id> \
  --reason "factual error in claim 3 — see source mismatch" \
  --reviewer david
```
This appends a new `gold.research_summaries` row with `status=REJECTED` and the reviewer's reason. Future `list --status DRAFT` won't show it.

### Step 66: Audit the promotion log
```bash
python3 ~/openclaw/promote.py history --limit 20
# Expected: one line per promotion/rejection, newest first.

python3 ~/openclaw/integrity_engine.py audit
# Expected: ✓ Chain INTACT — promotion writes added 3 entries each
```

### Step 67: Verify the public_archive table
```bash
python3 ~/openclaw/databricks_worker.py read-recent --table public_archive.published --limit 5
# Expected: row(s) with markdown_export field containing the rendered brief
```

### Phase 10 Sign-off
- [ ] All 16 promote tests pass on the VM
- [ ] `promote list --status DRAFT` returns the Phase 9 smoke briefs
- [ ] `promote show <id>` displays brief + scorecard + history
- [ ] `promote approve <id>` succeeds; markdown exported; content_hash matches `sha256` of file
- [ ] Override-promotion of a REJECTED brief refused without `--override`, accepted with it
- [ ] `promote reject <id> --reason ...` writes a new REJECTED row
- [ ] `promote history` shows entries with correct prior/new status transitions
- [ ] Integrity chain audit reports `INTACT` after promotions
- [ ] `read-recent --table public_archive.published` returns the rendered brief

When all 9 are checked, Phase 10 is complete. Next branch (or main, depending on cadence): Phase 11 (adversarial validation suite — exercising the boundary every prior phase asserts).

### Phase 10 cost impact
**$0/mo.** Pure code on existing VM compute and persistent disk.

---

## ☑️ PHASE 11: ADVERSARIAL VALIDATION SUITE (15 minutes)

**Plan refs:** `openclaw/EXECUTION_databricks_integration.md` Phase 5; `openclaw/EXECUTION_integrity_engine.md` (chain invariants).

What this phase does: ships **two complementary verification surfaces** for the boundaries asserted across Phases 7-10:

1. `openclaw/tests/test_adversarial.py` — 20 pytest tests covering cross-cutting attack scenarios that the per-module tests don't reach (code-boundary statics, end-to-end attack flows, replay attacks, content-hash tamper detection, forged chain-entry detection).

2. `openclaw/adversarial_drills.py` — operator CLI that runs the same boundary checks against the **live** Delta data on the VM. Each drill prints PASS/FAIL with details:
    - `boundary-check` — static analysis: no agent module imports promote.py; only promote.py constructs `status='PROMOTED'`
    - `promotion-blocked` — DatabricksWorker has no method matching `promote`/`publish`/`approve`
    - `pydantic-invariants` — short counter_arguments and unjustified T4 citations are rejected at construction time
    - `chain-audit` — runs the integrity audit; passes only if INTACT
    - `markdown-integrity <published_id>` — recomputes SHA-256 of stored markdown_export, compares to content_hash
    - `all` — runs every drill except markdown-integrity, prints summary

**Phase 11 deliberately does NOT introduce new features.** It's the verification layer. Every prior phase asserted invariants; Phase 11 confirms they hold.

Phase 11 also hot-fixes a latent issue from earlier phases: `python3 ~/openclaw/<script>.py` invocations from `~` could not import sibling modules under the `openclaw` package because `sys.path[0]` was set to `openclaw/` rather than its parent. Each affected CLI (`integrity_engine.py`, `promote.py`, `adversarial_drills.py`) now bootstraps its parent directory onto `sys.path` so direct script invocation works regardless of cwd.

### Prerequisites
- [ ] Phase 10 complete (promotion CLI installed; at least one PROMOTED entry in `public_archive.published`)
- [ ] `SECRET_SALT` exported (so `chain-audit` can run)

### Step 68: Pull Phase 11 files to the VM
```bash
gcloud compute scp \
  --zone=us-east4-a --tunnel-through-iap --project=orphansinthedesert \
  openclaw/adversarial_drills.py \
  openclaw/integrity_engine.py \
  openclaw/promote.py \
  openclaw-secure-node:~/openclaw/

gcloud compute scp \
  --zone=us-east4-a --tunnel-through-iap --project=orphansinthedesert \
  openclaw/tests/test_adversarial.py \
  openclaw-secure-node:~/openclaw/tests/
```
The `integrity_engine.py` and `promote.py` are re-pushed because they got the path-bootstrap fix.

### Step 69: Run the full pytest suite on the VM
```bash
oc-ssh
cd ~ && python3 -m pytest openclaw/tests/ -v
```
Expected: **124 passing** (across all phases).

Spot-check the Phase 11 portion specifically:
```bash
python3 -m pytest openclaw/tests/test_adversarial.py -v --no-header
```
Expected: 20 passing (boundary statics, exhaustion, end-to-end, post-promotion tamper detection, source integrity, chain integration, salt/signing, forged chain detection, Pydantic invariants).

### Step 70: Run the live drills
```bash
export SECRET_SALT=$(grep '^SECRET_SALT=' /mnt/disks/research/.secrets/.env | cut -d= -f2)

python3 ~/openclaw/adversarial_drills.py all
```
Expected output ends with:
```
=== summary ===
passed: 4/4
failed: 0/4
```

If `chain-audit` fails: investigate immediately — that means the integrity chain is reporting tamper on the live system, not just in tests. Run `python3 ~/openclaw/integrity_engine.py audit` for the precise sequence_id and reason.

### Step 71: Drill — markdown integrity for a real published brief
Pick a published_id from the Phase 10 promotion(s):
```bash
python3 ~/openclaw/databricks_worker.py read-recent --table public_archive.published --limit 1 \
  | python3 -c "import sys, json; print(json.load(sys.stdin)[0]['published_id'])"
# → e.g. pub-3a7c1b9e4f02

python3 ~/openclaw/adversarial_drills.py markdown-integrity pub-3a7c1b9e4f02
# Expected:
#   ✓ content_hash matches recompute (a3f2c1...)
```

### Step 72: Drill — simulated tamper detection (destructive; do on snapshot or skip in prod)
This drill **modifies a real Delta row** to confirm the auditor catches it. **Take a snapshot before running** so you can roll back:
```bash
oc-snapshot
SNAPSHOT_NAME="openclaw-pre-phase11-tamper-$(date +%Y%m%d-%H%M)"
echo "Pre-tamper snapshot: $SNAPSHOT_NAME"

# Tamper: directly modify one chain entry's target_payload_hash via a delta-rs write.
python3 - <<'PY'
import os
from datetime import datetime, timezone
from openclaw.integrity_engine import (
    Salter, Signer, ChainStore, ChainEntry, ChainOperation,
)
from openclaw.databricks_worker import DeltaTableWriter

salter = Salter()
signer = Signer(salter)
store = ChainStore(DeltaTableWriter())
latest = store.read_latest()
print(f"appending forged entry at sequence_id={latest.sequence_id + 1}")

bad = ChainEntry(
    sequence_id=latest.sequence_id + 1,
    recorded_at=datetime.now(timezone.utc),
    author_identity="adversarial-drill",
    operation=ChainOperation.INSERT,
    target_table="research.bronze.raw_responses",
    target_row_id="forged-drill-001",
    target_payload_hash="f"*64,
    previous_hash="0"*64,  # WRONG — should be latest.row_signature
    row_signature=signer.sign("f"*64, "0"*64, salter.current_version),
    salt_version=salter.current_version,
)
store.append(bad)
PY

# Audit MUST detect:
python3 ~/openclaw/integrity_engine.py audit
# Expected: ✗ TAMPER DETECTED at sequence_id=<the forged seq>
#           Reason: previous_hash does not match prior row's row_signature

# Rollback to clean state by restoring the snapshot taken at the start of this step:
# (See "Rollback to Previous Snapshot" in MANUAL_COMMANDS.md.)
```
**This is the operational equivalent of the pytest test `test_forged_chain_entry_with_wrong_prev_hash_detected`.** It confirms the same invariant against the live datastore.

If you'd rather not modify the live chain, run only Steps 68-71. The pytest suite covers the same scenarios in isolation.

### Phase 11 Sign-off
- [ ] All 124 tests pass on the VM (`pytest openclaw/tests/`)
- [ ] All 20 Phase 11 adversarial tests pass specifically
- [ ] `adversarial_drills.py all` reports passed: 4/4
- [ ] `markdown-integrity <real_published_id>` confirms hash matches
- [ ] (optional) Live tamper drill detected at correct sequence_id, then chain restored from snapshot
- [ ] `python3 ~/openclaw/integrity_engine.py audit` reports `Chain INTACT` after any drill cleanup

When all six are checked, Phase 11 is complete. Next: Phase 12 (public commons via Delta Sharing + GitHub durable archive + daily Merkle seal publication).

### Phase 11 cost impact
**$0/mo.** Tests and drills run on existing VM compute.

---

## ☑️ PHASE 12: PUBLIC COMMONS — LOCAL EXPORT + SEAL LOG (15 minutes)

**Plan ref:** `openclaw/EXECUTION_public_commons.md`.

What this phase does: ships `openclaw/commons_publisher.py` — three subcommands that produce the durable public commons output to a local export directory. The directory is the **content** of the public commons; pushing it to a public GitHub repo (or any other distribution channel — API, MCP, website, the choice was deferred earlier) is a separate step and explicitly out of Phase 12 v1 scope.

| Subcommand | Output | Purpose |
|---|---|---|
| `rollup-ledger` | `<commons>/ledger/<YYYY-MM-DD>.md` + Delta `research.shared.ai_usage_ledger` | Daily AI-usage rollup with `give_back_ratio` |
| `export-briefs` | `<commons>/briefs/<YYYY-MM>/<published_id>.md` | Markdown copies of every PROMOTED brief |
| `publish-seal` | `<commons>/SEALS.md` | Append-only public log of daily Merkle seals |
| `publish-all` | All three, idempotent | Single nightly cron entry |

**Phase 12 explicitly defers** (each has clean follow-up paths once the user decides how to distribute):

- **Delta Sharing OSS server sidecar.** Provides programmatic read access to the five curated views. Worth adding when there's a downstream consumer; the markdown export is the human-readable surface for v1.
- **Public TLS endpoint, domain, CDN.** The user explicitly deferred the access-method decision (API / MCP / website / git / combination) until there's something to view. Phase 12 v1 produces the something.
- **Bidirectional GitHub sync.** Phase 12 v1 ships a `parse_retraction_commits` utility (so the parser is ready and tested), but doesn't actually invoke `git log`. When the export dir is published to a public repo, a follow-up step wires up the parser to a periodic `git log --grep='^retraction:'` invocation.

### Prerequisites
- [ ] Phase 11 complete (124+ tests passing, drills clean)
- [ ] At least one PROMOTED entry in `public_archive.published` (Phase 10's smoke test produced one)
- [ ] At least one CHECKPOINT entry in `audit.integrity_chain` (Phase 8's seal smoke test produced one — or run `integrity_engine.py seal` manually)
- [ ] `SECRET_SALT` exported (the seal publish doesn't need it directly, but the chain audit run as part of `publish-all` does for verification)

### Step 73: Pull Phase 12 files to the VM
```bash
gcloud compute scp \
  --zone=us-east4-a --tunnel-through-iap --project=orphansinthedesert \
  openclaw/commons_publisher.py \
  openclaw-secure-node:~/openclaw/

gcloud compute scp \
  --zone=us-east4-a --tunnel-through-iap --project=orphansinthedesert \
  openclaw/tests/test_commons_publisher.py \
  openclaw-secure-node:~/openclaw/tests/
```

### Step 74: Run unit tests on the VM
```bash
oc-ssh
cd ~ && python3 -m pytest openclaw/tests/test_commons_publisher.py -v
```
Expected: 23 passing (rollup math, brief export idempotency, seal log append, retraction parser, Pydantic guards).

### Step 75: Create the commons export directory
```bash
sudo mkdir -p /mnt/disks/research/public_commons_export
sudo chown -R "$(id -u):$(id -g)" /mnt/disks/research/public_commons_export
ls -la /mnt/disks/research/public_commons_export
```

### Step 76: Compute yesterday's AI-usage ledger entry
```bash
python3 ~/openclaw/commons_publisher.py rollup-ledger
# Expected:
#   ✓ ledger entry written
#     period:           2026-05-07 to 2026-05-07
#     briefs_published: <N>
#     estimated_cost:   $0.0xxx
#     give_back_ratio:  <ratio> briefs/USD
#     human-readable:   /mnt/disks/research/public_commons_export/ledger/2026-05-07.md

cat /mnt/disks/research/public_commons_export/ledger/$(date -u -d "yesterday" +%Y-%m-%d).md
# Or if `date -d` isn't available: cat the file from the path above.
```

If you want a specific date (e.g., the day Phase 9 ran the smoke test):
```bash
python3 ~/openclaw/commons_publisher.py rollup-ledger --date 2026-05-07
```

### Step 77: Export PROMOTED briefs as markdown
```bash
python3 ~/openclaw/commons_publisher.py export-briefs
# Expected:
#   ✓ exported N new brief(s)
#     briefs/2026-05/pub-<12hex>.md

# Inspect one
ls -R /mnt/disks/research/public_commons_export/briefs/
cat /mnt/disks/research/public_commons_export/briefs/2026-05/pub-<id>.md | head -30
```

Re-running is a no-op (idempotent — files already match their `content_hash`):
```bash
python3 ~/openclaw/commons_publisher.py export-briefs
# Expected: ✓ exported 0 new brief(s) / (N brief(s) already up-to-date — content_hash matched)
```

### Step 78: Publish the daily seal to SEALS.md
```bash
python3 ~/openclaw/commons_publisher.py publish-seal
# Expected: ✓ appended N new seal(s) to /mnt/disks/research/public_commons_export/SEALS.md

cat /mnt/disks/research/public_commons_export/SEALS.md
# Expected format:
#   2026-05-08  seq=42  merkle=ae48f...  seal=8b91d4...  salt_v=1
```

The `merkle` field is the Merkle root over that day's chain row signatures; an external observer can verify it without `SECRET_SALT` by pulling the chain and recomputing. The `seal` field is the salted signature, verifiable by anyone holding `SECRET_SALT`.

### Step 79: Combined nightly run
```bash
python3 ~/openclaw/commons_publisher.py publish-all
# Runs all three above, idempotent. This is the cron line.
```

### Step 80: Wire to nightly cron (4:56 PM EDT, just after the daily seal)
```bash
oc-ssh
crontab -e
# Add (one line):
56 20 * * 1-5 SECRET_SALT=$(grep '^SECRET_SALT=' /mnt/disks/research/.secrets/.env | cut -d= -f2) /usr/bin/python3 /home/$(whoami)/openclaw/commons_publisher.py publish-all >> /mnt/disks/research/logs/commons_publish.log 2>&1
```

This runs one minute after the Phase 8 daily seal cron entry (4:55 PM EDT). The order matters: integrity_engine writes the CHECKPOINT first, then commons_publisher captures it into SEALS.md.

### Step 81: Inspect the full commons output
```bash
tree /mnt/disks/research/public_commons_export
# Expected layout:
# .
# ├── briefs/
# │   └── 2026-05/
# │       ├── pub-001.md
# │       └── pub-002.md
# ├── ledger/
# │   └── 2026-05-07.md
# └── SEALS.md
```

This directory is the **content** of the public commons. Distribution is a separate concern (deferred per the EXECUTION_public_commons.md "Open Questions" resolution).

### Phase 12 Sign-off
- [ ] All 23 commons-publisher unit tests pass on the VM
- [ ] `rollup-ledger` writes a Delta row to `research.shared.ai_usage_ledger` AND a markdown file under `ledger/`
- [ ] `export-briefs` produces one `.md` file per PROMOTED entry; second invocation is a no-op
- [ ] `publish-seal` appends a line to `SEALS.md` for each new CHECKPOINT; second invocation is a no-op
- [ ] `publish-all` succeeds end-to-end
- [ ] Cron entry added for 4:56 PM EDT daily commons publish
- [ ] Integrity chain still reports `INTACT` after the Phase 12 writes

When all 7 are checked, Phase 12 v1 is complete. Phase 12.5 (Delta Sharing server sidecar) and Phase 12.6 (public distribution — GitHub or otherwise) are follow-ups.

### Phase 12 follow-ups (when ready)

**12.5 — Delta Sharing OSS server sidecar.** Add `delta-sharing-server` to `sandbox/gcp/sidecars.sh`, point it at `/mnt/disks/research/delta`, expose the five curated views over the open Delta Sharing protocol. Bind to 127.0.0.1 initially. Schema is already designed; only the sidecar config is missing.

**12.6 — Public distribution.** Wire the export directory to a public GitHub repo (single `git push` cron after `publish-all`). Optional: Cloudflare in front of a TLS endpoint to the Delta Sharing server. Choose API / MCP / website / combination based on which audience emerges. Both halves of the bidirectional retraction sync (push briefs, parse retraction commits) become operationally relevant at this point — `parse_retraction_commits` is ready and tested.

### Phase 12 cost impact
**$0/mo.** Local file writes to the existing persistent disk; export size after several months of operation should be well under 100 MB. Egress is $0 because nothing leaves the VM in v1 (distribution deferred).

---

### Daily Operations

**Morning (Automatic):**
- 9:00 AM EST: VM auto-starts
- Container auto-starts via Docker restart policy
- OpenClaw ready within 2-3 minutes

**Evening (Automatic):**
- 5:00 PM EST: Shutdown script saves state
- VM auto-stops
- Billing for compute stops (disk charges continue)

**Manual Tasks:**
- **Weekly:** Review logs for errors (`oc-ssh`, then `docker logs openclaw`)
- **Weekly:** Create snapshot (`oc-snapshot`)
- **Monthly:** Check billing (`oc-cost`)
- **Quarterly:** Rotate all API keys/tokens

### Monitoring

**Daily health check (30 seconds):**
```bash
oc-status               # Verify VM running
oc-ssh                  # Connect to VM
docker ps               # Verify container running
docker stats openclaw --no-stream  # Check resources
exit
```

Weekly review (5 minutes):
```bash
oc-ssh
docker logs openclaw --since 7d | grep -i error
df -h /mnt/disks/research  # Check disk usage
exit
```
Troubleshooting Quick Reference
Issue	Command	Expected Fix
VM won't start	gcloud compute instances describe openclaw-secure-node --zone=us-east4-a	Check for quota errors
Can't SSH	gcloud compute firewall-rules describe allow-ssh-iap	Verify IAP rule exists
Container not running	docker logs openclaw	Check for config errors
Out of disk space	du -sh /mnt/disks/research/*	Clean old logs/data
High costs	oc-cost + check billing console	Verify schedule is active
Support Resources
Full Command Reference: MANUAL_COMMANDS.md
Setup Guide: README.md
GCP IAP Docs: https://cloud.google.com/iap/docs
Docker Security: https://docs.docker.com/engine/security/

## 🎓 OPTIONAL ENHANCEMENTS
### A. Enable Cloud Armor (DDoS Protection)
```bash
# If you later add a load balancer
gcloud compute security-policies create openclaw-policy \
  --description="Cloud Armor policy for OpenClaw"
```

### B. Add Monitoring Dashboards
```bash
# Create custom dashboard in Cloud Console
# Metrics to track:
# - CPU utilization
# - Memory usage
# - Disk I/O
# - Network egress (cost tracking)
```

### C. Implement Cloud Functions for Overtime
Instead of manual metadata override, use Cloud Function:
```bash 
# Deploy function that sets overtime_active=true via Pub/Sub
# Trigger via Telegram bot command: "/overtime enable"
```
### D. Multi-Region Backup
```bash
# Replicate snapshots to different region
gcloud compute snapshots create openclaw-backup \
  --source-disk=openclaw-secure-node \
  --zone=us-east4-a \
  --storage-location=us-west1
```

## 📊 SUCCESS METRICS
After 30 days of operation, verify:

 Uptime: 99%+ availability during scheduled hours (9-5)
 Cost: Actual spend within 10% of $14.82 estimate
 Security: Zero unauthorized access attempts in logs
 Performance: Average query response time < 30 seconds
 Reliability: Zero data loss incidents
 Backup: At least 4 weekly snapshots exist

 ## 🔄 MONTHLY REVIEW CHECKLIST
Run this checklist on the 1st of each month:

 Review billing: oc-cost + visit billing console
 Check snapshot count: gcloud compute snapshots list
 Delete snapshots >90 days old
 Review error logs: docker logs openclaw --since 30d | grep ERROR
- [ ] Update Docker image: `docker pull ghcr.io/openclaw/openclaw:latest && cd ~/openclaw && ./setup.sh`
- [ ] Verify schedule still active: `gcloud compute resource-policies describe openclaw-day-shift --region=us-east4`
- [ ] Check disk usage trend: `df -h /mnt/disks/research` (project if >80% full)
- [ ] Test backup restore: Create test VM from latest snapshot
- [ ] Review IAM permissions: No unexpected service accounts added
- [ ] Security patch check: `docker exec openclaw apt-get update && apt-get upgrade -s`

---

## 🔐 QUARTERLY SECURITY REVIEW

Run this every 90 days (recommend calendar reminder):

### 1. Rotate All Secrets
```bash
# Generate new tokens from all providers
# - Telegram: Message @BotFather → /revoke → /token
# - Gemini: Delete and regenerate at https://aistudio.google.com/app/apikey
# - GitHub: Revoke old PAT, create new one
# - Exa: Generate new API key

# Update .env file
oc-ssh
nano /mnt/disks/research/.secrets/.env
# Replace all tokens
# NOTE: docker restart does NOT re-read --env-file. Must stop+rm+rerun.
docker stop openclaw && docker rm openclaw && ~/openclaw/setup.sh
```

### 2. Audit Access Logs
```bash
# Check who accessed via IAP
gcloud logging read 'resource.type="gce_instance" AND jsonPayload.event_type="iam_policy"' \
  --limit=100 \
  --format=json \
  --project=orphansinthedesert > access-audit.json

# Review for unexpected users
```

### 3. Validate Firewall Rules
```bash
# Ensure only IAP can access
gcloud compute firewall-rules list --format="table(name,sourceRanges,allowed,targetServiceAccounts)"

# Verify no changes to allow-ssh-iap rule
```

### 4. Container Security Scan
```bash
# Scan for vulnerabilities
docker exec openclaw apt-get update
docker exec openclaw apt-get upgrade -s | grep -i security

# Rebuild image with latest base
oc-ssh
cd ~/openclaw
docker pull ghcr.io/openclaw/openclaw:latest
docker build --no-cache -t openclaw-hardened -f Dockerfile.hardened .
docker stop openclaw && docker rm openclaw && ./setup.sh
```

### Billing Anomaly Check
```bash
# Check for unusual spending patterns
gcloud beta billing projects describe orphansinthedesert

# If cost >$20/month, investigate:
# - Is schedule still active?
# - Unexpected egress charges?
# - NAT misconfiguration?
```

## 🚨 INCIDENT RESPONSE PLAYBOOK
### Scenario 1: VM Compromised (Suspected)
Detection Signs:

Unusual processes in docker top openclaw
Unexpected network connections
CPU/memory spiking without user activity
Response:
```bash
# 1. Immediate isolation (stop VM)
gcloud compute instances stop openclaw-secure-node --zone=us-east4-a

# 2. Create forensic snapshot
gcloud compute disks snapshot openclaw-secure-node \
  --zone=us-east4-a \
  --snapshot-names=forensic-$(date +%Y%m%d-%H%M%S) \
  --description="Suspected compromise - preserve for analysis"

# 3. Disable service account
gcloud iam service-accounts disable openclaw-agent-v3@orphansinthedesert.iam.gserviceaccount.com

# 4. Revoke all API tokens
# - Telegram: @BotFather → /revoke
# - Gemini: Delete API key at https://aistudio.google.com/app/apikey
# - GitHub: Revoke PAT immediately
# - Exa: Delete API key from dashboard

# 5. Review access logs
gcloud logging read 'resource.type="gce_instance"' \
  --limit=500 \
  --format=json > incident-logs-$(date +%Y%m%d).json

# 6. Contact security team or consultant for forensic analysis
```

### Scenario 2: Unexpected High Bill (continued)

**Response:**
```bash
# 1. Check current spending
gcloud beta billing projects describe orphansinthedesert

# 2. Identify cost drivers
# Visit: https://console.cloud.google.com/billing/[BILLING_ACCOUNT]/reports

# 3. Immediate cost reduction
gcloud compute instances stop openclaw-secure-node --zone=us-east4-a

# 4. Check for resource leaks
gcloud compute instances list --project=orphansinthedesert
gcloud compute disks list --project=orphansinthedesert
gcloud compute addresses list --project=orphansinthedesert

# 5. Review NAT logs for excessive egress
gcloud logging read 'resource.type="nat_gateway"' --limit=100

# 6. If unauthorized resources found, delete immediately
gcloud compute instances delete [UNKNOWN_VM] --zone=[ZONE] --quiet
```

### Scenario 3: Data Loss / Corruption
Response:
```bash
# 1. Stop container immediately (prevent further writes)
oc-ssh
docker stop openclaw

# 2. Check disk integrity
sudo fsck -n /dev/disk/by-id/google-openclaw-secure-node
# (-n flag = dry run, no changes)

# 3. List available snapshots
gcloud compute snapshots list --filter="sourceDisk:openclaw-secure-node" \
  --sort-by=~creationTimestamp

# 4. Restore from most recent good snapshot
# (See "Rollback to Previous Snapshot" in MANUAL_COMMANDS.md)

# 5. Verify restored data
ls -lah /mnt/disks/research/workspace
docker start openclaw
docker logs openclaw
```

### Scenario 4: Cannot SSH / IAP Tunnel Fails
Response:
```bash
# 1. Verify VM is running
gcloud compute instances describe openclaw-secure-node \
  --zone=us-east4-a \
  --format="get(status)"

# 2. Check IAP firewall rule
gcloud compute firewall-rules describe allow-ssh-iap

# 3. Verify IAP API is enabled
gcloud services list --enabled | grep iap

# 4. Test from GCP Console (browser-based SSH)
# Visit: https://console.cloud.google.com/compute/instances
# Click "SSH" button next to openclaw-secure-node

# 5. Check for network policy changes
gcloud compute networks describe default

# 6. Last resort: Enable temporary public IP (DANGER - remove after debug)
gcloud compute instances add-access-config openclaw-secure-node \
  --zone=us-east4-a \
  --access-config-name="temporary-debug"

# SSH via public IP, fix issue, then remove:
gcloud compute instances delete-access-config openclaw-secure-node \
  --zone=us-east4-a \
  --access-config-name="temporary-debug"
```

## 📈 SCALING & OPTIMIZATION
### When to Scale Up
Indicators you need more resources:

Container frequently OOM killed (check docker inspect openclaw --format='{{.State.OOMKilled}}')
CPU consistently >150% (check docker stats)
Disk >80% full
Response times >60 seconds
Scaling Options:

**Option A: Larger Machine Type
```bash
# Stop VM
gcloud compute instances stop openclaw-secure-node --zone=us-east4-a

# Change to e2-standard-2 (2 vCPU, 8GB RAM = $16.18/month for 8h/day)
gcloud compute instances set-machine-type openclaw-secure-node \
  --machine-type=e2-standard-2 \
  --zone=us-east4-a

# Update container limits in setup.sh:
# --memory=6000m --cpus=1.8

# Restart
gcloud compute instances start openclaw-secure-node --zone=us-east4-a
```

**Option B: Larger Disk**
```bash
# Resize disk to 100GB (online, no downtime required)
gcloud compute disks resize openclaw-secure-node \
  --size=100GB \
  --zone=us-east4-a

# Inside VM, resize filesystem
oc-ssh
sudo resize2fs /dev/disk/by-id/google-openclaw-secure-node

# Verify new size
df -h /mnt/disks/research
```
Cost impact: $5.00/month → $10.00/month

**Option C: Extend Hours
```bash
# Change schedule to 7 AM - 7 PM (12 hours)
gcloud compute resource-policies update instance-schedule openclaw-day-shift \
  --region=us-east4 \
  --vm-start-schedule="0 7 * * *" \
  --vm-stop-schedule="0 19 * * *"
```

Cost impact: $8.09/month → $12.13/month

## 🎯 PERFORMANCE OPTIMIZATION
### If Container Feels Slow

1. Check Resource Utilization
```bash
oc-ssh
docker stats openclaw --no-stream

# If CPU is bottleneck (>150% consistently):
# - Upgrade to e2-standard-2
# - Or reduce concurrent tasks

# If Memory is bottleneck (approaching 1.8GB):
# - Increase --memory=3000m in setup.sh
# - Or upgrade machine type
```

2. Optimize Disk I/O
```bash
# Check disk I/O stats
oc-ssh
sudo iotop -o

# If high I/O wait:
# - Upgrade to pd-ssd (6x faster: $17/month vs $5/month)
# - Or reduce log verbosity in OpenClaw config
```

3. Network Latency
```bash
# Test API response times
oc-ssh
docker exec openclaw curl -w "@-" -o /dev/null -s https://api.telegram.org/botTOKEN/getMe <<< '
time_namelookup:  %{time_namelookup}s
time_connect:     %{time_connect}s
time_total:       %{time_total}s
'

# If >500ms consistently:
# - Check Cloud NAT logs for throttling
# - Consider moving to region closer to API endpoints
```

## 🔮 FUTURE ENHANCEMENTS ROADMAP
### Phase 2: Multi-Agent Setup (Advanced)
```bash
# Run multiple specialized agents (research, summarization, code review)
# Each in separate container with resource limits
docker run -d --name openclaw-research --memory=900m --cpus=0.9 ...
docker run -d --name openclaw-summarize --memory=900m --cpus=0.9 ...
```

### Phase 3: Persistent Knowledge Base
```bash
# Add vector database service (e.g., Qdrant, Weaviate)
docker run -d --name qdrant \
  -p 127.0.0.1:6333:6333 \
  -v /mnt/disks/research/vector_db:/qdrant/storage \
  qdrant/qdrant:latest
```

### Phase 4: Automated Research Pipeline
```bash
# Cloud Scheduler triggers research tasks daily
gcloud scheduler jobs create http daily-research \
  --schedule="0 10 * * *" \
  --uri="http://localhost:18789/api/research" \
  --http-method=POST
```

### Phase 5: Slack/Discord Integration
```bash
# Add to .env:
# SLACK_BOT_TOKEN=xoxb-...
# SLACK_CHANNEL_ID=C123456

# Container automatically posts research summaries to Slack
```

## 📚 ADDITIONAL LEARNING RESOURCES
### GCP Security Best Practices
- **IAM Roles:** https://cloud.google.com/iam/docs/understanding-roles
- **VPC Service Controls:** https://cloud.google.com/vpc-service-controls/docs
- **Shielded VMs:** https://cloud.google.com/compute/shielded-vm/docs
- **Binary Authorization:** https://cloud.google.com/binary-authorization/docs

### Docker Security
- **CIS Benchmarks:** https://www.cisecurity.org/benchmark/docker
- **Capability Reference:** https://man7.org/linux/man-pages/man7/capabilities.7.html
- **Seccomp Profiles:** https://docs.docker.com/engine/security/seccomp/
- **AppArmor:** https://docs.docker.com/engine/security/apparmor/

### AI Agent Security
- **OWASP Top 10 for LLMs:** https://owasp.org/www-project-top-10-for-large-language-model-applications/
- **Prompt Injection Defense:** https://learnprompting.org/docs/prompt_hacking/injection
- **Agent Safety:** https://www.anthropic.com/index/claude-2-1-prompting

### Cost Optimization
- **GCP Pricing Calculator:** https://cloud.google.com/products/calculator
- **Committed Use Discounts:** https://cloud.google.com/compute/docs/instances/committed-use-discounts-overview
- **Sustained Use Discounts:** https://cloud.google.com/compute/docs/sustained-use-discounts

---

## 🏆 PRODUCTION READINESS SCORECARD

Rate your deployment (honest self-assessment):

### Security (Weight: 40%)
- [ ] No public IP on VM (10 pts)
- [ ] Secrets in separate read-only mount (10 pts)
- [ ] Container capabilities dropped (10 pts)
- [ ] Service account least-privilege (10 pts)
- **Score: ___/40**

### Reliability (Weight: 30%)
- [ ] Automated backups (snapshots) weekly (10 pts)
- [ ] Tested restore procedure (10 pts)
- [ ] Container auto-restart configured (5 pts)
- [ ] Health checks enabled (5 pts)
- **Score: ___/30**

### Observability (Weight: 15%)
- [ ] Cloud Logging enabled (5 pts)
- [ ] Budget alerts configured (5 pts)
- [ ] Performance monitoring (docker stats) (5 pts)
- **Score: ___/15**

### Cost Control (Weight: 15%)
- [ ] Actual spend within 10% of budget (5 pts)
- [ ] Schedule active and working (5 pts)
- [ ] No zombie resources (unused disks/IPs) (5 pts)
- **Score: ___/15**

### **TOTAL SCORE: ___/100**

**Grading:**
- **90-100:** Production-ready (excellent)
- **75-89:** Production-ready (good, minor improvements needed)
- **60-74:** Pre-production (significant gaps to address)
- **<60:** Not ready (review security and reliability sections)

---

## ✅ FINAL SIGN-OFF

By checking these boxes, I confirm:

- [ ] I understand the security model (no public IP, least-privilege SA)
- [ ] I have tested SSH access via IAP
- [ ] I have tested container restart after reboot
- [ ] I have created at least one backup snapshot
- [ ] I have tested the restore procedure
- [ ] I have configured budget alerts
- [ ] I have documented all secrets in secure location (password manager)
- [ ] I have set calendar reminders for quarterly reviews
- [ ] I have read and understand the incident response playbook
- [ ] I accept responsibility for monitoring costs and security

**Deployment Date:** _______________  
**Deployed By:** _______________  
**Reviewed By (if team deployment):** _______________  

---

## 📞 EMERGENCY CONTACT CARD

**Print this and keep with your desk/laptop:**
┌────────────────────────────────────────────────┐
│        OPENCLAW EMERGENCY QUICK REFERENCE       │
├────────────────────────────────────────────────┤
│ Project: orphansinthedesert                    │
│ VM: openclaw-secure-node                       │
│ Zone: us-east4-a                               │
├────────────────────────────────────────────────┤
│ EMERGENCY STOP (cost bleeding):                │
│ gcloud compute instances stop \                │
│   openclaw-secure-node --zone=us-east4-a       │
├────────────────────────────────────────────────┤
│ EMERGENCY SNAPSHOT (before changes):           │
│ gcloud compute disks snapshot \                │
│   openclaw-secure-node --zone=us-east4-a \     │
│   --snapshot-names=emergency-YYYYMMDD          │
├────────────────────────────────────────────────┤
│ QUICK SSH:                                     │
│ oc-ssh                                         │
├────────────────────────────────────────────────┤
│ CHECK STATUS:                                  │
│ oc-status                                      │
├────────────────────────────────────────────────┤
│ VIEW COSTS:                                    │
│ https://console.cloud.google.com/billing       │
├────────────────────────────────────────────────┤
│ SECRETS BACKUP LOCATION:                       │
│ [Your password manager / encrypted USB]        │
└────────────────────────────────────────────────┘


---

## 🎉 CONGRATULATIONS!

You have successfully deployed a **production-grade, security-hardened AI research agent** with:

✅ **Cost:** $14.82/month (67% cheaper than 24/7 operation)  
✅ **Security:** Zero public exposure, least-privilege access, container hardening  
✅ **Reliability:** Automated backups, tested recovery, auto-restart  
✅ **Compliance:** Audit logging, budget controls, secrets management  

### What You've Achieved

1. **Infrastructure as Code:** All configurations documented and reproducible
2. **Defense in Depth:** 7 layers of security (network, IAM, container, filesystem, capabilities, secrets, monitoring)
3. **Operational Excellence:** Automated scheduling, monitoring, backups
4. **Cost Optimization:** Schedule-based runtime, optimized NAT, right-sized resources
5. **Disaster Recovery:** Snapshot strategy, tested restore procedures

### Your Competitive Advantage

Compared to typical AI agent deployments:
- **10x more secure** (most skip container hardening entirely)
- **3x cheaper** (most run 24/7 with public IPs)
- **5x more reliable** (most lack backup/restore testing)

### Next 30 Days

**Week 1:** Monitor daily, verify schedule works, test all aliases  
**Week 2:** Reduce monitoring to every other day, create first monthly snapshot  
**Week 3:** Test Telegram integration with complex queries  
**Week 4:** Month-end review: Check costs, review logs, optimize as needed  

---

---

## ✅ PHASE 1 COMPLETION CHECKPOINT
**Date completed:** 2026-05-04  
**Status:** OPERATIONAL

### What was deployed and verified
- **GCP VM:** `openclaw-secure-node` (e2-standard-2, us-east4-a, no public IP)
- **Networking:** Cloud NAT outbound, IAP-only inbound, SSH + web tunnel firewall rules
- **Docker:** Hardened container (`openclaw-hardened`) — non-root user (UID 1000), read-only filesystem, capability drop, memory/CPU limits
- **Persistent disk:** `/mnt/disks/research` mounted with workspace, logs, vector_db, .secrets, .openclaw directories
- **OpenClaw gateway:** Running on `google/gemini-2.5-flash` via Gemini API
- **Telegram:** `@ToSeeData_bot` connected and responding
- **Web UI:** Accessible via `oc-tunnel` (SSH port forward) → `http://localhost:18789`

### Key decisions made during Phase 1
- **AI provider:** Gemini (not OpenAI) — `GEMINI_API_KEY` required in `.env`
- **Search:** Exa (not Tavily) — `EXA_API_KEY` required in `.env`
- **Tunnel method:** SSH port forwarding (`oc-tunnel`) not IAP TCP — gateway binds to `127.0.0.1` on VM
- **Model:** `google/gemini-2.5-flash` — default codex `models.json` removed to allow Gemini resolution
- **Container restart:** `docker stop && docker rm && docker run` required after `.env` changes (not `docker restart`)
- **Auth flow:** `openclaw models auth login --provider google` auto-detects `GEMINI_API_KEY` from env

### Known issues resolved
| Issue | Root cause | Fix |
|---|---|---|
| `groupadd: GID 1000 already exists` | Base image had UID/GID 1000 | Delete existing user/group in Dockerfile before creating clawuser |
| `Gateway failed: mkdir '/.openclaw'` | `--user $(id -u)` used host UID with no passwd entry → HOME=/ | Hard-coded `--user 1000:1000 -e HOME=/home/clawuser` |
| `error: unknown command 'start'` | CMD used non-existent `openclaw start` | Changed to `openclaw gateway --port 18789 --allow-unconfigured` |
| `Missing config` | Gateway needed `--allow-unconfigured` flag | Added to CMD |
| `No API key for openai` | Agent's `models.json` locked to codex/gpt-5.5 | Deleted `models.json`; set Gemini as global default |
| IAP tunnel 4003 on port 18789 | Docker bound to `127.0.0.1`, IAP connects to internal VM IP | Switched to SSH port forwarding |
| `docker restart` not picking up `.env` | Env vars baked in at `docker run` time | Must `stop + rm + rerun` for env changes |

### Firewall rules in place
| Rule | Port | Source | Purpose |
|---|---|---|---|
| `allow-ssh-iap` | 22 | 35.235.240.0/20 | SSH via IAP |
| `allow-openclaw-web-iap` | 18789 | 35.235.240.0/20 | Web UI (reserve, SSH forward used instead) |

---

## 🚀 YOU ARE NOW OPERATIONAL!

**Version:** 4.0  
**Last Updated:** 2026-05-04  
**Total Setup Time:** ~60 minutes  
**Estimated Monthly Cost:** $14.82  
**Security Posture:** Hardened  
**Production Readiness:** ✅ READY  

---

**END OF DEPLOYMENT_CHECKLIST.md**




