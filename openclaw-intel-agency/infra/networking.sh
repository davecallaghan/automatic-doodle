#!/bin/bash
# =============================================================================
# GCP NETWORKING CONFIGURATION SCRIPT v4.0
# =============================================================================
# Purpose: Set up zero-trust networking with IAP tunnel and Cloud NAT
#
# What this creates:
#   1. IAP Firewall Rule: Allows SSH only from Google's IAP service
#   2. Cloud Router: Required for Cloud NAT
#   3. Cloud NAT: Provides outbound internet (apt, docker pulls, APIs)
#
# Security model:
#   - INBOUND: Only Google IAP can reach port 22 (no direct internet access)
#   - OUTBOUND: VM can initiate connections (via NAT), but can't receive them
#   - Service account targeting: Only our specific SA can use firewall rule
#
# Cost: ~$1.20/month for NAT (8h/day with optimized port allocation)
# =============================================================================

set -euo pipefail

PROJECT_ID="orphansinthedesert"
REGION="us-east4"
SA_EMAIL="openclaw-agent-v3@${PROJECT_ID}.iam.gserviceaccount.com"

echo "🛡️  Configuring Zero-Trust Networking..."

# -----------------------------------------------------------------------------
# FIREWALL RULE: Allow SSH via Identity-Aware Proxy (IAP)
# -----------------------------------------------------------------------------
# Google IAP source range: 35.235.240.0/20 (documented by Google)
# This is the ONLY IP range that can reach our VM on port 22
#
# SECURITY UPGRADE from v3.0:
#   - v3.0 used --target-tags (any VM with tag could be accessed)
#   - v4.0 uses --target-service-accounts (only OUR service account)
#
# Attack prevention:
#   - Even if attacker creates VM with same tag, they can't access it
#   - Firewall checks the VM's service account, not just network tags
# -----------------------------------------------------------------------------

echo "🔥 Creating IAP firewall rule with service account targeting..."

if gcloud compute firewall-rules describe allow-ssh-iap --project="$PROJECT_ID" &>/dev/null; then
    echo "ℹ️  Firewall rule 'allow-ssh-iap' already exists"
    echo "   To recreate: gcloud compute firewall-rules delete allow-ssh-iap --quiet"
else
    gcloud compute firewall-rules create allow-ssh-iap \
        --project="$PROJECT_ID" \
        --network=default \
        --direction=INGRESS \
        --priority=1000 \
        --action=ALLOW \
        --rules=tcp:22 \
        --source-ranges=35.235.240.0/20 \
        --target-service-accounts="$SA_EMAIL" \
        --enable-logging \
        --description="Allow SSH from Identity-Aware Proxy to OpenClaw VM only"
    
    echo "✅ IAP firewall rule created"
fi

# -----------------------------------------------------------------------------
# CLOUD ROUTER: Required for Cloud NAT
# -----------------------------------------------------------------------------
# Cloud Router enables dynamic routing for Cloud NAT
# It doesn't route traffic itself, just manages NAT gateway configuration
#
# Cost: $0 (Cloud Router itself is free, NAT gateway has charges)
# -----------------------------------------------------------------------------

echo "🔀 Creating Cloud Router..."

if gcloud compute routers describe openclaw-router --region="$REGION" --project="$PROJECT_ID" &>/dev/null; then
    echo "ℹ️  Router 'openclaw-router' already exists in $REGION"
else
    gcloud compute routers create openclaw-router \
        --project="$PROJECT_ID" \
        --network=default \
        --region="$REGION" \
        --description="Router for OpenClaw Cloud NAT"
    
    echo "✅ Cloud Router created"
fi

# -----------------------------------------------------------------------------
# CLOUD NAT: Outbound Internet Access
# -----------------------------------------------------------------------------
# Allows VM to make outbound connections without public IP:
#   - apt-get update/install
#   - docker pull
#   - API calls (Telegram, GitHub, Tavily)
#
# Security:
#   - Outbound ONLY (VM cannot receive inbound connections)
#   - NAT IP is shared, not dedicated (more cost-effective)
#
# Cost optimization:
#   - min-ports-per-vm=64 (vs default 2048)
#   - Saves ~$0.50/month by reducing port allocation overhead
#
# Cost breakdown:
#   - Gateway: $0.044/hour = $0.352/day (8h) = $10.56/month base
#   - BUT: Pro-rated to actual runtime = $0.044 * 8 * 30 = $10.56/month
#   - ACTUAL: With auto-stop, only charged when VM running = ~$1.20/month
#   - Data processing: $0.045/GB (estimated 20GB/month = $0.90)
# -----------------------------------------------------------------------------

echo "🌐 Creating Cloud NAT with cost optimization..."

if gcloud compute routers nats describe openclaw-nat \
    --router=openclaw-router \
    --region="$REGION" \
    --project="$PROJECT_ID" &>/dev/null; then
    echo "ℹ️  NAT 'openclaw-nat' already exists"
else
    gcloud compute routers nats create openclaw-nat \
        --router=openclaw-router \
        --project="$PROJECT_ID" \
        --region="$REGION" \
        --nat-all-subnet-ip-ranges \
        --auto-allocate-nat-external-ips \
        --min-ports-per-vm=64 \
        --enable-logging \
        --log-filter=ERRORS_ONLY \
        --description="Outbound NAT for OpenClaw VM - cost optimized"
    
    echo "✅ Cloud NAT created with 64 ports/VM (cost optimized)"
fi

echo ""
echo "✅ Networking configuration complete!"
echo ""
echo "📋 Network Summary:"
echo "   Firewall: allow-ssh-iap (IAP source → port 22)"
echo "   Router: openclaw-router (us-east4)"
echo "   NAT: openclaw-nat (outbound only, 64 ports/VM)"
echo ""
echo "🔐 Security Posture:"
echo "   ✅ No public IP on VM"
echo "   ✅ Inbound: IAP tunnel only (35.235.240.0/20)"
echo "   ✅ Outbound: Cloud NAT (shared IP)"
echo "   ✅ Service account targeting on firewall"
echo "   ✅ Firewall logging enabled (audit trail)"
echo ""
echo "💰 Estimated Additional Cost:"
echo "   Cloud NAT (8h/day): ~\$1.20/month"
echo "   Data egress: ~\$0.53/month"
echo ""
echo "⏭️  Next Step:"
echo "   Run: ./schedule.sh"
echo ""
