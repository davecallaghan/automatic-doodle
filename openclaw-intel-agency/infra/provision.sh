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
    --metadata=enable-oslogin=TRUE,block-project-ssh-keys=TRUE,serial-port-enable=FALSE \
    --metadata-from-file=startup-script=./startup.sh,shutdown-script=./shutdown.sh

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
echo "   VM (8h/day): \$8.09/month"
echo "   Disk (24/7): \$5.00/month"
echo "   Total (before NAT): \$13.09/month"
echo ""
echo "⏭️  Next Steps:"
echo "   1. Run: ./networking.sh"
echo "   2. Run: ./schedule.sh"
echo "   3. SSH into VM: gcloud compute ssh $VM_NAME --zone=$ZONE --tunnel-through-iap"
echo ""
