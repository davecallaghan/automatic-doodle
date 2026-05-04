#!/bin/bash
set -euo pipefail

echo "$(date): Running startup script..." | tee -a /var/log/startup-script.log

mkdir -p /mnt/disks/research/{workspace,logs,vector_db,.secrets}
chown -R 1000:1000 /mnt/disks/research
chmod 750 /mnt/disks/research
chmod 700 /mnt/disks/research/.secrets

echo "$(date): Startup script completed successfully" | tee -a /var/log/startup-script.log
