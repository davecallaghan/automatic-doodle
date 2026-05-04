# Databricks OSS Stack — Phase 1 Init Checklist

**Branch:** `databrick_init`
**Plan refs:** `EXECUTION_databricks_integration.md` Phase 1; `EXECUTION_integrity_engine.md` (deferred to Phase 2.5); `EXECUTION_public_commons.md` (deferred to Phase 6).

This is the operator runbook for getting the Unity Catalog + MLflow sidecars running on the existing OpenClaw VM and bootstrapping the `research` catalog. Phase 1 is **purely additive** — the running OpenClaw container is not touched. Phase 2 will wire the worker to the new sidecars.

---

## Prerequisites

- [ ] OpenClaw is already deployed and running per the existing Deployment_Checklist.md (Phase 1 completion checkpoint dated 2026-05-04).
- [ ] VM is `e2-standard-2` (or larger). If still on `e2-medium`, bump it before Phase 2 because UC + MLflow add ~768 MB RAM:
      ```bash
      oc-stop
      gcloud compute instances set-machine-type openclaw-secure-node \
        --machine-type=e2-standard-2 --zone=us-east4-a --project=orphansinthedesert
      oc-start
      ```
- [ ] Persistent disk `/mnt/disks/research` is mounted on the VM.

---

## Step 1 — Pull the `databrick_init` branch onto the VM

From your Mac:
```bash
gcloud compute scp \
  --zone=us-east4-a --tunnel-through-iap --project=orphansinthedesert \
  sandbox/gcp/sidecars.sh \
  openclaw/uc_init.py \
  openclaw/unity_catalog_setup.sql \
  openclaw-secure-node:~/openclaw/
```

Or, on the VM (if the repo is cloned there):
```bash
oc-ssh
cd ~/automatic-doodle
git fetch origin databrick_init
git checkout databrick_init
```

---

## Step 2 — Start the sidecars

On the VM:
```bash
~/openclaw/sidecars.sh        # or ~/automatic-doodle/sandbox/gcp/sidecars.sh
```

Expected output ends with:
```
✓ Unity Catalog responding at http://localhost:8080
✓ MLflow responding at http://localhost:5000
```

If either fails: `docker logs unity-catalog` or `docker logs mlflow-server`. Common gotchas:
- **UC image tag drift.** The `unitycatalog/unitycatalog:latest` tag and `bin/start-uc-server` entrypoint should be verified against the current upstream release before assuming they're correct. UC OSS packaging is still evolving; if the image fails to start, check the project's release notes for the current invocation.
- **Memory pressure.** If running on `e2-medium`, the sidecars will compete with the agent for RAM. Bump to `e2-standard-2` first.

### Verify

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' \
  | grep -E 'unity-catalog|mlflow-server'
```
Expected: both `Up`, both bound to `127.0.0.1`.

```bash
docker network inspect openclaw-net --format '{{range .Containers}}{{.Name}} {{end}}'
```
Expected: `unity-catalog mlflow-server` (the `openclaw` container will join this network in Phase 2).

---

## Step 3 — Bootstrap the `research` catalog

On the VM:
```bash
python3 ~/openclaw/uc_init.py
```

Expected:
```
Phase 1 bootstrap → http://localhost:8080
✓ Unity Catalog responding
[catalog] created research
[schema] created research.bronze
[schema] created research.silver
[schema] created research.gold
[schema] created research.public_archive
[schema] created research.audit

=== Verification ===
✓ catalog 'research' present
✓ 5 schemas present: ['audit', 'bronze', 'gold', 'public_archive', 'silver']

Phase 1 init complete. Tables will be created by the worker on first write.
```

Re-running is safe — every step is idempotent.

---

## Step 4 — Optional: open the MLflow UI via SSH port forward

```bash
# From your Mac, alongside the existing oc-tunnel on 18789:
gcloud compute ssh openclaw-secure-node \
  --tunnel-through-iap --zone=us-east4-a --project=orphansinthedesert \
  -- -L 5000:localhost:5000 -N
# Then visit: http://localhost:5000
```

The MLflow UI will be empty until Phase 3 starts logging runs. This is just to confirm the server renders.

A future `oc-mlflow` alias in `local_hardening.sh` is appropriate, but defer until Phase 3 actually has runs to view.

---

## Step 5 — Capture the operational state

Before signing off Phase 1:

```bash
# Capture the post-init catalog listing for the record
docker exec unity-catalog \
  curl -s http://localhost:8080/api/2.1/unity-catalog/catalogs | python3 -m json.tool

# Snapshot the persistent disk now that UC + MLflow data exists on it
oc-snapshot
```

---

## What Phase 1 deliberately does NOT do

- ❌ Create tables. The DDL is in `unity_catalog_setup.sql` for review; Phase 2's worker creates tables on first write via delta-rs.
- ❌ Touch the running `openclaw` container. It keeps running on its existing network. Phase 2 adds it to `openclaw-net` and restarts it with new env vars.
- ❌ Initialize the integrity chain genesis block. That is Phase 2.5a — must happen before any other writes but after the worker exists.
- ❌ Set up Delta Sharing or the public surfaces. That is Phase 6.

---

## Rollback (if you need to undo Phase 1)

```bash
docker stop unity-catalog mlflow-server
docker rm unity-catalog mlflow-server
docker network rm openclaw-net
# UC and MLflow data on the persistent disk is preserved at:
#   /mnt/disks/research/unity_catalog/
#   /mnt/disks/research/mlflow/
#   /mnt/disks/research/delta/
# Delete those directories only if you want a clean slate; otherwise leave them
# in place — sidecars.sh will pick them back up on the next run.
```

---

## Cost impact

| Item | Before Phase 1 | After Phase 1 | Delta |
|---|---|---|---|
| VM compute (8h/day, weekdays) | $8.09/mo (e2-medium) | $16.18/mo (e2-standard-2) | +$8.09 |
| Persistent disk (50 GB pd-balanced, 24/7) | $5.00/mo | $5.00/mo | — |
| Cloud NAT + egress | ~$1.73/mo | ~$1.73/mo | — |
| **Total target** | **~$14.82/mo** | **~$22.91/mo** | **+$8.09/mo** |

The VM bump is the only cost change in Phase 1. Sidecars themselves are zero marginal cost (they run in the same VM). Adjust the budget alert in GCP from $20 to $30 before proceeding.

---

## Sign-off

- [ ] sidecars.sh runs cleanly and both health checks pass
- [ ] uc_init.py reports catalog + 5 schemas present
- [ ] MLflow UI loads via SSH port forward
- [ ] OpenClaw container still responsive (Telegram ping returns)
- [ ] Disk snapshot taken
- [ ] Budget alert raised to $30/month

When all six are checked, Phase 1 is complete. Open a Phase 2 branch (`databricks_worker`) for the worker code.
