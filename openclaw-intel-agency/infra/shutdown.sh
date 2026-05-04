#!/bin/bash
set -euo pipefail

echo "$(date): Shutdown script initiated" | tee -a /var/log/shutdown-script.log

if ! systemctl is-active --quiet docker; then
    echo "$(date): Docker service not running, skipping container stop" | tee -a /var/log/shutdown-script.log
    exit 0
fi

if docker ps --format "{{.Names}}" | grep -q "^openclaw$"; then
    echo "$(date): Stopping openclaw container gracefully..." | tee -a /var/log/shutdown-script.log
    docker exec openclaw openclaw sync --all || echo "$(date): Sync command not available or failed" | tee -a /var/log/shutdown-script.log
    docker stop -t 30 openclaw
    echo "$(date): Container stopped successfully" | tee -a /var/log/shutdown-script.log
else
    echo "$(date): No running openclaw container found" | tee -a /var/log/shutdown-script.log
fi

echo "$(date): Shutdown script completed" | tee -a /var/log/shutdown-script.log
