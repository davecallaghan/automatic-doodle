#!/bin/bash
# =============================================================================
# INSTANCE SETUP SCRIPT v4.0
# =============================================================================
# Purpose: Run INSIDE the VM after SSH (one-time setup)
#
# What this does:
#   1. Installs Docker CE (official, not Debian's outdated version)
#   2. Adds current user to docker group
#   3. Creates .env file with placeholder secrets
#   4. Builds hardened Docker image from Dockerfile
#   5. Runs container with maximum security restrictions
#
# Prerequisites:
#   - VM must be running
#   - Persistent disk must be mounted at /mnt/disks/research
#   - You must have the Dockerfile.hardened file in ~/instance/
#
# Security features applied:
#   - Read-only root filesystem
#   - Dropped all Linux capabilities (except CHOWN, DAC_OVERRIDE)
#   - Memory limit (1.8GB max)
#   - CPU limit (1.8 cores max)
#   - Process limit (100 max)
#   - No new privileges (blocks setuid exploits)
#   - Secrets mounted read-only
# =============================================================================

set -euo pipefail

echo "🚀 Starting OpenClaw instance setup..."

# -----------------------------------------------------------------------------
# STEP 1: Install Docker CE
# -----------------------------------------------------------------------------
# Why not use apt-get install docker.io?
#   - Debian's docker.io package is often 6-12 months outdated
#   - Official Docker CE includes latest security patches
#
# Installation method: Official Docker convenience script
#   - Detects OS automatically (Debian 12 in our case)
#   - Adds Docker's official GPG key and APT repository
#   - Installs docker-ce, docker-ce-cli, containerd.io
# -----------------------------------------------------------------------------

echo "🐳 Installing Docker CE (this may take 2-3 minutes)..."

if command -v docker &>/dev/null; then
    echo "ℹ️  Docker already installed: $(docker --version)"
else
    # Download and run official Docker install script
    curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
    sudo sh /tmp/get-docker.sh
    rm /tmp/get-docker.sh
    echo "✅ Docker CE installed"
fi

# Add current user to docker group (avoids needing sudo for docker commands)
# Note: Requires logout/login or newgrp to take effect
if groups | grep -q docker; then
    echo "ℹ️  User already in docker group"
else
    sudo usermod -aG docker "$USER"
    echo "✅ User added to docker group"
    echo "⚠️  You'll need to log out and back in for group changes to take effect"
    echo "   Or run: newgrp docker"
fi

# Start and enable Docker service
sudo systemctl enable docker
sudo systemctl start docker

# -----------------------------------------------------------------------------
# STEP 2: Verify persistent disk is mounted
# -----------------------------------------------------------------------------
echo "💾 Verifying persistent disk mount..."

if ! mountpoint -q /mnt/disks/research; then
    echo "❌ ERROR: Persistent disk not mounted at /mnt/disks/research"
    echo "   The startup script should have mounted it automatically."
    echo "   Check: cat /var/log/startup-script.log"
    echo "   Manual mount: sudo mount /dev/disk/by-id/google-openclaw-secure-node /mnt/disks/research"
    exit 1
fi

echo "✅ Persistent disk mounted at /mnt/disks/research"
df -h /mnt/disks/research

# Ensure all required directories exist
sudo mkdir -p /mnt/disks/research/{workspace,logs,vector_db,.secrets,.openclaw}
sudo chown -R 1000:1000 /mnt/disks/research
sudo chmod 750 /mnt/disks/research
sudo chmod 700 /mnt/disks/research/.secrets

# -----------------------------------------------------------------------------
# STEP 3: Create .env file with secrets
# -----------------------------------------------------------------------------
# IMPORTANT: This creates a TEMPLATE with placeholder values
# You MUST edit this file and add your real API keys before container works
#
# Security notes:
#   - File permissions: 600 (owner read/write only)
#   - Location: On persistent disk (survives VM rebuilds)
#   - Mounted read-only into container (container cannot modify secrets)
# -----------------------------------------------------------------------------

echo "🔐 Creating secrets file..."

ENV_FILE="/mnt/disks/research/.secrets/.env"

if [ -f "$ENV_FILE" ]; then
    echo "ℹ️  Secrets file already exists: $ENV_FILE"
    echo "   Skipping creation (preserving existing secrets)"
else
    cat > "$ENV_FILE" <<'ENV_TEMPLATE'
# =============================================================================
# OPENCLAW SECRETS - EDIT THIS FILE WITH YOUR REAL API KEYS
# =============================================================================
# Security: This file is mounted READ-ONLY into the container
#           Permissions: 600 (owner read/write only)
#
# After editing, restart container: docker restart openclaw
# =============================================================================

# -----------------------------------------------------------------------------
# TELEGRAM BOT (Required for agent communication)
# -----------------------------------------------------------------------------
# Get token from: @BotFather on Telegram
#   1. Message @BotFather
#   2. Send: /newbot
#   3. Follow prompts to create bot
#   4. Copy token provided
#
# Get your chat ID:
#   1. Message your bot
#   2. Visit: https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
#   3. Look for "chat":{"id":XXXXXXX}
# -----------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN=your_token_from_botfather_here
TELEGRAM_CHAT_ID=your_numeric_chat_id_here

# -----------------------------------------------------------------------------
# EXA SEARCH API (Neural web search)
# -----------------------------------------------------------------------------
# Sign up: https://exa.ai
# Free tier: 1,000 searches/month
# -----------------------------------------------------------------------------
EXA_API_KEY=your_exa_api_key_here

# -----------------------------------------------------------------------------
# GITHUB PERSONAL ACCESS TOKEN (For repo operations)
# -----------------------------------------------------------------------------
# Create at: https://github.com/settings/tokens
# Scopes needed: repo, read:org
# Expiration: Set to 90 days and rotate quarterly
# -----------------------------------------------------------------------------
GITHUB_TOKEN=ghp_your_github_token_here

# -----------------------------------------------------------------------------
# OPTIONAL: Additional API Keys
# -----------------------------------------------------------------------------
# Add other services as needed:
# OPENAI_API_KEY=sk-your_key_here
# ANTHROPIC_API_KEY=sk-ant-your_key_here
# GOOGLE_AI_API_KEY=your_key_here

# -----------------------------------------------------------------------------
# OPENCLAW CONFIGURATION
# -----------------------------------------------------------------------------
LOG_LEVEL=INFO
OPENCLAW_PORT=18789
OPENCLAW_HOST=0.0.0.0

# Rate limiting (prevent API abuse)
RATE_LIMIT_REQUESTS_PER_MINUTE=30

ENV_TEMPLATE

    chmod 600 "$ENV_FILE"
    echo "✅ Secrets template created at: $ENV_FILE"
    echo ""
    echo "⚠️  IMPORTANT: Edit this file with your real API keys!"
    echo "   nano $ENV_FILE"
    echo ""
fi

# -----------------------------------------------------------------------------
# STEP 4: Build hardened Docker image
# -----------------------------------------------------------------------------
echo "🏗️  Building hardened Docker image (this may take 3-5 minutes)..."

# Check if Dockerfile exists
if [ ! -f ~/instance/Dockerfile.hardened ]; then
    echo "❌ ERROR: Dockerfile.hardened not found in ~/instance/"
    echo "   Copy it from your local machine using:"
    echo "   gcloud compute scp Dockerfile.hardened openclaw-secure-node:~/instance/ --zone=us-east4-a --tunnel-through-iap"
    exit 1
fi

cd ~/instance

# Build image with build-time optimizations
# --no-cache: Force fresh build (use on first run or after Dockerfile changes)
# Remove --no-cache for faster rebuilds if Dockerfile hasn't changed
docker build --no-cache -t openclaw-hardened -f Dockerfile.hardened .

echo "✅ Docker image built: openclaw-hardened"

# Verify image was built correctly
docker images | grep openclaw-hardened

# -----------------------------------------------------------------------------
# STEP 5: Run hardened container with maximum security
# -----------------------------------------------------------------------------
echo "🛡️  Starting OpenClaw container with security restrictions..."

# Stop and remove existing container if present
if docker ps -a --format '{{.Names}}' | grep -q '^openclaw$'; then
    echo "ℹ️  Removing existing openclaw container..."
    docker stop openclaw 2>/dev/null || true
    docker rm openclaw 2>/dev/null || true
fi

# -----------------------------------------------------------------------------
# DOCKER RUN COMMAND EXPLANATION
# -----------------------------------------------------------------------------
# This is the most critical security configuration in the entire system
# Each flag has been carefully chosen to minimize attack surface
#
# --name openclaw
#   Human-readable container name (easier to manage than random hash)
#
# --restart unless-stopped
#   Auto-restart on crash, but NOT if manually stopped
#   Survives VM reboots (started by Docker daemon on boot)
#
# --read-only
#   Container filesystem is IMMUTABLE
#   Malware cannot persist files to disk
#   Exception: /tmp is writable (see --tmpfs)
#
# --tmpfs /tmp:rw,noexec,nosuid,size=512m
#   Temporary filesystem in RAM (wiped on restart)
#   rw: Read/write (needed for temp files)
#   noexec: CANNOT execute binaries from /tmp (blocks common exploits)
#   nosuid: CANNOT use setuid binaries (blocks privilege escalation)
#   size=512m: Limit to prevent DoS via disk fill
#
# --cap-drop=ALL --cap-add=CHOWN --cap-add=DAC_OVERRIDE
#   Remove all 38 Linux capabilities, add back only 2 needed ones
#   CHOWN: Change file ownership (needed for workspace writes)
#   DAC_OVERRIDE: Bypass file permission checks (needed for mounted volumes)
#   Blocks: ptrace, sys_admin, net_admin, etc. (common exploit vectors)
#
# --security-opt=no-new-privileges:true
#   Prevents gaining MORE privileges than container started with
#   Blocks: setuid binaries, capability inheritance
#
# --pids-limit=100
#   Maximum 100 processes inside container
#   Prevents fork bombs (DoS attack)
#
# --memory=1800m
#   Hard limit: 1.8GB RAM (out of 4GB total on e2-medium)
#   Leaves 2.2GB for host OS and Docker daemon
#   Container killed if exceeds (prevents OOM on host)
#
# --cpus=1.8
#   Limit to 1.8 CPU cores (out of 1 vCPU on e2-medium)
#   Note: GCP e2-medium has shared CPU, can burst to full core temporarily
#   Prevents CPU starvation of host processes
#
# -p 127.0.0.1:18789:18789
#   Bind port ONLY to localhost (not 0.0.0.0)
#   Prevents access from internal network
#   Only accessible via IAP tunnel from your Mac
#
# -v /mnt/disks/research/workspace:/home/clawuser/workspace:rw
#   Mount workspace directory (read/write)
#   Container can create/modify research files
#   Data persists on 50GB disk (survives container deletion)
#
# -v /mnt/disks/research/logs:/home/clawuser/logs:rw
#   Separate mount for logs (organizational clarity)
#   Easier to backup/analyze logs independently
#
# -v /mnt/disks/research/vector_db:/home/clawuser/vector_db:rw
#   Vector database storage (for embeddings/semantic search)
#   Persists learned knowledge across restarts
#
# -v /mnt/disks/research/.secrets/.env:/home/clawuser/.env:ro
#   Mount secrets file as READ-ONLY
#   Container CANNOT modify its own secrets
#   Prevents malware from stealing/changing API keys
#
# --user $(id -u):$(id -g)
#   Run as current user (UID 1000), not root
#   Files created by container owned by your user
#   No privilege escalation possible
#
# --env-file /mnt/disks/research/.secrets/.env
#   Load environment variables from .env file
#   Makes secrets available to OpenClaw application
#
# openclaw-hardened
#   Image name built in previous step
# -----------------------------------------------------------------------------

docker run -d \
  --name openclaw \
  --restart unless-stopped \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=512m \
  --cap-drop=ALL \
  --cap-add=CHOWN \
  --cap-add=DAC_OVERRIDE \
  --security-opt=no-new-privileges:true \
  --pids-limit=100 \
  --memory=1800m \
  --cpus=1.8 \
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

echo ""
echo "✅ OpenClaw container started!"
echo ""

# Wait for container to fully start
sleep 5

# -----------------------------------------------------------------------------
# STEP 6: Verify container is running
# -----------------------------------------------------------------------------
echo "🔍 Verifying container status..."

if docker ps | grep -q openclaw; then
    echo "✅ Container is running"
    docker ps --filter "name=openclaw" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
else
    echo "❌ Container failed to start"
    echo "   View logs: docker logs openclaw"
    exit 1
fi

# Check container health
sleep 10
HEALTH_STATUS=$(docker inspect openclaw --format='{{.State.Health.Status}}' 2>/dev/null || echo "no healthcheck")

if [ "$HEALTH_STATUS" = "healthy" ]; then
    echo "✅ Container health check: HEALTHY"
elif [ "$HEALTH_STATUS" = "starting" ]; then
    echo "⏳ Container health check: STARTING (wait 30 seconds)"
elif [ "$HEALTH_STATUS" = "no healthcheck" ]; then
    echo "ℹ️  No health check configured in Dockerfile"
else
    echo "⚠️  Container health check: $HEALTH_STATUS"
fi

# Display first 20 lines of logs
echo ""
echo "📋 Container logs (first 20 lines):"
echo "════════════════════════════════════════════════════════════════════════"
docker logs openclaw --tail 20
echo "════════════════════════════════════════════════════════════════════════"
echo ""

# -----------------------------------------------------------------------------
# STEP 7: Display next steps
# -----------------------------------------------------------------------------
echo "✅ Instance setup complete!"
echo ""
echo "📋 Container Information:"
echo "   Name: openclaw"
echo "   Image: openclaw-hardened"
echo "   Status: $(docker inspect openclaw --format='{{.State.Status}}')"
echo "   Uptime: $(docker inspect openclaw --format='{{.State.StartedAt}}')"
echo "   Memory Limit: 1.8GB"
echo "   CPU Limit: 1.8 cores"
echo ""
echo "🔐 Security Features Active:"
echo "   ✅ Read-only filesystem"
echo "   ✅ No new privileges"
echo "   ✅ Capabilities dropped (only CHOWN + DAC_OVERRIDE)"
echo "   ✅ Process limit: 100"
echo "   ✅ Secrets mounted read-only"
echo "   ✅ Port bound to localhost only"
echo ""
echo "📂 Mounted Volumes:"
echo "   Workspace: /mnt/disks/research/workspace → /home/clawuser/workspace"
echo "   Logs: /mnt/disks/research/logs → /home/clawuser/logs"
echo "   Vector DB: /mnt/disks/research/vector_db → /home/clawuser/vector_db"
echo "   Secrets: /mnt/disks/research/.secrets/.env → /home/clawuser/.env (read-only)"
echo ""
echo "⚠️  IMPORTANT: Configure your API keys!"
echo "   1. Edit secrets file:"
echo "      nano /mnt/disks/research/.secrets/.env"
echo ""
echo "   2. Add your real tokens:"
echo "      - TELEGRAM_BOT_TOKEN (from @BotFather)"
echo "      - TELEGRAM_CHAT_ID (from /getUpdates)"
echo "      - EXA_API_KEY (from exa.ai)"
echo "      - GITHUB_TOKEN (from github.com/settings/tokens)"
echo ""
echo "   3. Restart container to load new secrets:"
echo "      docker restart openclaw"
echo ""
echo "🌐 Access from your Mac:"
echo "   1. Create IAP tunnel (in new terminal on your Mac):"
echo "      gcloud compute start-iap-tunnel openclaw-secure-node 18789 \\"
echo "        --local-host-port=localhost:18789 \\"
echo "        --zone=us-east4-a"
echo ""
echo "   2. Open browser:"
echo "      http://localhost:18789"
echo ""
echo "📊 Useful Commands:"
echo "   View logs: docker logs openclaw -f"
echo "   Restart: docker restart openclaw"
echo "   Shell access: docker exec -it openclaw /bin/bash"
echo "   Container stats: docker stats openclaw"
echo ""
echo "🆘 Troubleshooting:"
echo "   If container won't start:"
echo "🆘 Troubleshooting:"
echo "   If container won't start:"
echo "     docker logs openclaw"
echo "     docker inspect openclaw"
echo ""
echo "   If secrets not loading:"
echo "     cat /mnt/disks/research/.secrets/.env"
echo "     ls -la /mnt/disks/research/.secrets/"
echo ""
echo "   Check disk space:"
echo "     df -h /mnt/disks/research"
echo ""
