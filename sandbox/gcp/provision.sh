#!/bin/bash
# =============================================================================
# GCP INFRASTRUCTURE PROVISIONING SCRIPT v4.0
# =============================================================================
# Purpose: Creates the VM, service account, and persistent disk
#
# What this creates:
#   - Service account with logging/monitoring roles ONLY
#   - e2-medium VM with Shielded VM features enabled
#   - 50GB pd-balanced persistent disk (auto-delete=NO)
#   - Startup script that mounts disk safely (preserves data)
#   - Shutdown script for graceful container stop
#
# Security features:
#   - No public IP address (IAP-only access)
#   - Minimal OAuth scopes (logging.write, monitoring.write)
#   - OS Login enforced (no SSH key sprawl)
#   - Serial port disabled (no console bypass)
#   - Deletion protection enabled
#
# Cost: $8.09/month for VM (8h/day) + $5.00/month for disk
# =============================================================================

set -euo pipefail

# -----------------------------------------------------------------------------
# CONFIGURATION VARIABLES
# -----------------------------------------------------------------------------
PROJECT_ID="orphansinthedesert"
ZONE="us-east4-a"
REGION="us-east4"
SA_NAME="openclaw-agent-v3"
VM_NAME="openclaw-secure-node"
DISK_SIZE="50"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "🔐 Creating Least-Privilege Service Account..."

# Check if service account already exists
if ! gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" &>/dev/null; then
    gcloud iam service-accounts create "$SA_NAME" \
        --project="$PROJECT_ID" \
        --display-name="OpenClaw Agent (Logging Only)" \
        --description="Restricted service account for OpenClaw VM - can only write logs and metrics"
    echo "✅ Service account created: $SA_EMAIL"
else
    echo "ℹ️  Service account already exists: $SA_EMAIL"
fi

# -----------------------------------------------------------------------------
# GRANT MINIMAL IAM ROLES
# -----------------------------------------------------------------------------
# CRITICAL: These are the ONLY permissions the VM will have
# - logging.logWriter: Write logs to Cloud Logging
# - monitoring.metricWriter: Write metrics to Cloud Monitoring
# - NO compute permissions (cannot modify itself or other VMs)
# - NO storage permissions (cannot access GCS buckets)
# -----------------------------------------------------------------------------

echo "🔒 Granting minimal IAM roles..."

for role in roles/logging.logWriter roles/monitoring.metricWriter; do
    # Check if binding already exists to avoid duplicates
    if ! gcloud projects get-iam-policy "$PROJECT_ID" \
        --flatten="bindings[].members" \
        --filter="bindings.role:$role AND bindings.members:serviceAccount:$SA_EMAIL" \
        --format="value(bindings.role)" | grep -q "$role"; then
        
        gcloud projects add-iam-policy-binding "$PROJECT_ID" \
            --member="serviceAccount:$SA_EMAIL" \
            --role="$role" \
            --condition=None
        echo "✅ Granted $role"
    else
        echo "ℹ️  Role $role already granted"
    fi
done

# -----------------------------------------------------------------------------
# CREATE VM INSTANCE
# -----------------------------------------------------------------------------
echo "🖥️  Creating Shielded VM with Persistent Disk..."

# Check if VM already exists
if gcloud compute instances describe "$VM_NAME" --zone="$ZONE" --project="$PROJECT_ID" &>/dev/null; then
    echo "⚠️  VM already exists: $VM_NAME"
    echo "   To recreate, first run: gcloud compute instances delete $VM_NAME --zone=$ZONE"
    exit 1
fi

gcloud compute instances create "$VM_NAME" \
    --project="$PROJECT_ID" \
    --zone="$ZONE" \
    --machine-type=e2-medium \
    --no-address \
    --network-tier=PREMIUM \
    --service-account="$SA_EMAIL" \
    --scopes=https://www.googleapis.com/auth/logging.write,https://www.googleapis.com/auth/monitoring.write \
    --tags=openclaw-secure \
    --shielded-secure-boot \
    --shielded-vtpm \
    --shielded-integrity-monitoring \
    --deletion-protection \
    --create-disk=auto-delete=no,boot=yes,device-name="$VM_NAME",image=projects/debian-cloud/global/images/family/debian-12,mode=rw,size="$DISK_SIZE",type=pd-balanced \
    --metadata=enable-oslogin=TRUE,block-project-ssh-keys=TRUE,serial-port-enable=FALSE,startup-script='#!/bin/bash

# =============================================================================
# STARTUP SCRIPT - Runs on every VM boot
# =============================================================================
# Purpose: Safely mount the persistent disk to /mnt/disks/research
#
# Key safety features:
#   - Only formats disk if completely blank (checks with blkid)
#   - Uses UUID in /etc/fstab (survives disk reattachment)
#   - Sets ownership to UID 1000 (clawuser in container)
#   - Creates required subdirectories
#
# Data Loss Prevention:
#   v1.0 BUG: Used "mkfs.ext4 -F" which ALWAYS formatted (data loss)
#   v4.0 FIX: Checks for existing filesystem first
# =============================================================================

set -euo pipefail

echo "$(date): Starting disk mount procedure..." | tee -a /var/log/startup-script.log

# Create mount point
mkdir -p /mnt/disks/research

# Identify the persistent disk device
DEVICE_NAME="/dev/disk/by-id/google-'"$VM_NAME"'"

# Check if already mounted (prevents double-mount on script re-run)
if mountpoint -q /mnt/disks/research; then
    echo "$(date): Disk already mounted at /mnt/disks/research" | tee -a /var/log/startup-script.log
else
    echo "$(date): Disk not mounted, proceeding with mount..." | tee -a /var/log/startup-script.log
    
    # CRITICAL: Only format if disk has NO filesystem
    # This prevents data loss on reboot
    if ! blkid "$DEVICE_NAME" &>/dev/null; then
        echo "$(date): Blank disk detected, formatting as ext4..." | tee -a /var/log/startup-script.log
        # -m 0: No reserved blocks (maximize usable space)
        # -E lazy_itable_init=0: Initialize immediately (safer)
        # -E discard: Enable TRIM support for SSD
        mkfs.ext4 -m 0 -E lazy_itable_init=0,lazy_journal_init=0,discard "$DEVICE_NAME"
    else
        echo "$(date): Existing filesystem detected, skipping format" | tee -a /var/log/startup-script.log
    fi
    
    # Mount with performance optimizations
    # - discard: TRIM support for SSD longevity
    # - defaults: Standard mount options
    # - noatime: Don'\''t update access time (reduces I/O operations)
    mount -o discard,defaults,noatime "$DEVICE_NAME" /mnt/disks/research
    echo "$(date): Disk mounted successfully" | tee -a /var/log/startup-script.log
    
    # Add to /etc/fstab for automatic mount on future boots
    # Using UUID instead of device path (more reliable)
    DISK_UUID=$(blkid -s UUID -o value "$DEVICE_NAME")
    if ! grep -q "$DISK_UUID" /etc/fstab; then
        echo "UUID=$DISK_UUID /mnt/disks/research ext4 discard,defaults,noatime 0 2" >> /etc/fstab
        echo "$(date): Added to /etc/fstab with UUID=$DISK_UUID" | tee -a /var/log/startup-script.log
    fi
fi

# Create required subdirectories if they don'\''t exist
mkdir -p /mnt/disks/research/{workspace,logs,vector_db,.secrets,.openclaw}

# Set ownership to UID 1000 (matches clawuser in container)
chown -R 1000:1000 /mnt/disks/research

# Set restrictive permissions
# 750 = owner:rwx, group:rx, others:none
chmod 750 /mnt/disks/research

# Extra security for secrets directory
chmod 700 /mnt/disks/research/.secrets

echo "$(date): Startup script completed successfully" | tee -a /var/log/startup-script.log
',shutdown-script='#!/bin/bash
# =============================================================================
# SHUTDOWN SCRIPT - Runs when VM stops (scheduled or manual)
# =============================================================================
# Purpose: Gracefully stop the OpenClaw container before VM shutdown
#
# Why this matters:
#   - Gives container 30 seconds to save state
#   - Prevents data corruption in vector_db
#   - Ensures logs are flushed to disk
#
# Schedule behavior:
#   - Runs at 5:00 PM EST daily (instance schedule)
#   - Also runs on manual "gcloud compute instances stop"
# =============================================================================

set -euo pipefail

echo "$(date): Shutdown script initiated" | tee -a /var/log/shutdown-script.log

# Check if Docker is running
if ! systemctl is-active --quiet docker; then
    echo "$(date): Docker service not running, skipping container stop" | tee -a /var/log/shutdown-script.log
    exit 0
fi

# Check if OpenClaw container exists and is running
if docker ps --format "{{.Names}}" | grep -q "^openclaw$"; then
    echo "$(date): Stopping openclaw container gracefully..." | tee -a /var/log/shutdown-script.log
    
    # Try to run sync command inside container (if OpenClaw has this feature)
    docker exec openclaw openclaw sync --all || echo "$(date): Sync command not available or failed" | tee -a /var/log/shutdown-script.log
    
    # Stop container with 30 second grace period
    # After 30s, Docker will send SIGKILL
    docker stop -t 30 openclaw
    
    echo "$(date): Container stopped successfully" | tee -a /var/log/shutdown-script.log
else
    echo "$(date): No running openclaw container found" | tee -a /var/log/shutdown-script.log
fi

echo "$(date): Shutdown script completed" | tee -a /var/log/shutdown-script.log
'

echo ""
echo "✅ VM provisioning complete!"
echo ""
echo "📋 Resource Summary:"
echo "   VM Name: $VM_NAME"
echo "   Zone: $ZONE"
echo "   Machine Type: e2-medium (1 vCPU, 4GB RAM)"
echo "   Disk: 50GB pd-balanced (persistent)"
echo "   Service Account: $SA_EMAIL"
echo ""
echo "🔐 Security Features Enabled:"
echo "   ✅ No public IP (IAP-only access)"
echo "   ✅ Shielded VM (Secure Boot + vTPM + Integrity Monitoring)"
echo "   ✅ OS Login enforced"
echo "   ✅ Deletion protection enabled"
echo "   ✅ Minimal service account scopes"
echo ""
echo "💰 Estimated Cost:"
echo "   VM (8h/day): $8.09/month"
echo "   Disk (24/7): $5.00/month"
echo "   Total (before NAT): $13.09/month"
echo ""
echo "⏭️  Next Steps:"
echo "   1. Run: ./networking.sh"
echo "   2. Run: ./schedule.sh"
echo "   3. SSH into VM: gcloud compute ssh $VM_NAME --zone=$ZONE --tunnel-through-iap"
echo ""
