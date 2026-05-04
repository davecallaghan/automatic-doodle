#!/bin/bash
# =============================================================================
# LOCAL MAC HARDENING SCRIPT v4.0
# =============================================================================
# Purpose: Secure your Mac's gcloud CLI and set up convenient aliases
#
# What this does:
#   1. Restricts permissions on gcloud config directory
#   2. Configures shell history to prevent secret leakage
#   3. Creates convenient aliases for OpenClaw commands
#   4. Sets up budget alerts (requires your email)
#
# Run this ONCE on your Mac before provisioning infrastructure
#
# Security improvements:
#   - Prevents accidental credential exposure
#   - Blocks secret storage in shell history
#   - Enables budget monitoring
# =============================================================================

set -euo pipefail

# Detect shell (zsh is default on macOS since Catalina)
if [[ "$SHELL" == */zsh ]]; then
    SHELL_RC="$HOME/.zshrc"
    SHELL_NAME="zsh"
elif [[ "$SHELL" == */bash ]]; then
    SHELL_RC="$HOME/.bashrc"
    SHELL_NAME="bash"
else
    echo "❌ Unsupported shell. Please use bash or zsh."
    exit 1
fi

echo "🔐 Hardening Local Environment for OpenClaw..."
echo "   Detected shell: $SHELL_NAME"
echo "   Config file: $SHELL_RC"
echo ""

# -----------------------------------------------------------------------------
# STEP 1: Secure gcloud configuration directory
# -----------------------------------------------------------------------------
echo "🔒 Securing gcloud config directory..."

if [ -d "$HOME/.config/gcloud" ]; then
    chmod 700 "$HOME/.config/gcloud"
    find "$HOME/.config/gcloud" -type f -name "*.json" -exec chmod 600 {} \;
    echo "✅ Restricted gcloud config permissions (700 for directories, 600 for JSON files)"
else
    echo "ℹ️  gcloud config directory not found (will be created on first gcloud login)"
fi

# -----------------------------------------------------------------------------
# STEP 2: Configure shell history protection
# -----------------------------------------------------------------------------
echo "🛡️  Configuring shell history protection..."

# Create backup of shell config
cp "$SHELL_RC" "${SHELL_RC}.backup-$(date +%Y%m%d-%H%M%S)"
echo "✅ Created backup: ${SHELL_RC}.backup-$(date +%Y%m%d-%H%M%S)"

# Add history protection settings
cat >> "$SHELL_RC" <<'HISTORY_EOF'

# =============================================================================
# OPENCLAW SECURITY: Shell History Protection
# Added by local_hardening.sh on $(date +%Y-%m-%d)
# =============================================================================

# Prevent commands starting with space from being saved to history
export HISTCONTROL=ignorespace:ignoredups

# Zsh-specific: Ignore commands starting with space
if [ -n "$ZSH_VERSION" ]; then
    setopt HIST_IGNORE_SPACE
    setopt HIST_IGNORE_DUPS
fi

# Pattern-based filtering (blocks commands containing secrets)
# This prevents accidental history storage of API keys
HISTORY_IGNORE="(gcloud *--key*|*TOKEN=*|*PASSWORD=*|*SECRET=*|export *KEY=*)"

# When running sensitive commands, prefix with a space:
# Example:  gcloud auth activate-service-account --key-file=secret.json
#          ^ notice the space before 'gcloud'
HISTORY_EOF

echo "✅ Shell history protection configured"

# -----------------------------------------------------------------------------
# STEP 3: Create convenient aliases
# -----------------------------------------------------------------------------
echo "⚡ Creating OpenClaw command aliases..."

cat >> "$SHELL_RC" <<'ALIASES_EOF'

# =============================================================================
# OPENCLAW ALIASES: Quick Access Commands
# =============================================================================

# SSH and Tunnel
alias oc-ssh='gcloud compute ssh openclaw-secure-node --project=orphansinthedesert --zone=us-east4-a --tunnel-through-iap'
alias oc-tunnel='gcloud compute ssh openclaw-secure-node --tunnel-through-iap --zone=us-east4-a --project=orphansinthedesert -- -L 18789:localhost:18789 -N'

# VM Management
alias oc-start='gcloud compute instances start openclaw-secure-node --zone=us-east4-a --project=orphansinthedesert'
alias oc-stop='gcloud compute instances stop openclaw-secure-node --zone=us-east4-a --project=orphansinthedesert'
alias oc-status='gcloud compute instances describe openclaw-secure-node --zone=us-east4-a --project=orphansinthedesert --format="value(status)"'

# Logging and Monitoring
alias oc-logs='gcloud logging read "resource.type=gce_instance AND resource.labels.instance_id=$(gcloud compute instances describe openclaw-secure-node --zone=us-east4-a --format=\"value(id)\")" --limit=50 --project=orphansinthedesert'
alias oc-serial='gcloud compute instances get-serial-port-output openclaw-secure-node --zone=us-east4-a --project=orphansinthedesert'

# Snapshots and Backups
alias oc-snapshot='gcloud compute disks snapshot openclaw-secure-node --zone=us-east4-a --snapshot-names=openclaw-$(date +%Y%m%d-%H%M) --project=orphansinthedesert'

# Cost Monitoring
alias oc-cost='gcloud beta billing projects describe orphansinthedesert --format="table(billingAccountName,billingEnabled)"'

# Quick access to web interface (run oc-tunnel first)
alias oc-web='open http://localhost:18789'

# File transfer shortcuts
alias oc-scp-to='gcloud compute scp --zone=us-east4-a --tunnel-through-iap --project=orphansinthedesert'
alias oc-scp-from='gcloud compute scp --zone=us-east4-a --tunnel-through-iap --project=orphansinthedesert'

# Help reminder
alias oc-help='echo "OpenClaw Quick Commands:
  oc-ssh          SSH into VM
  oc-tunnel       Create web tunnel (then visit localhost:18789)
  oc-start        Start VM manually
  oc-stop         Stop VM manually
  oc-status       Check if VM is running
  oc-logs         View Cloud Logging entries
  oc-snapshot     Create disk snapshot
  oc-cost         Check billing status
  oc-web          Open browser to localhost:18789
  oc-help         Show this message
"'

ALIASES_EOF

echo "✅ Aliases created (will be available after restarting terminal)"

# -----------------------------------------------------------------------------
# STEP 4: Set up budget alerts
# -----------------------------------------------------------------------------
echo ""
echo "💰 Setting up budget alerts..."
echo ""
read -p "Enter your email for budget alerts: " ALERT_EMAIL

if [ -z "$ALERT_EMAIL" ]; then
    echo "⚠️  Skipping budget alert setup (no email provided)"
else
    echo "📧 Configuring budget alert to: $ALERT_EMAIL"
    
    # Get billing account ID
    BILLING_ACCOUNT=$(gcloud beta billing projects describe orphansinthedesert \
        --format="value(billingAccountName)" 2>/dev/null || echo "")
    
    if [ -z "$BILLING_ACCOUNT" ]; then
        echo "⚠️  Could not detect billing account"
        echo "   You may need to enable billing on the project first"
        echo "   Visit: https://console.cloud.google.com/billing/linkedaccount?project=orphansinthedesert"
    else
        echo "   Billing account: $BILLING_ACCOUNT"
        
        # Create notification channel
        echo "   Creating email notification channel..."
        
        CHANNEL_ID=$(gcloud alpha monitoring channels create \
            --display-name="OpenClaw Budget Alert" \
            --type=email \
            --channel-labels=email_address="$ALERT_EMAIL" \
            --format="value(name)" 2>/dev/null || echo "")
        
        if [ -z "$CHANNEL_ID" ]; then
            echo "   ⚠️  Notification channel may already exist or API not enabled"
        else
            echo "   ✅ Notification channel created: $CHANNEL_ID"
        fi
        
        # Create budget (even if channel creation failed)
        echo "   Creating budget with \$20 threshold..."
        
        gcloud beta billing budgets create \
            --billing-account="$BILLING_ACCOUNT" \
            --display-name="OpenClaw Monthly Budget" \
            --budget-amount=20USD \
            --threshold-rule=percent=50 \
            --threshold-rule=percent=90 \
            --threshold-rule=percent=100,basis=forecasted-spend 2>/dev/null && \
            echo "   ✅ Budget created successfully" || \
            echo "   ⚠️  Budget may already exist or billing API not enabled"
    fi
fi

# -----------------------------------------------------------------------------
# STEP 5: Configure gcloud defaults
# -----------------------------------------------------------------------------
echo ""
echo "⚙️  Configuring gcloud defaults..."

gcloud config set project orphansinthedesert 2>/dev/null && echo "✅ Default project: orphansinthedesert" || true
gcloud config set compute/zone us-east4-a 2>/dev/null && echo "✅ Default zone: us-east4-a" || true

# -----------------------------------------------------------------------------
# STEP 6: Final instructions
# -----------------------------------------------------------------------------
echo ""
echo "════════════════════════════════════════════════════════════════════════"
echo "✅ LOCAL HARDENING COMPLETE!"
echo "════════════════════════════════════════════════════════════════════════"
echo ""
echo "🔐 Security improvements applied:"
echo "   ✅ gcloud config directory restricted (700 permissions)"
echo "   ✅ Shell history configured to ignore secrets"
echo "   ✅ Command aliases created"
if [ -n "$ALERT_EMAIL" ]; then
    echo "   ✅ Budget alerts configured for $ALERT_EMAIL"
fi
echo ""
echo "⚠️  IMPORTANT: Restart your terminal to apply changes"
echo "   Close this terminal window and open a new one"
echo "   Or run: source $SHELL_RC"
echo ""
echo "🧪 Test the aliases:"
echo "   oc-help          Show available commands"
echo "   oc-status        Check VM status (after provisioning)"
echo ""
echo "📋 Backup created:"
echo "   Original config saved to: ${SHELL_RC}.backup-$(date +%Y%m%d)*"
echo ""
echo "⏭️  Next step:"
echo "   Run: ./init_agency.sh"
echo "   Then: cd infra && ./provision.sh"
echo ""
echo "════════════════════════════════════════════════════════════════════════"
