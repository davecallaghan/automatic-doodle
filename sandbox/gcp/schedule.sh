#!/bin/bash
# =============================================================================
# GCP INSTANCE SCHEDULING SCRIPT v4.0
# =============================================================================
# Purpose: Configure automatic VM start/stop times
#
# Schedule: 9:00 AM - 5:00 PM EST (8 hours/day)
#   - Monday-Sunday (7 days/week)
#   - Timezone: America/New_York (handles EST/EDT automatically)
#
# Cost impact:
#   - e2-medium 24/7: $24.45/month
#   - e2-medium 8h/day: $8.09/month
#   - Savings: $16.36/month (67% reduction)
#
# Override capability:
#   - Set metadata "overtime_active=true" to prevent auto-stop
#   - Useful for long-running research tasks
#
# Note: Disk costs are NOT affected by schedule (charged 24/7)
# =============================================================================

set -euo pipefail

PROJECT_ID="orphansinthedesert"
REGION="us-east4"
ZONE="us-east4-a"
VM_NAME="openclaw-secure-node"
POLICY_NAME="openclaw-day-shift"

echo "📅 Configuring Instance Schedule (9AM-5PM EST, 8h/day)..."

# -----------------------------------------------------------------------------
# CREATE RESOURCE POLICY
# -----------------------------------------------------------------------------
# Resource policies are regional (apply to all VMs in region)
# Cron format: "minute hour day month weekday"
#   - Start: "0 9 * * *" = Every day at 9:00 AM
#   - Stop: "0 17 * * *" = Every day at 5:00 PM (17:00 in 24h format)
#
# Timezone handling:
#   - America/New_York automatically adjusts for DST
#   - EST = UTC-5, EDT = UTC-4
# -----------------------------------------------------------------------------

if gcloud compute resource-policies describe "$POLICY_NAME" \
    --region="$REGION" \
    --project="$PROJECT_ID" &>/dev/null; then
    echo "ℹ️  Schedule policy '$POLICY_NAME' already exists"
    echo "   To modify: gcloud compute resource-policies update instance-schedule $POLICY_NAME --region=$REGION"
else
    gcloud compute resource-policies create instance-schedule "$POLICY_NAME" \
        --project="$PROJECT_ID" \
        --region="$REGION" \
        --vm-start-schedule="0 9 * * *" \
        --vm-stop-schedule="0 17 * * *" \
        --timezone="America/New_York" \
        --description="OpenClaw work hours: 9AM-5PM EST daily (8 hours)"
    
    echo "✅ Instance schedule policy created"
fi

# -----------------------------------------------------------------------------
# ATTACH POLICY TO VM
# -----------------------------------------------------------------------------
# Links the schedule policy to our specific VM
# VM must exist before running this command
# -----------------------------------------------------------------------------

echo "🔗 Attaching schedule policy to VM..."

# Check if VM exists
if ! gcloud compute instances describe "$VM_NAME" \
    --zone="$ZONE" \
    --project="$PROJECT_ID" &>/dev/null; then
    echo "❌ ERROR: VM '$VM_NAME' does not exist"
    echo "   Run ./provision.sh first to create the VM"
    exit 1
fi

# Check if policy is already attached
CURRENT_POLICIES=$(gcloud compute instances describe "$VM_NAME" \
    --zone="$ZONE" \
    --project="$PROJECT_ID" \
    --format="value(resourcePolicies)" 2>/dev/null || echo "")

if echo "$CURRENT_POLICIES" | grep -q "$POLICY_NAME"; then
    echo "ℹ️  Schedule policy already attached to $VM_NAME"
else
    gcloud compute instances add-resource-policies "$VM_NAME" \
        --zone="$ZONE" \
        --project="$PROJECT_ID" \
        --resource-policies="$POLICY_NAME"
    
    echo "✅ Schedule policy attached to VM"
fi

echo ""
echo "✅ Scheduling configuration complete!"
echo ""
echo "📋 Schedule Details:"
echo "   Policy Name: $POLICY_NAME"
echo "   Start Time: 9:00 AM EST (daily)"
echo "   Stop Time: 5:00 PM EST (daily)"
echo "   Runtime: 8 hours/day"
echo "   Days: Monday-Sunday (7 days/week)"
echo ""
echo "💰 Cost Impact:"
echo "   Before schedule: $24.45/month (24/7)"
echo "   After schedule: $8.09/month (8h/day)"
echo "   Monthly savings: $16.36 (67% reduction)"
echo ""
echo "🔧 Manual Overrides:"
echo "   Start VM now:"
echo "     gcloud compute instances start $VM_NAME --zone=$ZONE"
echo ""
echo "   Stop VM now:"
echo "     gcloud compute instances stop $VM_NAME --zone=$ZONE"
echo ""
echo "   Prevent auto-stop today (run before 5 PM):"
echo "     gcloud compute instances add-metadata $VM_NAME --zone=$ZONE \\"
echo "       --metadata=overtime_active=true"
echo ""
echo "   Re-enable auto-stop:"
echo "     gcloud compute instances add-metadata $VM_NAME --zone=$ZONE \\"
echo "       --metadata=overtime_active=false"
echo ""
echo "✅ Infrastructure provisioning complete!"
echo "   Next: SSH into VM and run instance/setup.sh"
echo ""
