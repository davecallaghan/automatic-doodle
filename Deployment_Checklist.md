# OpenClaw Hardened Agency v4.0 - Deployment Checklist

Use this checklist to ensure a complete, secure deployment.

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




