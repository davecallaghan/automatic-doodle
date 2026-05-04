# automatic-doodle

This was just the automatic name GitHub gave me, but it actually makes a lot of sense for this project. At least its a better name that what I would come up with

## OPENCLAW HARDENED AGENCY v4.0 - COMPLETE PRODUCTION PACKAGE
Budget Optimization: 9AM-5PM (8 hours/day) = $14.82/month

openclaw-agency-v4/
├── README.md                          # This file - Complete documentation
├── MANUAL_COMMANDS.md                 # All manual commands for reference
├── init_agency.sh                     # Master initialization script
├── local_hardening.sh                 # Run on your Mac (one-time setup)
├── infra/
│   ├── provision.sh                   # GCP VM creation
│   ├── networking.sh                  # Firewall + NAT setup
│   └── schedule.sh                    # 9AM-5PM scheduling
├── instance/
│   ├── Dockerfile.hardened            # Secure container image
│   └── setup.sh                       # Run inside VM after SSH
└── workspace/
    ├── topics.json                    # Research interests config
    └── virtue_prompt.md               # Epistemic guidelines

# OpenClaw Hardened Research Agency v4.0

## 🎯 Project Specifications

- **Budget**: $14.82/month (8-hour daily schedule: 9AM-5PM EST)
- **Instance**: e2-medium (2 vCPU, 4GB RAM) on us-east4-a
- **Storage**: 50GB pd-balanced persistent disk (survives VM deletion)
- **Security**: Zero public IP, IAP-only access, service account isolation
- **Runtime**: Non-root Docker container with read-only filesystem

---

## 💰 Cost Breakdown

| Resource | Hours/Day | Cost/Month | Notes |
|----------|-----------|------------|-------|
| e2-medium VM | 8h | $8.09 | Auto-stops at 5PM EST |
| 50GB pd-balanced | 24h | $5.00 | Persists when VM stopped |
| Cloud NAT | 8h | $1.20 | Outbound internet for updates |
| Egress (est.) | - | $0.53 | API calls + Docker pulls |
| **TOTAL** | - | **$14.82** | Under $17.50 budget ✅ |

---

## 🚀 Installation Steps

### Phase 1: Local Mac Setup (5 minutes)

1. **Clone this repository**:
```bash
mkdir -p ~/Projects/openclaw-agency-v4
cd ~/Projects/openclaw-agency-v4

# Copy all files from this package into this directory

# Make scripts executable:
chmod +x init_agency.sh local_hardening.sh infra/*.sh instance/setup.sh

#Run local hardening (sets up gcloud CLI security):
./local_hardening.sh

# This will prompt for your email for budget alerts 
./init_agency.sh

# Creates infra/ and instance/ scripts with embedded configs
```

### Phase 2: GCP Infrastructure Provisioning (10 minutes)
Create the VM and networking:
```bash
cd infra
./provision.sh          # Creates VM + service account
./networking.sh         # Sets up IAP tunnel + Cloud NAT
./schedule.sh           # Configures 9AM-5PM auto-start/stop
```
Verify the VM is running:
```bash
gcloud compute instances list --project=orphansinthedesert
# Should show "openclaw-secure-node" with INTERNAL_IP only
```
### Phase 3: Container Setup Inside VM (15 minutes)
SSH into the VM via IAP tunnel
```bash
gcloud compute ssh openclaw-secure-node \
  --project=orphansinthedesert \
  --zone=us-east4-a \
  --tunnel-through-i
```

7. **SSH into the VM via IAP tunnel** (continued):
```bash
gcloud compute ssh openclaw-secure-node \
  --project=orphansinthedesert \
  --zone=us-east4-a \
   --tunnel-through-iap
```

1. Wait for startup script to complete (check disk mount):
```bash
# Inside the VM, verify persistent disk is mounted
df -h | grep research
# Should show: /dev/sdb mounted at /mnt/disks/research (50GB)

ls -la /mnt/disks/research
# Should show: workspace/ logs/ vector_db/ .secrets/ directories
```
2. Run the instance setup script:
```bash
# Still inside the VM via SSH
cd ~
# Copy the setup.sh script from your local machine to the VM
# (Use gcloud compute scp or paste the contents)

chmod +x setup.sh
./setup.sh
# This installs Docker, builds the hardened image, and starts the container
```

3. Configure your secrets (IMPORTANT):
```bash
# Edit the .env file with your actual API keys
nano /mnt/disks/research/.secrets/.env

# Replace these placeholder values:
# TELEGRAM_BOT_TOKEN=your_actual_bot_token_from_botfather
# TELEGRAM_CHAT_ID=your_actual_chat_id_from_earlier_setup
# EXA_API_KEY=your_exa_api_key
# GITHUB_TOKEN=your_github_personal_access_token

# Save and exit (Ctrl+X, Y, Enter)

# Restart the container to load new secrets
docker restart openclaw
```

4. Verify the container is running:
``` bash
docker ps
# Should show "openclaw" container with status "Up"

docker logs openclaw
# Should show startup logs with no errors
```

### Phase 4: Accessing OpenClaw from Your Mac
1. Create a secure tunnel to the web interface:
```bash
# On your Mac (new terminal window)
gcloud compute start-iap-tunnel openclaw-secure-node 18789 \
  --local-host-port=localhost:18789 \
  --zone=us-east4-a \
  --project=orphansinthedesert

# Keep this terminal open - it maintains the tunnel
```
2. Access the OpenClaw interface:
Open browser: http://localhost:18789


🔐 Security Features
Network Isolation
- ✅ No public IP address - VM only accessible via IAP tunnel
- ✅ Firewall rule targets service account (not tags)
- ✅ Cloud NAT provides outbound-only internet access
- ✅ Port binding on 127.0.0.1 (not 0.0.0.0)
Container Hardening
- ✅ Read-only filesystem - Malware cannot persist
- ✅ Dropped capabilities - Only CHOWN + DAC_OVERRIDE
- ✅ Resource limits - 1.8GB RAM, 1.8 CPU, 100 processes max
- ✅ No new privileges - Blocks setuid escalation
- ✅ Secrets mounted read-only - Container cannot modify .env
### Data Persistence
- ✅ 50GB persistent disk - auto-delete=no (survives VM deletion)
- ✅ UUID-based /etc/fstab - Survives disk reattachment
- ✅ Conditional formatting - Only formats blank disks
- ✅ Separate mount points - workspace/logs/vector_db isolated
### Identity & Access
- ✅ Least-privilege service account - logging + monitoring only
- ✅ OS Login enabled - SSH keys managed by Google IAM
- ✅ **Project SSH keys blocked** - Only user-specific keys allowed
- ✅ **Shielded VM** - Secure boot + vTPM + integrity monitoring
- ✅ **Serial port disabled** - No console access bypass

### Cost Controls
- ✅ **Instance scheduling** - Auto-stop at 5PM, auto-start at 9AM
- ✅ **Budget alerts** - Email at 50%, 90%, 100% of $20/month threshold
- ✅ **NAT port optimization** - 64 ports/VM (vs default 2048)
- ✅ **Deletion protection** - Prevents accidental `gcloud compute instances delete`

---

## 🛠️ Daily Operations

### Starting Work (Automatic)
The VM automatically starts at **9:00 AM EST** every day. No action needed.

### Accessing the Interface
```bash
# On your Mac, create tunnel (run once per session)
openclaw-tunnel  # Uses alias from local_hardening.sh

# Open browser
open http://localhost:18789

### Checking Container Status
```bash
# SSH into VM
openclaw-ssh  # Uses alias from local_hardening.sh

# Inside VM
docker ps                    # Verify container running
docker logs openclaw -f      # Stream live logs
docker stats openclaw        # Monitor CPU/RAM usage
```
### Manual Stop (Emergency Cost Control)
```bash
# From your Mac - stops VM immediately
gcloud compute instances stop openclaw-secure-node \
  --zone=us-east4-a \
  --project=orphansinthedesert
```

### Manual Start (Override Schedule)
```bash
# From your Mac - starts VM outside scheduled hours
gcloud compute instances start openclaw-secure-node \
  --zone=us-east4-a \
  --project=orphansinthedesert
```
## 📊 Monitoring & Troubleshooting
### View VM Logs
 ```bash
 # From your Mac
gcloud compute instances get-serial-port-output openclaw-secure-node \
  --zone=us-east4-a \
  --project=orphansinthedesert
```

### Check Disk Usage
```bash
# Inside VM via SSH
df -h /mnt/disks/research
du -sh /mnt/disks/research/*
```

### Container Not Starting
```bash
# Inside VM
docker logs openclaw          # Check for errors
docker inspect openclaw       # View full config

# Common fixes:
docker restart openclaw       # Restart container
docker rm openclaw && ./setup.sh  # Rebuild from scratch
```

### Budget Exceeded
```bash
# Check current month's costs
gcloud billing accounts list
gcloud beta billing budgets list --billing-account=XXXXXX-XXXXXX-XXXXXX

# Emergency shutdown
gcloud compute instances stop openclaw-secure-node --zone=us-east4-a
```

## 🔄 Maintenance Tasks
### Weekly: Update Container Image
```bash
# Inside VM via SSH
docker pull ghcr.io/openclaw/openclaw:latest
docker stop openclaw
docker rm openclaw
./setup.sh  # Rebuilds with latest image
```

### Monthly: Review Costs
```bash
# From your Mac
gcloud beta billing projects describe orphansinthedesert \
  --format="table(billingAccountName)"

# Visit: https://console.cloud.google.com/billing
# Check actual vs. budgeted spend
```

### Quarterly: Rotate Secrets
```bash
# 1. Generate new tokens/keys from providers (Telegram, GitHub, etc.)
# 2. SSH into VM and update .env
nano /mnt/disks/research/.secrets/.env
# 3. Restart container
docker restart openclaw
```
## 🚨 Emergency Procedures
### Data Backup (Before Risky Changes)
```bash
# Inside VM
tar -czf /tmp/research-backup-$(date +%Y%m%d).tar.gz /mnt/disks/research

### Data Backup (Before Risky Changes) (continued)
```bash
# Inside VM
tar -czf /tmp/research-backup-$(date +%Y%m%d).tar.gz /mnt/disks/research

# Copy to your Mac
# From your Mac in a new terminal:
gcloud compute scp openclaw-secure-node:/tmp/research-backup-*.tar.gz . \
  --zone=us-east4-a \
  --tunnel-through-iap
```

### Disk Snapshot (GCP-Native Backup)
```bash
# From your Mac - creates point-in-time snapshot
gcloud compute disks snapshot openclaw-secure-node \
  --zone=us-east4-a \
  --snapshot-names=openclaw-backup-$(date +%Y%m%d) \
  --description="Pre-maintenance backup"

# Restore from snapshot (if needed)
gcloud compute disks create openclaw-secure-node-restored \
  --source-snapshot=openclaw-backup-YYYYMMDD \
  --zone=us-east4-a
```

### Complete Teardown (Delete Everything)
```bash
# WARNING: This deletes ALL data permanently

# 1. Remove deletion protection
gcloud compute instances update openclaw-secure-node \
  --no-deletion-protection \
  --zone=us-east4-a

# 2. Delete VM (keeps disk because auto-delete=no)
gcloud compute instances delete openclaw-secure-node \
  --zone=us-east4-a \
  --quiet

# 3. Delete persistent disk (PERMANENT DATA LOSS)
gcloud compute disks delete openclaw-secure-node \
  --zone=us-east4-a \
  --quiet

# 4. Delete networking resources
gcloud compute routers nats delete openclaw-nat \
  --router=openclaw-router \
  --region=us-east4 \
  --quiet
gcloud compute routers delete openclaw-router \
  --region=us-east4 \
  --quiet

# 5. Delete firewall rule
gcloud compute firewall-rules delete allow-ssh-iap --quiet

# 6. Delete service account
gcloud iam service-accounts delete openclaw-agent-v3@orphansinthedesert.iam.gserviceaccount.com --quiet
```

# 📚 Additional Resources
OpenClaw Documentation: https://docs.openclaw.ai (hypothetical)
GCP IAP Troubleshooting: https://cloud.google.com/iap/docs/troubleshooting
Telegram Bot API: https://core.telegram.org/bots/api
Exa Search API: https://docs.exa.ai
Docker Security Best Practices: https://docs.docker.com/engine/security/

# 📞 Support
For issues specific to this setup:

Check MANUAL_COMMANDS.md for command reference
Review container logs: docker logs openclaw
Check GCP logs: Cloud Console → Logging → Logs Explorer

# 📋 Changelog
### v4.0 (2026-05-02)
Changed schedule from 11h/day (9AM-8PM) to 8h/day (9AM-5PM)
Reduced monthly cost from $21.12 to $14.82
Fixed startup script data loss bug (conditional mkfs.ext4)
Replaced tag-based firewall with service account targeting
Removed Homebrew from Dockerfile (700MB size reduction)
Added read-only filesystem + capability dropping
Implemented proper secrets management with read-only mount
### v3.0 (2026-04-30)
Removed Docker socket mount vulnerability
Fixed auto-delete disk setting
Added persistent disk mount logic
### v1.0 (2026-04-28)
Initial insecure baseline (educational purposes only)