# Execution Plan: Public Commons Sharing — Phase 6

**Companion to:** `EXECUTION_databricks_integration.md`
**Branch:** `databricks-integration`

---

## The Fairness Argument This Phase Operationalizes

The Virtue Protocol's "Regenerative Export" principle says outputs should be "commons-compatible" under CC BY 4.0. Phase 4 (the promotion CLI) gets briefs into a `public_archive.published` table. But that table sits on one VM's local disk — it isn't actually public, just human-approved.

This phase makes the auditability itself public. The argument is symmetric:

- **The agent consumes commons content** — every cited URL, every fact, every model that was trained on commons-licensed text
- **The fair contribution back is proportional to that consumption** — measured, not claimed
- **The auditability must be public** so the proportionality claim is verifiable by anyone, not just by trusting our self-report

A private audit trail proves nothing externally. A public audit trail is the contribution.

---

## Why Delta Sharing Fits the Structured Side

Delta Sharing (the open protocol, `delta-io/delta-sharing`, originally from Databricks but vendor-neutral) gives exactly the right semantics:

| Property | Why it matters here |
|---|---|
| Open REST protocol | Recipients use any tool — `delta-sharing` Python client, Spark, pandas, DuckDB, BI dashboards — no Databricks account required |
| Reads against your storage | Data stays on the GCP VM's persistent disk; recipients just get read access via the share server |
| Open-mode shares | Sharing without bearer tokens — pure public consumption, no recipient management |
| Token-mode shares | If we ever want a private/preview tier, we can issue bearer tokens with expiry |
| Schema-aware | Recipients see the same column names, types, partitioning we use internally — no translation layer |
| Time-travel preserved | Recipients can pin a query to a specific Delta version — provably stable for citation |
| No data duplication | Same Delta tables we already wrote in Phase 2-4 — sharing is metadata-level, not a copy |

The reference implementation is a small Java/Scala server (`io.delta:delta-sharing-server`) — it runs as another sidecar container alongside Unity Catalog and MLflow. About 300MB RAM, exposes a REST endpoint.

### What Delta Sharing is *not* the right tool for

- **Human-readable consumption.** Casual readers (journalists, students, curious peers) shouldn't need a Python client to read a brief. GitHub or a static site is the right surface.
- **Long-term durability.** A share endpoint backed by one VM is not durable. The actual commons archive needs to live somewhere that survives this project being shut down — GitHub, IPFS, archive.org. Delta Sharing is a live query interface, not a permanent record.

So the answer is **both**: Delta Sharing for the live structured audit, GitHub-published markdown for the durable human-readable archive. They serve different audiences and they back each other up.

---

## What Gets Shared, Concretely

Five views exposed as a Delta Share named `openclaw_public_commons`:

### 1. `published_briefs`

A view over `research.public_archive.published` — only PROMOTED entries, only the public-safe columns.

```sql
CREATE VIEW research.shared.published_briefs AS
SELECT
  published_id,
  published_at,
  license,                      -- always 'CC BY 4.0'
  attribution,
  markdown_export,
  content_hash                  -- recipients can verify integrity
FROM research.public_archive.published;
```

### 2. `published_fairness_scorecards`

The fairness metrics for the published briefs. This is the "proof of process" — anyone can aggregate and check our self-reported quality bar.

```sql
CREATE VIEW research.shared.published_fairness_scorecards AS
SELECT
  fs.scorecard_id,
  fs.brief_id,
  pa.published_id,              -- joinable to published_briefs
  fs.computed_at,
  fs.source_tier_t1_pct,
  fs.source_tier_t2_pct,
  fs.source_tier_t3_pct,
  fs.source_tier_t4_pct,
  fs.vendor_diversity_count,
  fs.counter_argument_ratio,
  fs.recency_within_18mo_pct,
  fs.paid_content_disclosed,
  fs.threshold_pass,
  fs.failed_thresholds          -- including human-overrides, transparently
FROM research.gold.fairness_scorecards fs
INNER JOIN research.public_archive.published pa
  ON fs.brief_id = pa.summary_id;
```

### 3. `ai_usage_ledger`

The "take" side — tracking AI consumption per published brief, plus a project-level monthly rollup. **This is the proportionality argument's evidence.**

```sql
CREATE TABLE research.shared.ai_usage_ledger (
  ledger_id                 STRING NOT NULL,
  period_start              DATE NOT NULL,
  period_end                DATE NOT NULL,
  total_input_tokens        BIGINT NOT NULL,
  total_output_tokens       BIGINT NOT NULL,
  total_model_calls         BIGINT NOT NULL,
  total_source_fetches      BIGINT NOT NULL,
  estimated_cost_usd        DOUBLE NOT NULL,
  models_used               ARRAY<STRING> NOT NULL,
  briefs_published          BIGINT NOT NULL,
  briefs_drafted            BIGINT NOT NULL,
  briefs_rejected           BIGINT NOT NULL,
  human_review_hours        DOUBLE,           -- self-reported by David, optional
  give_back_ratio           DOUBLE NOT NULL   -- briefs_published / estimated_cost_usd
) USING DELTA;
```

The `give_back_ratio` is the headline metric. It's deliberately not "tokens consumed per brief" — that rewards verbosity. It's "published briefs per dollar of AI spend" — it rewards getting useful content out the door per unit of commons consumed. Inverted version (`tokens_per_published_brief`) is also fine; pick the framing that's hardest to game.

The honest version of this metric exposes the failure modes too: `briefs_rejected` and `briefs_drafted` (drafts that never got promoted) are both AI consumption that didn't produce public value. They get counted in the denominator anyway. That's the whole point — the ratio is meaningful precisely because it includes the misses.

### 4. `published_run_summaries`

Per-brief MLflow run details — the "how this specific brief was produced" view. Strengthens the reproducibility claim by linking each published brief to the exact model run that generated it.

This requires a small extension to Phase 3: in addition to logging metrics to MLflow, the worker writes a parallel row to `research.gold.run_summaries` (Delta) so the data is queryable via the share endpoint, not only via the MLflow UI.

```sql
-- Underlying gold table (added to Phase 3 scope)
CREATE TABLE research.gold.run_summaries (
  run_summary_id        STRING NOT NULL,
  mlflow_run_id         STRING NOT NULL,
  brief_id              STRING NOT NULL,        -- FK to silver.validated_briefs
  started_at            TIMESTAMP NOT NULL,
  ended_at              TIMESTAMP NOT NULL,
  model_id              STRING NOT NULL,        -- e.g. "google/gemini-2.5-flash"
  prompt_version        STRING NOT NULL,
  agent_version         STRING NOT NULL,
  input_tokens          BIGINT NOT NULL,
  output_tokens         BIGINT NOT NULL,
  total_cost_usd        DOUBLE NOT NULL,
  source_count          INT NOT NULL,
  unique_domain_count   INT NOT NULL,
  latency_seconds       DOUBLE NOT NULL,
  retry_count           INT NOT NULL
) USING DELTA;

-- Public-facing view: only runs that produced a published brief
CREATE VIEW research.shared.published_run_summaries AS
SELECT rs.*
FROM research.gold.run_summaries rs
INNER JOIN research.public_archive.published pa
  ON rs.brief_id = pa.summary_id;
```

### 5. `retractions`

Mirrors retractions from the GitHub repo (see "Retractions" section below) so recipients querying via Delta Sharing see the same retraction status that GitHub readers see.

```sql
CREATE TABLE research.shared.retractions (
  retraction_id         STRING NOT NULL,
  published_id          STRING NOT NULL,        -- which brief is retracted
  retracted_at          TIMESTAMP NOT NULL,     -- git commit timestamp
  retraction_reason     STRING NOT NULL,        -- from commit message body
  retracted_by          STRING NOT NULL,        -- git author
  superseded_by         STRING,                 -- optional: replacement published_id
  git_commit_sha        STRING NOT NULL         -- back-pointer to the commit
) USING DELTA;
```

---

## Where the AI Usage Numbers Come From

These are already in the system after Phase 3:

- **Token counts**: Gemini API responses include `usage.prompt_tokens` and `usage.completion_tokens` per call. Captured in MLflow as run metrics. The agent already logs them to MLflow per the Phase 3 plan.
- **Model calls**: count of MLflow runs in the period.
- **Source fetches**: count of `bronze.source_fetches` rows in the period.
- **Cost estimate**: token counts × Google's published per-token pricing for `gemini-2.5-flash` (currently $0.075/1M input, $0.30/1M output as of 2026 pricing — needs a `model_pricing.json` config file we update when prices change).
- **Brief counts**: SQL aggregate over `gold.research_summaries` grouped by status.

The `ai_usage_ledger` is populated by a scheduled job (`openclaw/ledger_rollup.py`) that runs nightly, computes the daily delta, and appends a row. Monthly and lifetime views are SQL aggregates over the daily rows. Append-only — corrections require an explicit `ledger_correction` row, never an UPDATE.

---

## The Public Surfaces

Three surfaces, each tuned to a different audience.

### Surface 1: Delta Sharing endpoint (machine-readable)

For analysts, researchers, and other agents who want to query the data programmatically.

```
URL: https://share.openclaw.example/delta-sharing/
Profile: openclaw_public_commons
Tables: published_briefs, published_fairness_scorecards, ai_usage_ledger
Auth: open mode (no token required)
```

Recipient usage example:

```python
import delta_sharing

profile = "https://share.openclaw.example/delta-sharing/openclaw_public_commons.share"
df = delta_sharing.load_as_pandas(f"{profile}#openclaw.shared.ai_usage_ledger")

# Anyone can compute their own give-back ratio
df.groupby("period_start")[["briefs_published", "estimated_cost_usd"]].sum()
```

### Surface 2: Public GitHub repo (human-readable + retraction mechanism)

For readers who just want the briefs — and the channel through which David retracts entries (see "Retractions" below).

```
Repo: github.com/davecallaghan/openclaw-public-commons
Contents:
  briefs/<YYYY-MM>/<topic>-<published_id>.md          # one file per published brief
  scorecards/<YYYY-MM>/<topic>-<published_id>.json    # corresponding fairness scorecard
  runs/<YYYY-MM>/<topic>-<published_id>.json          # MLflow run summary for that brief
  ledger/<YYYY-MM>.md                                 # monthly AI usage rollup, human-formatted
  README.md                                            # explains the project, license, share endpoint
  ATTRIBUTION.md                                       # how to cite
```

Populated by a scheduled job (`openclaw/github_publisher.py`) that runs nightly and is **bidirectional**:

- **Push:** queries the share views for new PROMOTED briefs, exports as markdown, commits and pushes. The same `content_hash` from the Delta row is included in the markdown frontmatter so a reader can cross-check the markdown against the share endpoint and confirm they got the same content.
- **Pull:** scans recent commits on the public repo for the `retraction:` prefix pattern (see below), parses the structured fields, inserts rows into `research.shared.retractions` so the Delta endpoint reflects the same retraction state.

This is the **durable** copy — if the GCP VM disappears tomorrow, the published commons survives in GitHub, and so does the retraction history.

### Surface 3: Status page (the proportionality story, at a glance)

A static HTML page (Cloud Run or GitHub Pages) that pulls from the share endpoint and renders:

- Total briefs published, drafted, rejected (lifetime)
- Total AI cost consumed, lifetime and current month
- Give-back ratio over time (chart)
- Source tier distribution across all published briefs (chart)
- Last 10 published briefs (links to GitHub markdown)

Refreshes on a cron. Public, no auth. The point is to make the take/give story visible at a glance to anyone who wants to spot-check the project's claims about itself.

---

## Retractions — Git as the Mechanism, Delta as the Mirror

A retraction is fundamentally a human judgment ("we got this wrong"). It shouldn't require a schema migration or a CLI dance. The simplest mechanism that fits the architecture: **a retraction is a git commit with a structured pattern, and the publisher mirrors it back into Delta.**

### Mechanics

David retracts via a single commit on the public GitHub repo. The commit:

1. Modifies the published markdown file by inserting a retraction header at the top
2. Uses a structured commit message: `retraction: <published_id> — <one-line reason>` followed by a body with details
3. Optionally links to a superseding brief

Example commit message:
```
retraction: pub-3a7c — pricing claim was outdated by one quarter

The brief at briefs/2026-05/databricks-pricing-3a7c.md cited e2-medium at
$0.033/hour, which was the 2026-Q1 figure. Correct as of 2026-Q2: $0.034/hour.
A revised brief is published at pub-7e2f.

superseded-by: pub-7e2f
```

Modified markdown file gets a header that preserves the original content:
```markdown
> **RETRACTED 2026-05-15** — pricing claim was outdated. See pub-7e2f.
> Original content preserved below for audit purposes.

# [original brief content unchanged]
```

### Why this works

- **No schema dance for David.** Retracting is one commit. He's a software engineer; git is the tool he already uses.
- **Git history is the audit trail.** The diff shows what was added (the retraction header); the commit message has the structured reason; the author is logged with timestamp.
- **Original content preserved.** The retraction adds a header but doesn't delete the body. Readers can still see what was originally claimed and why it was wrong.
- **Public from day one.** The retraction is visible to anyone reading the GitHub repo before it ever syncs back to Delta.
- **Reuses an existing channel.** The publisher already has GitHub write access; pulling commits back is just a `git log --grep="^retraction:"` parser.

### The Delta mirror

The bidirectional publisher's pull pass parses retraction commits and writes to `research.shared.retractions`:

```python
# Pseudocode for the parser
for commit in repo.iter_commits(since=last_sync_time):
    if commit.message.startswith("retraction:"):
        published_id = parse_published_id(commit.message)
        reason = parse_reason(commit.message)
        superseded_by = parse_superseded_by(commit.message)  # optional
        insert_retraction_row(
            published_id=published_id,
            retracted_at=commit.committed_datetime,
            retraction_reason=reason,
            retracted_by=commit.author.email,
            superseded_by=superseded_by,
            git_commit_sha=commit.hexsha,
        )
```

Recipients querying via Delta Sharing see retractions in the structured table; recipients reading GitHub see the retraction headers in the markdown. Both are authoritative for their respective audiences. They will briefly disagree (within the daily sync window) — document this expectation.

### What this doesn't try to solve

- **It doesn't undo the original publication.** If the original brief was already mirrored downstream by a recipient who then republished it, the retraction can't reach that copy. That's a property of CC BY 4.0 — downstream copies are out of our control. The best we can do is publish the retraction loudly and timestamp it.
- **It doesn't prevent a malicious retraction.** A compromised git PAT could mass-retract everything. Mitigation: branch protection on the public repo, GitHub email notifications on every commit so unauthorized retractions are noticed quickly. The integrity chain (see `EXECUTION_integrity_engine.md`) gives independent detection: a forged retraction on git doesn't alter the integrity chain on the VM, so a chain re-verification spots the divergence.

---

## Permission Model Update

The Phase 4 permission table extends:

| Principal | What it can do |
|---|---|
| `openclaw-agent` (service principal) | Same as Phase 4. No access to `research.shared.*`. |
| `david` (human) | Same as Phase 4. INSERT into `ai_usage_ledger` (manual corrections only). |
| `delta-share-server` (service principal) | SELECT-only on the three views in `research.shared.*`. No access to anything else. |
| **Public (anonymous)** | Read via Delta Sharing protocol on the three shared views only. |

The share server runs with its own UC service principal. It cannot see `bronze`, `silver`, `gold`, or `audit` directly — only the three curated views in `research.shared.*`. So the share endpoint cannot accidentally leak rejected drafts, raw sources, or audit trails of internal operations.

---

## Phase 6 Build Steps

1. **Add `delta-sharing-server` sidecar** in `sandbox/gcp/sidecars.sh`. Reference image: `deltaio/delta-sharing-server:latest` (verify before building — like UC OSS, packaging is still maturing). Bind to `127.0.0.1:8085` initially; expose externally only after Phase 6.4.
2. **Define share config** at `openclaw/share_config.yaml` listing the catalog/schema/views to expose, in the format the Delta Sharing server expects.
3. **Create the three views** (`published_briefs`, `published_fairness_scorecards`, `ai_usage_ledger`) in UC under a new `research.shared` schema. Apply SELECT-only grant to `delta-share-server` principal.
4. **Build the AI usage ledger rollup** — `openclaw/ledger_rollup.py`, scheduled via cron at 02:00 UTC daily. Reads MLflow run metrics + Delta tables for the prior 24h, appends one row.
5. **Expose the share endpoint publicly.** Add a new GCP firewall rule `allow-delta-share-public` opening port 443 (with TLS termination — Caddy or Cloud Load Balancer) to the share server. **This is the only intentional public-facing surface in the entire architecture** — it deserves explicit threat modeling, see below.
6. **GitHub publisher** — `openclaw/github_publisher.py`, scheduled daily after the rollup. Pulls new published briefs via the share endpoint (eat our own dog food), formats as markdown, commits and pushes to the public repo. Uses a separate GitHub PAT scoped to the public repo only.
7. **Status page** — static HTML, deployed to Cloud Run or GitHub Pages, fetches from the share endpoint on page load. Charts via a tiny client-side library (e.g. `uPlot`) to keep the page small and dependency-light.
8. **Validation suite extensions:**
   - Recipient with no auth can read the three views via `delta-sharing` Python client. ✓
   - Recipient cannot read any non-shared table (returns 404 or permission denied). ✓
   - Markdown in the GitHub repo has matching `content_hash` to the share endpoint row. ✓
   - Status page numbers reconcile with direct Delta Sharing query. ✓
   - Adversarial: attempt to enumerate all tables via the share endpoint. Expect: only the three shared views visible. ✓

---

## Threat Model for the Public Endpoint

This is the architecture's first intentional public-facing surface. Worth being deliberate.

| Concern | Mitigation |
|---|---|
| Reading data outside the shared views | Share server runs as a UC principal with grants only on the three views; even a bug in the share server can't escalate. |
| Data exfiltration via the share endpoint | The share endpoint already serves "data we want public" — exfiltration is not a meaningful concept here. |
| Cost blow-up from heavy public reads | Cloud NAT egress is metered. Mitigation: put Cloudflare or a similar CDN in front of the share endpoint with response caching; Delta Sharing responses are cacheable by file URL. Set a billing alert at 2× expected egress. |
| Vulnerability in the share server itself | Pin to a specific image digest; subscribe to release announcements; the share server has no read access to anything sensitive even if compromised. |
| Tampering with public outputs | `content_hash` columns in every shared view; recipients can verify; GitHub repo provides a second copy with independent hashing. |
| Doxing via metadata | Audit views are deliberately excluded from sharing. Only the three curated views go out. The `audit.write_log` (which contains internal IPs and principals) stays private. |

Notably absent: any concern about authentication. We're publishing intentionally. The share endpoint being open is the feature, not the bug.

---

## What "Proportionality" Looks Like Once This Is Built

A reader can run this query against the public endpoint and answer the fairness question themselves, without taking our word for anything:

```python
import delta_sharing
profile = "https://share.openclaw.example/openclaw_public_commons.share"

ledger = delta_sharing.load_as_pandas(f"{profile}#openclaw.shared.ai_usage_ledger")
briefs = delta_sharing.load_as_pandas(f"{profile}#openclaw.shared.published_briefs")

# Lifetime: how much commons was consumed, and what came back?
lifetime_cost = ledger["estimated_cost_usd"].sum()
lifetime_published = len(briefs)
lifetime_give_back_ratio = lifetime_published / lifetime_cost  # briefs per dollar

# Are the published briefs holding up the quality bar?
scorecards = delta_sharing.load_as_pandas(f"{profile}#openclaw.shared.published_fairness_scorecards")
mean_t1_t2_share = (scorecards["source_tier_t1_pct"] + scorecards["source_tier_t2_pct"]).mean()
# If this is below 60% across the published corpus, the project is failing its own fairness contract.
```

That query, against an open endpoint, is the answer to "is this project being fair?" The architecture exists to make that query trustworthy.

---

## Open Questions — Resolutions

1. **Access methods (API / MCP / Website / Git / combination).** **Deferred** until Phase 6 has produced something viewable. Decision will be made looking at real outputs, not in advance. The architecture supports all of them — the share endpoint, GitHub repo, and status page can each be wrapped as an MCP server, exposed as a REST API, or left as-is depending on what audience emerges.

2. **Cost ceiling for public egress.** **Resolved.** Egress and overall cost will be aggressively managed via CDN caching, billing alerts, and operational discipline — same approach that's already keeping the project under $15/month. Not blocking.

3. **MLflow run visibility.** **Resolved by including a fourth shared view** (`published_run_summaries`, see schema above). Each published brief is linked to its full run record — model, prompt version, token counts, latency, retry count. This requires a small extension to Phase 3 (the worker writes to `research.gold.run_summaries` in addition to MLflow's native store) so the data is queryable via the share endpoint, not only via the MLflow UI.

4. **Retractions.** **Resolved by the Retractions section above** — git commit is the mechanism, Delta mirror is the byproduct. No schema columns added to `published_briefs`; instead a separate `research.shared.retractions` view recipients can join against. Markdown gets a retraction header preserving original content.

5. **Cadence of GitHub publishes vs. share endpoint.** Share endpoint is live (current to the last successful Delta write); GitHub is the durable daily snapshot. They will briefly disagree within the sync window. Document in the public repo README so recipients know which to trust for which use case (live data → share endpoint; durable record → GitHub).
