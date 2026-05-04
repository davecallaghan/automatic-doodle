# OpenClaw Hardened Agency - Manual Commands Reference

This document contains all commands you might need to run manually, organized by category.

---

## 🔐 AUTHENTICATION & PROJECT SETUP

### Set Active GCP Project
```bash
# Configure gcloud to use the correct project
gcloud config set project orphansinthedesert

# Verify current project
gcloud config get-value project

# Set default zone
gcloud config set compute/zone us-east4-a
```

### Authenticate with GCP
```bash
# Login with your Google account
gcloud auth login

# Set up Application Default Credentials (for Terraform/SDK)
gcloud auth application-default login --no-launch-browser

# List authenticated accounts
gcloud auth list
```

### Enable Required APIs
```bash
# Enable all necessary GCP APIs (run once during initial setup)
gcloud services enable compute.googleapis.com
gcloud services enable iap.googleapis.com
gcloud services enable logging.googleapis.com
gcloud services enable monitoring.googleapis.com
gcloud services enable cloudresourcemanager.googleapis.com
```

## 🖥️ VM MANAGEMENT
### Start VM (Manual Override)
```bash
# Start the VM outside of scheduled hours
gcloud compute instances start openclaw-secure-node \
  --zone=us-east4-a \
  --project=orphansinthedesert

# Start and wait for completion
gcloud compute instances start openclaw-secure-node \
  --zone=us-east4-a \
  --async=false
```

### Stop VM (Cost Savings)
```bash
# Stop the VM immediately (billing stops for compute, disk persists)
gcloud compute instances stop openclaw-secure-node \
  --zone=us-east4-a \
  --project=orphansinthedesert

# Force stop (if graceful shutdown hangs)
gcloud compute instances stop openclaw-secure-node \
  --zone=us-east4-a \
  --discard-local-ssd=true
```

### Chack VM Status
```bash
# List all VMs in project
gcloud compute instances list --project=orphansinthedesert

# Get detailed info about specific VM
gcloud compute instances describe openclaw-secure-node \
  --zone=us-east4-a \
  --format=yaml

# Check if VM is running
gcloud compute instances describe openclaw-secure-node \
  --zone=us-east4-a \
  --format="get(status)"
# Output: RUNNING, TERMINATED, or STOPPING
```

### View VM Serial Console Output
```bash
# View boot logs and startup script output
gcloud compute instances get-serial-port-output openclaw-secure-node \
  --zone=us-east4-a \
  --project=orphansinthedesert

# Stream live serial output (Ctrl+C to exit)
gcloud compute instances tail-serial-port-output openclaw-secure-node \
  --zone=us-east4-a
```

### Modify VM Metadata
```bash
# Add/update metadata key
gcloud compute instances add-metadata openclaw-secure-node \
  --zone=us-east4-a \
  --metadata=overtime_active=true

# Remove metadata key
gcloud compute instances remove-metadata openclaw-secure-node \
  --zone=us-east4-a \
  --keys=overtime_active

# View all metadata
gcloud compute instances describe openclaw-secure-node \
  --zone=us-east4-a \
  --format="get(metadata.items)"
```
## 🔌 SSH & TUNNEL MANAGEMENT
### SSH into VM via IAP
```bash
# Standard SSH connection (no public IP needed)
gcloud compute ssh openclaw-secure-node \
  --project=orphansinthedesert \
  --zone=us-east4-a \
  --tunnel-through-iap

# SSH with specific user
gcloud compute ssh your-username@openclaw-secure-node \
  --zone=us-east4-a \
  --tunnel-through-iap

# SSH and run a single command
gcloud compute ssh openclaw-secure-node \
  --zone=us-east
  --tunnel-through-iap \
  --command="docker ps"

# SSH with verbose logging (troubleshooting)
gcloud compute ssh openclaw-secure-node \
  --zone=us-east4-a \
  --tunnel-through-iap \
  -- -vvv
```

### Create SSH Port Forward for Web Access
```bash
# Forward port 18789 to localhost via IAP SSH (OpenClaw web interface)
# Note: Docker binds to 127.0.0.1 — IAP TCP tunnel cannot reach it; SSH port forward works
gcloud compute ssh openclaw-secure-node \
  --tunnel-through-iap \
  --zone=us-east4-a \
  --project=orphansinthedesert \
  -- -L 18789:localhost:18789 -N

# Keep tunnel running in background (macOS/Linux)
gcloud compute ssh openclaw-secure-node \
  --tunnel-through-iap \
  --zone=us-east4-a \
  --project=orphansinthedesert \
  -- -L 18789:localhost:18789 -N &

# Find and kill background tunnel
ps aux | grep "18789:localhost"
kill <PID>
```

### Copy Files to/from VM
```bash
Copy Files to/from VM
```

## 🐳 DOCKER COMMANDS (Run Inside VM)
### Container Lifecycle
```bash
# Start stopped container
docker start openclaw

# Stop running container (30 second graceful shutdown)
docker stop -t 30 openclaw

# Restart container
docker restart openclaw

# Remove container (preserves mounted data)
docker rm openclaw

# Force remove running container
docker rm -f openclaw
```

### View Container Logs
```bash
# View all logs
docker logs openclaw

# Stream live logs (like tail -f)
docker logs openclaw -f

# View last 100 lines
docker logs openclaw --tail 100

# View logs with timestamps
docker logs openclaw -t

# View logs since specific time
docker logs openclaw --since 2026-05-02T09:00:00
```

### Inspect Container
```bash
# View full container configuration
docker inspect openclaw

# Get specific value (e.g., IP address)
docker inspect openclaw --format='{{.NetworkSettings.IPAddress}}'

# Check resource limits
docker inspect openclaw --format='{{.HostConfig.Memory}}'

# View mounted volumes
docker inspect openclaw --format='{{json .Mounts}}' | jq
```

### Execute Commands Inside Container
```bash
# Open interactive shell inside container
docker exec -it openclaw /bin/bash

# Run single command
docker exec openclaw ls -la /home/clawuser/workspace

# Run command as root (troubleshooting only)
docker exec -u root openclaw apt-get update

# Check OpenClaw version
docker exec openclaw openclaw --version
```

### Monitor Container Resources
```bash
# Real-time resource usage (CPU, RAM, Network, Disk I/O)
docker stats openclaw

# Single snapshot (non-interactive)
docker stats openclaw --no-stream

# Monitor all containers
docker stats

# View container processes
docker top openclaw

# View container disk usage
docker exec openclaw du -sh /home/clawuser/*
```

### Rebuild Container from Scratch
```bash
# Inside VM: Stop and remove existing container
docker stop openclaw
docker rm openclaw

# Remove old image (forces fresh pull)
docker rmi openclaw-hardened

# Rebuild using Dockerfile
cd ~/openclaw
docker build -t openclaw-hardened -f Dockerfile.hardened .

# Re-run setup script to start new container
./setup.sh
```

## 💾 DISK & DATA MANAGEMENT
### Check Disk Status
```bash
# Inside VM: View all mounted disks
df -h

# Check persistent disk specifically
df -h /mnt/disks/research

# View disk usage by directory
du -sh /mnt/disks/research/*

# Find largest files
du -ah /mnt/disks/research | sort -rh | head -20

# Check inode usage (file count limits)
df -i /mnt/disks/research
```

### Verify Disk Mount
```bash
# Inside VM: Check if disk is mounted
mountpoint -q /mnt/disks/research && echo "Mounted" || echo "NOT MOUNTED"

# View mount options
mount | grep research

# Check /etc/fstab entry
cat /etc/fstab | grep research
```

### Manual Disk Operations
```bash
# Inside VM: Unmount disk (must stop container first)
docker stop openclaw
sudo umount /mnt/disks/research

# Manually mount disk
sudo mount -o discard,defaults,noatime /dev/disk/by-id/google-openclaw-secure-node /mnt/disks/research

# Check disk filesystem health
sudo fsck -n /dev/disk/by-id/google-openclaw-secure-node
# (-n flag = dry run, no changes made)

# Force filesystem check (ONLY if unmounted)
sudo fsck -f /dev/disk/by-id/google-openclaw-secure-node
```

### Create Disk Snapshots
```bash
# From your Mac: Create snapshot (VM can stay running)
gcloud compute disks snapshot openclaw-secure-node \
  --zone=us-east4-a \
  --snapshot-names=openclaw-snapshot-$(date +%Y%m%d-%H%M) \
  --description="Manual backup before maintenance"

# List all snapshots
gcloud compute snapshots list \
  --filter="sourceDisk:openclaw-secure-node"

# Delete old snapshot
gcloud compute snapshots delete openclaw-snapshot-YYYYMMDD-HHMM

# Restore from snapshot (creates NEW disk)
gcloud compute disks create openclaw-restored-disk \
  --source-snapshot=openclaw-snapshot-YYYYMMDD-HHMM \
  --zone=us-east4-a \
  --type=pd-balanced
```

### Attach Additional Disk to VM
```bash
# From your Mac: Create new blank disk
gcloud compute disks create openclaw-data-disk \
  --size=100GB \
  --type=pd-balanced \
  --zone=us-east4-a

# Attach to running VM
gcloud compute instances attach-disk openclaw-secure-node \
  --disk=openclaw-data-disk \
  --zone=us-east4-a

# Inside VM: Format and mount new disk
sudo mkfs.ext4 -m 0 /dev/disk/by-id/google-openclaw-data-disk
sudo mkdir -p /mnt/disks/data
sudo mount /dev/disk/by-id/google-openclaw-data-disk /mnt/disks/data

# Add to /etc/fstab for automatic mount
echo "UUID=$(sudo blkid -s UUID -o value /dev/disk/by-id/google-openclaw-data-disk) /mnt/disks/data ext4 discard,defaults 0 2" | sudo tee -a /etc/fstab
```

## 🔒 SECURITY & SECRETS MANAGEMENT
### View/Edit Secrets
```bash
# Inside VM: Edit environment variables (CAREFUL!)
nano /mnt/disks/research/.secrets/.env

# View current secrets (masked)
docker exec openclaw env | grep -E "TELEGRAM|EXA|GITHUB"

# Verify .env file permissions (should be 600)
ls -la /mnt/disks/research/.secrets/.env
# Expected: -rw------- (owner read/write only)

# Fix permissions if incorrect
chmod 600 /mnt/disks/research/.secrets/.env
chown $(id -u):$(id -g) /mnt/disks/research/.secrets/.env
```

### Rotate Secrets (Best Practice: Quarterly)
```bash
# 1. Generate new Telegram bot token
# - Message @BotFather on Telegram
# - Send: /token
# - Send: @your_bot_name
# - Copy new token

# 2. Generate new GitHub Personal Access Token
# - Visit: https://github.com/settings/tokens
# - Click "Generate new token (classic)"
# - Set expiration: 90 days
# - Select scopes: repo, read:org
# - Copy token

# 3. Inside VM: Update .env file
nano /mnt/disks/research/.secrets/.env
# Replace old values with new tokens

# 4. Restart container to load new secrets
docker restart openclaw

# 5. Verify new tokens work
docker logs openclaw | grep -i "authenticated"
```

### Audit Container Security
```bash
# Inside VM: Check container security settings
docker inspect openclaw --format='{{.HostConfig.ReadonlyRootfs}}'
# Expected: true

docker inspect openclaw --format='{{.HostConfig.CapDrop}}'
# Expected: [ALL]

docker inspect openclaw --format='{{.HostConfig.CapAdd}}'
# Expected: [CHOWN DAC_OVERRIDE]

docker inspect openclaw --format='{{.HostConfig.SecurityOpt}}'
# Expected: [no-new-privileges:true]

# Check for mounted Docker socket (SHOULD NOT EXIST)
docker inspect openclaw --format='{{json .Mounts}}' | grep docker.sock
# Expected: No output (empty)
```

### Service Account Permissions
```bash
# From your Mac: View service account roles
gcloud projects get-iam-policy orphansinthedesert \
  --flatten="bindings[].members" \
  --filter="bindings.members:serviceAccount:openclaw-agent-v3@orphansinthedesert.iam.gserviceaccount.com" \
  --format="table(bindings.role)"

# Expected output:
# roles/logging.logWriter
# roles/monitoring.metricWriter

# Test service account permissions (should FAIL for compute)
gcloud compute instances list \
  --impersonate-service-account=openclaw-agent-v3@orphansinthedesert.iam.gserviceaccount.com
# Expected: ERROR: Permission denied
```

## 📅 SCHEDULING & AUTOMATION
### View Current Schedule
```bash
# From your Mac: Check instance schedule policy
gcloud compute resource-policies describe openclaw-day-shift \
  --region=us-east4

# View which VMs use this schedule
gcloud compute instances describe openclaw-secure-node \
  --zone=us-east4-a \
  --format="get(resourcePolicies)"
```

### Modify Schedule
```bash
# From your Mac: Change to 8AM-6PM (10 hours)
gcloud compute resource-policies update instance-schedule openclaw-day-shift \
  --region=us-east4 \
  --vm-start-schedule="0 8 * * *" \
  --vm-stop-schedule="0 18 * * *"

# Temporarily disable schedule (keeps VM running 24/7)
gcloud compute instances remove-resource-policies openclaw-secure-node \
  --zone=us-east4-a \
  --resource-policies=openclaw-day-shift

# Re-enable schedule
gcloud compute instances add-resource-policies openclaw-secure-node \
  --zone=us-east4-a \
  --resource-policies=openclaw-day-shift

# Delete schedule policy completely
gcloud compute resource-policies delete openclaw-day-shift \
  --region=us-east4
```

### Manual Schedule Override
```bash
# From your Mac: Keep VM running past scheduled stop time
# (Must run BEFORE 5:00 PM on the day you want to extend)
gcloud compute instances add-metadata openclaw-secure-node \
  --zone=us-east4-a \
  --metadata=overtime_active=true

# Reset to normal schedule (VM will stop at next scheduled time)
gcloud compute instances add-metadata openclaw-secure-node \
  --zone=us-east4-a \
  --metadata=overtime_active=false
```

## 💰 BILLING & COST MANAGEMENT
### View Current Month Costs
```bash
# From your Mac: Get billing account ID
gcloud beta billing projects describe orphansinthedesert \
  --format="value(billingAccountName)"

# View budget status
gcloud beta billing budgets list \
  --billing-account=XXXXXX-XXXXXX-XXXXXX \
  --format="table(displayName, budgetAmount, thresholdRules)"

# Check current spend (requires billing export to BigQuery)
# Visit: https://console.cloud.google.com/billing/
```

### Create/Update Budget Alert
```bash
# From your Mac: Create $20/month budget with email alerts
# First, create notification channel
gcloud alpha monitoring channels create \
  --display-name="Budget Alert Email" \
  --type=email \
  --channel-labels=email_address=your-email@example.com

# Get channel ID
gcloud alpha monitoring channels list \
  --format="value(name)"

# Create budget
gcloud beta billing budgets create \
  --billing-account=$(gcloud beta billing projects describe orphansinthedesert --format="value(billingAccountName)") \
  --display-name="OpenClaw Monthly Budget" \
  --budget-amount=20USD \
  --threshold-rule=percent=50 \
  --threshold-rule=percent=90 \
  --threshold-rule=percent=100,basis=forecasted-spend \
  --notification-channels=projects/orphansinthedesert/notificationChannels/CHANNEL_ID

# Update existing budget
gcloud beta billing budgets update BUDGET_ID \
  --billing-account=XXXXXX-XXXXXX-XXXXXX \
  --budget-amount=15USD
```

### Estimate Daily Costs
```bash
# From your Mac: View VM uptime today
gcloud compute instances describe openclaw-secure-node \
  --zone=us-east4-a \
  --format="get(lastStartTimestamp)"

# Calculate runtime hours (manual math)
# Current time - Start time = Hours running
# e2-medium = $0.033460/hour
# 8 hours/day = $0.27/day = $8.09/month
```

### Cost Breakdown by Resource
```bash
# From your Mac: List all billable resources
gcloud compute instances list \
  --format="table(name,zone,machineType,status,scheduling.automaticRestart)"

gcloud compute disks list \
  --format="table(name,zone,sizeGb,type,status)"

gcloud compute routers nats list \
  --router=openclaw-router \
  --region=us-east4 \
  --format="table(name,natIpAllocateOption)"

# Expected monthly costs:
# - e2-medium (8h/day): $8.09/month
# - 50GB pd-balanced: $5.00/month
# - Cloud NAT: ~$1.20/month
# - Egress (estimate): ~$0.53/month
# TOTAL: ~$14.82/month
```

## 🔍 MONITORING & LOGS
### View Application Logs
```bash
# From your Mac: View logs in Cloud Logging
gcloud logging read "resource.type=gce_instance AND resource.labels.instance_id=INSTANCE_ID" \
  --limit=50 \
  --format=json

# Inside VM: View container logs
docker logs openclaw --tail 100

# Inside VM: Search logs for errors
docker logs openclaw 2>&1 | grep -i error

# Inside VM: Export logs to file
docker logs openclaw > /tmp/openclaw-logs-$(date +%Y%m%d).txt
```

### Monitor VM Health
```bash
# From your Mac: Check VM is responsive
gcloud compute instances describe openclaw-secure-node \
  --zone=us-east4-a \
  --format="get(status)"

# Inside VM: Check system resources
top           # Real-time process monitor
htop          # Enhanced top (if installed)
free -h       # Memory usage
df -h         # Disk usage
uptime        # System uptime and load
```

### Network Diagnostics
```bash
# Inside VM: Test outbound internet (via Cloud NAT)
curl -I https://www.google.com
# Expected: HTTP/2 200

# Test Docker Hub connectivity
docker pull hello-world
# Expected: Success

# Test Telegram API
curl https://api.telegram.org/botYOUR_TOKEN/getMe
# Expected: JSON response with bot info

# Check DNS resolution
nslookup github.com
dig google.com
```

### Firewall Rule Debugging
```bash
# From your Mac: View firewall logs (if logging enabled)
gcloud logging read "resource.type=gce_subnetwork AND logName=projects/orphansinthedesert/logs/compute.googleapis.com%2Ffirewall" \
  --limit=20 \
  --format="table(timestamp,jsonPayload.connection,jsonPayload.disposition)"

# Test IAP tunnel connectivity
gcloud compute ssh openclaw-secure-node \
  --zone=us-east4-a \
  --tunnel-through-iap \
  --dry-run
# Shows the SSH command that will be executed
```

## 🛠️ TROUBLESHOOTING COMMANDS
### VM Won't Start
```bash
# From your Mac: Check for quota issues
gcloud compute project-info describe --project=orphansinthedesert

# Force delete and recreate (LAST RESORT - keeps disk)
gcloud compute instances delete openclaw-secure-node \
  --zone=us-east4-a \
  --keep-disks=all

# Then re-run provision.sh with --attach-disk flag
```

### Can't SSH via IAP
```bash
# From your Mac: Verify IAP is enabled
gcloud services list --enabled | grep iap

# Enable if missing
gcloud services enable iap.googleapis.com

# Check firewall rule exists
gcloud compute firewall-rules describe allow-ssh-iap

# Test IAP tunnel manually
gcloud compute start-iap-tunnel openclaw-secure-node 22 \
  --local-host-port=localhost:2222 \
  --zone=us-east4-a

# In new terminal, SSH via tunnel
ssh -p 2222 localhost
```

### Container Won't Start
```bash
# Inside VM: Check Docker daemon status
sudo systemctl status docker

# Restart Docker daemon
sudo systemctl restart docker

# Check Docker logs
sudo journalctl -u docker.service -n 

# Verify image exists
docker images | grep openclaw-hardened

# Try running container in foreground (see errors immediately)
docker run --rm -it \
  -v /mnt/disks/research/workspace:/home/clawuser/workspace:rw \
  --env-file /mnt/disks/research/.secrets/.env \
  openclaw-hardened

# Check for port conflicts
sudo netstat -tlnp | grep 18789
# OR
sudo ss -tlnp | grep 18789

# If port is occupied, find and kill process
sudo lsof -ti:18789 | xargs kill -9
```

### Disk Won't Mount
```bash
# Inside VM: Check if disk is attached
lsblk
# Should show /dev/sdb (or similar) with 50GB size

# Check for filesystem corruption
sudo fsck -n /dev/disk/by-id/google-openclaw-secure-node

# View kernel messages about disk
dmesg | grep -i error
dmesg | grep sd

# Manually mount with verbose output
sudo mount -v /dev/disk/by-id/google-openclaw-secure-node /mnt/disks/research

# If mount fails, check if directory exists
ls -la /mnt/disks/research
sudo mkdir -p /mnt/disks/research
```

### Out of Disk Space
```bash
# Inside VM: Find what's consuming space
du -sh /mnt/disks/research/* | sort -rh

# Clean Docker cache
docker system prune -a --volumes
# WARNING: This removes ALL unused images, containers, volumes

# Remove old container logs
sudo truncate -s 0 /var/lib/docker/containers/*/*-json.log

# Check for large log files
find /mnt/disks/research -type f -size +100M -exec ls -lh {} \;

# Compress old logs
find /mnt/disks/research/logs -name "*.log" -mtime +7 -exec gzip {} \;
```

### Memory Issues (OOM Killed)
```bash
# Inside VM: Check if container was OOM killed
docker inspect openclaw --format='{{.State.OOMKilled}}'
# If "true", container exceeded 1.8GB limit

# View memory usage history
docker stats openclaw --no-stream

# Check kernel OOM logs
dmesg | grep -i "out of memory"
sudo journalctl -k | grep -i oom

# Increase container memory limit (edit setup.sh)
# Change: --memory=1800m to --memory=3000m
# Then rebuild container
```

### Network Connectivity Issues
```bash
# Inside VM: Test DNS
nslookup api.telegram.org
# If fails, check /etc/resolv.conf

# Test Cloud NAT
curl -4 ifconfig.me
# Should return a public IP (NAT gateway IP)

# Verify default gateway
ip route show default

# Check iptables rules (shouldn't block outbound)
sudo iptables -L -n -v

# Test specific API endpoints
curl -v https://api.exa.ai/health
curl -v https://api.github.com/user -H "Authorization: Bearer YOUR_TOKEN"
```

### Startup Script Not Running
```bash
# Inside VM: Check startup script logs
sudo journalctl -u google-startup-scripts.service

# View metadata startup script
curl -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/attributes/startup-script

# Manually run startup script (for testing)
sudo google_metadata_script_runner startup

# Check if script completed
ls -la /var/log/google-startup-script.log
cat /var/log/google-startup-script.log
```

## 🔄 BACKUP & RESTORE PROCEDURES
### Full System Backup
```bash
# Method 1: Disk Snapshot (Recommended)
# From your Mac:
gcloud compute disks snapshot openclaw-secure-node \
  --zone=us-east4-a \
  --snapshot-names=openclaw-full-backup-$(date +%Y%m%d) \
  --description="Complete system backup including OS and data"

# Method 2: Data-Only Backup (Faster)
# Inside VM:
sudo tar -czf /tmp/research-backup-$(date +%Y%m%d-%H%M).tar.gz \
  -C /mnt/disks/research \
  workspace logs vector_db .secrets

# Copy to your Mac
# From your Mac:
gcloud compute scp openclaw-secure-node:/tmp/research-backup-*.tar.gz ~/Backups/ \
  --zone=us-east4-a \
  --tunnel-through-iap

# Method 3: Incremental Backup (Advanced)
# Inside VM: Install rsync (if not present)
sudo apt-get update && sudo apt-get install -y rsync

# Sync to external location (example: another GCS bucket)
gsutil -m rsync -r -d /mnt/disks/research gs://openclaw-backups/$(date +%Y%m%d)/
```

### Restore from Backup
```bash
# Restore from Snapshot:
# From your Mac:
# 1. Create new disk from snapshot
gcloud compute disks create openclaw-restored \
  --source-snapshot=openclaw-full-backup-YYYYMMDD \
  --zone=us-east4-a \
  --type=pd-balanced

# 2. Stop VM
gcloud compute instances stop openclaw-secure-node --zone=us-east4-a

# 3. Detach old disk
gcloud compute instances detach-disk openclaw-secure-node \
  --disk=openclaw-secure-node \
  --zone=us-east4-a

# 4. Attach restored disk
gcloud compute instances attach-disk openclaw-secure-node \
  --disk=openclaw-restored \
  --boot \
  --zone=us-east4-a

# 5. Start VM
gcloud compute instances start openclaw-secure-node --zone=us-east4-a

# Restore from tar.gz:
# Inside VM:
sudo tar -xzf /tmp/research-backup-YYYYMMDD-HHMM.tar.gz \
  -C /mnt/disks/research
docker restart openclaw
```

### Disaster Recovery (Complete Rebuild)
```bash
# From your Mac: If VM is completely corrupted
# 1. Create snapshot of existing disk (preserve data)
gcloud compute disks snapshot openclaw-secure-node \
  --zone=us-east4-a \
  --snapshot-names=openclaw-disaster-$(date +%Y%m%d)

# 2. Delete corrupted VM (keeps disk because auto-delete=no)
gcloud compute instances delete openclaw-secure-node \
  --zone=us-east4-a \
  --keep-disks=boot

# 3. Re-run provision script to create new VM
cd ~/sandbox/gcp
./provision.sh

# NOTE: New VM will auto-mount existing disk via startup script
# All data in /mnt/disks/research will be preserved
```

## 📊 PERFORMANCE OPTIMIZATION
### Reduce Startup Time
```bash
# Inside VM: Pre-pull Docker images
docker pull ghcr.io/openclaw/openclaw:latest

# Pin specific image version (avoid latest tag)
# Edit Dockerfile.hardened:
# FROM ghcr.io/openclaw/openclaw:v1.2.3

# Reduce startup script execution time
# Move heavy operations to instance/setup.sh instead of startup-script
```

### Optimize Disk Performance   
```bash
# From your Mac: Upgrade to SSD (pd-ssd) if budget allows
gcloud compute disks create openclaw-ssd \
  --size=50GB \
  --type=pd-ssd \
  --zone=us-east4-a
# Cost: $17/month (vs $5/month for pd-balanced)
# Performance: 30 IOPS/GB vs 6 IOPS/GB

# Inside VM: Enable TRIM for better SSD performance
sudo fstrim -v /mnt/disks/research

# Add to cron for weekly TRIM
echo "0 3 * * 0 /sbin/fstrim /mnt/disks/research" | sudo tee -a /etc/crontab
```

### Monitor Container Performance
```bash
# Inside VM: Install performance tools
sudo apt-get update && sudo apt-get install -y sysstat iotop

# Monitor disk I/O
sudo iotop -o

# Monitor system stats
sar -u 5 10    # CPU usage every 5 seconds, 10 iterations
sar -r 5 10    # Memory usage
sar -d 5 10    # Disk activity

# Profile container CPU usage
docker stats openclaw --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"
```

## 🧪 TESTING & VALIDATION
### Verify Secrets are Loaded
```bash
# Inside VM: Check environment variables in container
docker exec openclaw env | grep TELEGRAM_BOT_TOKEN
# Should show: TELEGRAM_BOT_TOKEN=123456789:ABC...

# Test Telegram bot connection
docker exec openclaw curl -s https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe | jq
# Should return bot info JSON

# Test from container shell
docker exec -it openclaw /bin/bash
# Inside container:
echo $TELEGRAM_BOT_TOKEN
openclaw test-connection --service telegram
exit
```

### Test Skills/Integrations
```bash
# Inside container: Test Exa search
docker exec openclaw openclaw skills test --skill exa --query "OpenAI GPT-4"

# Test GitHub integration
docker exec openclaw openclaw skills test --skill github --action list-repos

# Test browser automation (Playwright)
docker exec openclaw openclaw skills test --skill browser --url https://example.com

# View available skills
docker exec openclaw openclaw skills list
```

### Security Validation
```bash
# Inside VM: Verify container cannot escape
docker exec openclaw cat /proc/1/cgroup
# Should show docker container ID

# Verify read-only filesystem
docker exec openclaw touch /test.txt
# Expected: touch: cannot touch '/test.txt': Read-only file system

# Verify no Docker socket access
docker exec openclaw ls /var/run/docker.sock
# Expected: ls: cannot access '/var/run/docker.sock': No such file or directory

# Check capabilities
docker exec openclaw capsh --print
# Should only show: cap_chown, cap_dac_override

# Test privilege escalation (should fail)
docker exec openclaw sudo echo "test"
# Expected: bash: sudo: command not found
```

### Load Testing
```bash
# Inside VM: Simulate API load
for i in {1..100}; do
  docker exec openclaw curl -s http://localhost:18789/health &
done
wait

# Monitor during load
docker stats openclaw

# Check for memory leaks (run overnight)
watch -n 60 'docker stats openclaw --no-stream'
```

## 🔧 ADVANCED CONFIGURATION
### Enable Debug Logging
```bash
# Inside VM: Edit .env to enable debug mode
nano /mnt/disks/research/.secrets/.env

# Add:
# LOG_LEVEL=DEBUG
# OPENCLAW_DEBUG=true

# Restart container
docker restart openclaw

# View verbose logs
docker logs openclaw -f
```

### Custom Docker Build
```bash
# Inside VM: Modify Dockerfile
cd ~/openclaw
nano Dockerfile.hardened

# Make your changes, then rebuild
docker build -t openclaw-hardened:custom -f Dockerfile.hardened .

# Test new image
docker run --rm -it openclaw-hardened:custom openclaw --version

# If successful, stop old container and deploy new one
docker stop openclaw
docker rm openclaw
./setup.sh  # Edit setup.sh to use :custom tag if needed
```

### Add Custom Skills/Plugins
```bash
# Inside VM: Mount additional directory for custom code
# Edit setup.sh to add:
# -v /mnt/disks/research/plugins:/home/clawuser/plugins:ro \

# Create plugins directory
mkdir -p /mnt/disks/research/plugins

# Add your custom Python/JS files
nano /mnt/disks/research/plugins/my_skill.py

# Restart container
docker restart openclaw

# Verify plugin loaded
docker exec openclaw openclaw plugins list
```

### Multi-Container Setup (Advanced)
```bash
# Inside VM: Run companion services (e.g., Redis for caching)
docker run -d \
  --name openclaw-redis \
  --network=bridge \
  --memory=256m \
  --restart unless-stopped \
  redis:alpine redis-server --maxmemory 200mb --maxmemory-policy allkeys-lru

# Link to OpenClaw container (edit setup.sh)
# Add: --link openclaw-redis:redis \

# Configure OpenClaw to use Redis
nano /mnt/disks/research/.secrets/.env
# Add: REDIS_URL=redis://openclaw-redis:6379

docker restart openclaw
```

## 📱 TELEGRAM BOT MANAGEMENT
### Test Bot Connection
```bash
# From your Mac or inside VM:
curl https://api.telegram.org/bot<YOUR_TOKEN>/getMe

# Expected response:
# {"ok":true,"result":{"id":123456789,"is_bot":true,"first_name":"OpenClaw"...}}
```

### Send Test Message
```bash
# Send message to yourself
curl -X POST https://api.telegram.org/bot<YOUR_TOKEN>/sendMessage \
  -d chat_id=<YOUR_CHAT_ID> \
  -d text="OpenClaw is online! 🛡️"
```

### View Bot Updates
```bash
# Get recent messages sent to bot
curl https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates

# Clear pending updates
curl https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates?offset=-1
```

### Set Bot Commands (User Experience)
```bash
# Define bot command menu
curl -X POST https://api.telegram.org/bot<YOUR_TOKEN>/setMyCommands \
  -H "Content-Type: application/json" \
  -d '{
    "commands": [
      {"command": "start", "description": "Start OpenClaw agent"},
      {"command": "search", "description": "Search the web"},
      {"command": "status", "description": "Check system status"},
      {"command": "help", "description": "Show available commands"}
    ]
  }'
```

### Revoke and Regenerate Bot Token
```bash
# If token is compromised:
# 1. Message @BotFather
# 2. Send: /revoke
# 3. Select your bot
# 4. Send: /token to get new token
# 5. Update .env file inside VM
# 6. Restart container
```

## 🌐 NETWORKING ADVANCED
### View Cloud NAT Statistics
```bash
# From your Mac: Check NAT usage
gcloud compute routers get-nat-mapping-info openclaw-router \
  --region=us-east4 \
  --nat-name=openclaw-nat

# View NAT logs (if logging enabled)
gcloud logging read "resource.type=nat_gateway" \
  --limit=50 \
  --format="table(timestamp,jsonPayload.connection.src_ip,jsonPayload.connection.dest_ip,jsonPayload.allocation_status)"

# Estimate NAT costs
# Formula: $0.044/hour per NAT gateway + $0.045/GB processed
# For 8h/day: $0.044 * 8 * 30 = $10.56/month base
# Plus data: ~20GB/month * $0.045 = $0.90
# Note: Our setup uses lower cost due to minimal data transfer
```

### Test IAP Firewall Rule
```bash
# From your Mac: Verify IAP range is allowed
gcloud compute firewall-rules describe allow-ssh-iap \
  --format="get(sourceRanges)"
# Expected: ['35.235.240.0/20']

# Test connection from outside IAP range (should fail)
# This would require access from a non-IAP IP (intentionally blocked)

# View denied connections in logs
gcloud logging read 'resource.type="gce_subnetwork" AND jsonPayload.disposition="DENIED"' \
  --limit=10
```

### Custom VPC Network (Optional)
```bash
# From your Mac: Create dedicated VPC for isolation
gcloud compute networks create openclaw-vpc \
  --subnet-mode=custom \
  --bgp-routing-mode=regional

# Create subnet
gcloud compute networks subnets create openclaw-subnet \
  --network=openclaw-vpc \
  --region=us-east4 \
  --range=10.128.0.0/20

# Recreate VM in custom VPC (requires deleting existing VM first)
# Edit provision.sh to add: --network=openclaw-vpc --subnet=openclaw-subnet
```

## 🔐 IAM & PERMISSIONS
### List All Project Permissions
```bash
# From your Mac: View all IAM bindings
gcloud projects get-iam-policy orphansinthedesert \
  --format=json > project-iam-policy.json

# View human-readable format
gcloud projects get-iam-policy orphansinthedesert \
  --format="table(bindings.role,bindings.members)"
```

### Audit Service Account Usage
```bash
# From your Mac: See which resources use the service account
gcloud compute instances list \
  --filter="serviceAccounts.email:openclaw-agent-v3@orphansinthedesert.iam.gserviceaccount.com" \
  --format="table(name,zone,status)"

# View service account keys (should be none for VM-based auth)
gcloud iam service-accounts keys list \
  --iam-account=openclaw-agent-v3@orphansinthedesert.iam.gserviceaccount.com
# Expected: Only system-managed keys, no user-managed keys
```

### Grant Additional Permissions (If Needed)
```bash
# From your Mac: Example - Allow service account to read from GCS bucket
gcloud projects add-iam-policy-binding orphansinthedesert \
  --member="serviceAccount:openclaw-agent-v3@orphansinthedesert.iam.gserviceaccount.com" \
  --role="roles/storage.objectViewer" \
  --condition='expression=resource.name.startsWith("projects/_/buckets/openclaw-data"),title=LimitedBucketAccess'

# Remove permission
gcloud projects remove-iam-policy-binding orphansinthedesert \
  --member="serviceAccount:openclaw-agent-v3@orphansinthedesert.iam.gserviceaccount.com" \
  --role="roles/storage.objectViewer"
```

## 📦 INFRASTRUCTURE AS CODE (Export Current State)
### Export Current VM Configuration
```bash
# From your Mac: Get VM config in YAML
gcloud compute instances describe openclaw-secure-node \
  --zone=us-east4-a \
  --format=yaml > vm-config-backup.yaml

# Export as JSON (for Terraform import)
gcloud compute instances describe openclaw-secure-node \
  --zone=us-east4-a \
  --format=json > vm-config-backup.json

# Generate equivalent gcloud command
gcloud compute instances describe openclaw-secure-node \
  --zone=us-east4-a \
  --format="value(format('gcloud compute instances create {0} --machine-type={1} --zone={2}', name, machineType.basename(), zone.basename()))"
```

### Export Firewall Rules
```bash
# From your Mac: Export all firewall rules
gcloud compute firewall-rules list \
  --format=yaml > firewall-rules-backup.yaml

# Export specific rule
gcloud compute firewall-rules describe allow-ssh-iap \
  --format=yaml > allow-ssh-iap-backup.yaml
```

### Export to Terraform (Advanced)
```bash
# From your Mac: Use gcloud terraform export (experimental)
gcloud beta resource-config bulk-export \
  --resource-format=terraform \
  --resource-types=ComputeInstance,ComputeDisk,ComputeFirewall \
  --project=orphansinthedesert \
  --path=./terraform-export/

# This creates .tf files you can use with Terraform
```

## 🆘 EMERGENCY PROCEDURES
### Complete System Lockdown
```bash
# From your Mac: If security breach suspected

# 1. Immediately stop VM
gcloud compute instances stop openclaw-secure-node \
  --zone=us-east4-a

# 2. Create forensic snapshot
gcloud compute disks snapshot openclaw-secure-node \
  --zone=us-east4-a \
  --snapshot-names=forensic-$(date +%Y%m%d-%H%M%S) \
  --description="Emergency snapshot - suspected breach"

# 3. Revoke all service account permissions
gcloud projects remove-iam-policy-binding orphansinthedesert \
  --member="serviceAccount:openclaw-agent-v3@orphansinthedesert.iam.gserviceaccount.com" \
  --role="roles/logging.logWriter"

gcloud projects remove-iam-policy-binding orphansinthedesert \
  --member="serviceAccount:openclaw-agent-v3@orphansinthedesert.iam.gserviceaccount.com" \
  --role="roles/monitoring.metricWriter"

# 4. Disable service account
gcloud iam service-accounts disable openclaw-agent-v3@orphansinthedesert.iam.gserviceaccount.com

# 5. Block all firewall access
gcloud compute firewall-rules delete allow-ssh-iap --quiet

# 6. Investigate snapshot offline (attach to forensic VM)
```

### Rollback to Previous Snapshot
```bash
# From your Mac: List available snapshots
gcloud compute snapshots list \
  --filter="sourceDisk:openclaw-secure-node" \
  --sort-by=~creationTimestamp

# Restore to last known good state
# 1. Stop VM
gcloud compute instances stop openclaw-secure-node --zone=us-east4-a

# 2. Create new disk from snapshot
gcloud compute disks create openclaw-rollback-disk \
  --source-snapshot=openclaw-snapshot-YYYYMMDD-HHMM \
  --zone=us-east4-a

# 3. Detach current disk
gcloud compute instances detach-disk openclaw-secure-node \
  --disk=openclaw-secure-node \
  --zone=us-east4-a

# 4. Attach rollback disk
gcloud compute instances attach-disk openclaw-secure-node \
  --disk=openclaw-rollback-disk \
  --boot \
  --zone=us-east4-a

# 5. Rename disk to original name (for consistency)
gcloud compute disks describe openclaw-rollback-disk \
  --zone=us-east4-a \
  --format="get(name)"

# 6. Start VM with restored disk
gcloud compute instances start openclaw-secure-node --zone=us-east4-a
```

### Force Budget Cutoff
```bash
# From your Mac: Prevent runaway costs

# 1. Stop all compute instances
gcloud compute instances stop --all --zone=us-east4-a

# 2. Delete Cloud NAT (stops egress charges)
gcloud compute routers nats delete openclaw-nat \
  --router=openclaw-router \
  --region=us-east4 \
  --quiet

# 3. Set billing alerts to $0 (get notified of any charge)
gcloud beta billing budgets update BUDGET_ID \
  --billing-account=XXXXXX-XXXXXX-XXXXXX \
  --budget-amount=0.01USD \
  --threshold-rule=percent=100

# 4. Monitor daily costs
gcloud beta billing projects describe orphansinthedesert
```

## 🎓 LEARNING & EXPERIMENTATION
### Safe Testing Environment
```bash
# From your Mac: Create temporary test VM (preemptible = cheap)
gcloud compute instances create openclaw-test \
  --zone=us-east4-a \
  --machine-type=e2-micro \
  --preemptible \
  --no-address \
  --boot-disk-size=10GB \
  --metadata=enable-oslogin=TRUE

# Cost: ~$0.0025/hour = $0.02/day for testing
# Auto-deleted after 24 hours
```

### Simulate Failure Scenarios
```bash
# Inside VM: Test container auto-restart
docker stop openclaw
# Wait 10 seconds
docker ps
# Should show container restarted automatically (--restart unless-stopped)

# Test disk unmount/remount
sudo umount /mnt/disks/research
sudo mount /mnt/disks/research
docker restart openclaw

# Test OOM (Out of Memory)
docker exec openclaw stress --vm 1 --vm-bytes 2G --timeout 10s
# Container should be killed and restarted
```

### Benchmark Performance
```bash
# Inside VM: Install benchmarking tools
sudo apt-get install -y sysbench fio

# CPU benchmark
sysbench cpu --threads=2 --time=30 run

# Disk I/O benchmark (READ)
sudo fio --name=read_test --rw=read --bs=4k --size=1G \
  --filename=/mnt/disks/research/test_file --runtime=30

# Disk I/O benchmark (WRITE)
sudo fio --name=write_test --rw=write --bs=4k --size=1G \
  --filename=/mnt/disks/research/test_file --runtime=30

# Clean up test file
sudo rm /mnt/disks/research/test_file
```

## 📖 REFERENCE: GCP INSTANCE TYPES
```text
# e2-series machine types (used in this project)
e2-micro:    0.25 vCPU, 1GB RAM    = $6.11/month  (8h/day)
e2-small:    0.5 vCPU,  2GB RAM    = $12.22/month (8h/day)
e2-medium:   1 vCPU,    4GB RAM    = $24.45/month (8h/day) <- CURRENT
e2-standard-2: 2 vCPU,  8GB RAM    = $48.90/month (8h/day)

# To change machine type (VM must be stopped first):
gcloud compute instances stop openclaw-secure-node --zone=us-east4-a
gcloud compute instances set-machine-type openclaw-secure-node \
  --machine-type=e2-small \
  --zone=us-east4-a
gcloud compute instances start openclaw-secure-node --zone=us-east4-a
```

## 📖 REFERENCE: DISK TYPES & PRICING
```text
# Persistent Disk Types (50GB, us-east4)
pd-standard:  $2.00/month  (Slow HDD, legacy)
pd-balanced:  $5.00/month  (6 IOPS/GB, good value) <- CURRENT
pd-ssd:       $17.00/month (30 IOPS/GB, premium performance)
pd-extreme:   $65.00/month (Custom IOPS, overkill for this use case)

# To change disk type (requires snapshot/restore):
# 1. Create snapshot of current disk
gcloud compute disks snapshot openclaw-secure-node --zone=us-east4-a \
  --snapshot-names=upgrade-snapshot

# 2. Create new disk from snapshot with different type
gcloud compute disks create openclaw-secure-node-ssd \
  --source-snapshot=upgrade-snapshot \
  --type=pd-ssd \
  --zone=us-east4-a

# 3. Follow "Rollback to Previous Snapshot" procedure to swap disks
```

## 📖 REFERENCE: COMMON ERROR CODES
### HTTP 403: Permission Denied
```bash
# Cause: Service account lacks required IAM roles
# Fix: Add necessary role
gcloud projects add-iam-policy-binding orphansinthedesert \
  --member="serviceAccount:openclaw-agent-v3@orphansinthedesert.iam.gserviceaccount.com" \
  --role="roles/REQUIRED_ROLE"
```

### HTTP 429: Rate Limit Exceeded
``bash
# Cause: Too many API requests to Telegram/GitHub/Exa
# Fix: Implement rate limiting in OpenClaw config
# Inside VM:
nano /mnt/disks/research/.secrets/.env
# Add: RATE_LIMIT_REQUESTS_PER_MINUTE=30
docker restart openclaw
```

### RESOURCE_EXHAUSTED: Quota Exceeded
```bash
# Cause: GCP project quota limits reached
# Check quotas:
gcloud compute project-info describe --project=orphansinthedesert

# Request quota increase:
# Visit: https://console.cloud.google.com/iam-admin/quotas
```

### INVALID_ARGUMENT: Invalid Zone
```bash
# Cause: Typo in zone name or resource not available in zone
# List available zones:
gcloud compute zones list --filter="region:(us-east4)"

# Verify resource availability:
gcloud compute machine-types list --zones=us-east4-a
```

## 📖 REFERENCE: USEFUL ALIASES (Add to ~/.zshrc)
```bash
# Add these to your Mac's ~/.zshrc for quick access

# OpenClaw shortcuts
alias oc-ssh='gcloud compute ssh openclaw-secure-node --project=orphansinthedesert --zone=us-east4-a --tunnel-through-iap'
alias oc-tunnel='gcloud compute ssh openclaw-secure-node --tunnel-through-iap --zone=us-east4-a --project=orphansinthedesert -- -L 18789:localhost:18789 -N'
alias oc-start='gcloud compute instances start openclaw-secure-node --zone=us-east4-a --project=orphansinthedesert'
alias oc-stop='gcloud compute instances stop openclaw-secure-node --zone=us-east4-a --project=orphansinthedesert'
alias oc-status='gcloud compute instances describe openclaw-secure-node --zone=us-east4-a --format="get(status)"'
alias oc-logs='gcloud logging
```

## 📄 FILE: MANUAL_COMMANDS.md (continued)
```bash
# Add these to your Mac's ~/.zshrc for quick access

# OpenClaw shortcuts
alias oc-ssh='gcloud compute ssh openclaw-secure-node --project=orphansinthedesert --zone=us-east4-a --tunnel-through-iap'
alias oc-tunnel='gcloud compute ssh openclaw-secure-node --tunnel-through-iap --zone=us-east4-a --project=orphansinthedesert -- -L 18789:localhost:18789 -N'
alias oc-start='gcloud compute instances start openclaw-secure-node --zone=us-east4-a --project=orphansinthedesert'
alias oc-stop='gcloud compute instances stop openclaw-secure-node --zone=us-east4-a --project=orphansinthedesert'
alias oc-status='gcloud compute instances describe openclaw-secure-node --zone=us-east4-a --format="get(status)"'
alias oc-logs='gcloud logging read "resource.type=gce_instance" --limit=50 --format=json'
alias oc-cost='gcloud beta billing projects describe orphansinthedesert'
alias oc-snapshot='gcloud compute disks snapshot openclaw-secure-node --zone=us-east4-a --snapshot-names=openclaw-$(date +%Y%m%d-%H%M)'

# Docker shortcuts (use after oc-ssh)
alias doc-logs='docker logs openclaw -f'
alias doc-restart='docker restart openclaw'
alias doc-stats='docker stats openclaw --no-stream'
alias doc-shell='docker exec -it openclaw /bin/bash'
alias doc-env='docker exec openclaw env | grep -v PASSWORD'

# Apply aliases immediately:
source ~/.zshrc
```

## 📖 REFERENCE: CRON JOBS (Inside VM)
```bash
# Inside VM: Set up automated tasks with cron
crontab -e

# Add these lines:

# Daily disk cleanup (2 AM)
0 2 * * * find /mnt/disks/research/logs -name "*.log" -mtime +30 -delete

# Weekly disk TRIM (Sunday 3 AM)
0 3 * * 0 /sbin/fstrim -v /mnt/disks/research

# Weekly container restart (Sunday 4 AM, reduces memory leaks)
0 4 * * 0 /usr/bin/docker restart openclaw

# Daily backup to tar.gz (1 AM, keeps last 7 days)
0 1 * * * tar -czf /tmp/daily-backup-$(date +\%u).tar.gz -C /mnt/disks/research workspace logs .secrets

# Monthly snapshot (1st of month, 5 AM)
0 5 1 * * /snap/bin/gcloud compute disks snapshot openclaw-secure-node --zone=us-east4-a --snapshot-names=monthly-$(date +\%Y\%m)

# Save and exit (Ctrl+X, Y, Enter)
```

## 📖 REFERENCE: SYSTEMD SERVICE (Alternative to Docker Restart Policy)
```bash
# Inside VM: Create systemd service for OpenClaw container
sudo nano /etc/systemd/system/openclaw.service

# Paste this content:
[Unit]
Description=OpenClaw Hardened Research Agent
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/docker start openclaw
ExecStop=/usr/bin/docker stop -t 30 openclaw
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target

# Save and enable
sudo systemctl daemon-reload
sudo systemctl enable openclaw.service
sudo systemctl start openclaw.service

# Check status
sudo systemctl status openclaw.service
```

## 📖 REFERENCE: HELPFUL ONE-LINERS
```bash
# Find all API keys in .env (NEVER share this output!)
grep -E 'TOKEN|KEY|SECRET' /mnt/disks/research/.secrets/.env

# Count total files in workspace
find /mnt/disks/research/workspace -type f | wc -l

# Find files modified in last 24 hours
find /mnt/disks/research -type f -mtime -1 -ls

# Calculate total research data size
du -sh /mnt/disks/research

# Show top 10 largest files
find /mnt/disks/research -type f -exec du -h {} + | sort -rh | head -10

# Check if container is healthy
docker inspect openclaw --format='{{.State.Health.Status}}'

# Get container uptime
docker inspect openclaw --format='{{.State.StartedAt}}'

# View real-time disk writes
watch -n 1 'iostat -x 1 2 | grep sdb'

# Monitor network connections from container
docker exec openclaw netstat -tupn

# Export all environment variables from container (redact secrets!)
docker exec openclaw env | sed 's/=.*/=***REDACTED***/' > container-env-safe.txt

# Generate SSH config for easy access
cat >> ~/.ssh/config <<EOF
Host openclaw
  HostName openclaw-secure-node
  ProxyCommand gcloud compute start-iap-tunnel openclaw-secure-node 22 --listen-on-stdin --zone=us-east4-a --project=orphansinthedesert
  User $(whoami)
  IdentityFile ~/.ssh/google_compute_engine
EOF
# Now you can: ssh openclaw
```