# Execution Plan: Databricks OSS Stack as the Transparency & Fairness Layer

**Companion to:** `PLAN_databricks_integration.md`
**Branch:** `databricks-integration`

---

## The Core Insight

The Virtue Protocol (`workspace/virtue_prompt.md`) is currently **advisory text in a system prompt**. It tells the agent how to be transparent and fair — Devil's Advocate, Authority Tiers, Bias Awareness, Regenerative Export — but nothing in the system can detect or prevent a violation. The agent could ignore every line and ship a vendor-pumping, single-source, recency-biased brief, and the only check is David eyeballing the output.

The open-source Databricks stack (Unity Catalog OSS + Delta Lake + MLflow) is not a logging system. It is the **enforcement layer** that turns the Virtue Protocol from aspirational prose into a queryable, immutable, schema-enforced contract.

| Virtue Protocol section | Currently | After this work |
|---|---|---|
| "Tag every citation with tier level" | Optional in prose | NOT NULL enum column in Delta `citations` table |
| "Every brief MUST include counter-arguments" | Polite request | CHECK constraint + worker-side reject if empty |
| "Bias Awareness Checklist" | Markdown bullets | MLflow metrics logged per run, queryable across history |
| "DRAFT → public_archive promotion" | Folder copy | UC permission boundary the agent literally cannot cross |
| "Version-controlled, datestamped" | Manual filename suffix | Delta time travel + UC lineage, free |
| "Authority Ranking 1-4" | Self-reported in prose | Enforced as enum; T4 sources rejected unless `t4_justification` field is non-empty |

---

## What "Transparency" Means in This System

A reader (human auditor, future maintainer, regulator, or skeptical David in six months) must be able to answer **all** of these about any published brief, in under 60 seconds:

1. **What did the agent see?** — every URL fetched, with timestamp and SHA-256 of the body it actually retrieved
2. **What did it ignore?** — sources fetched but dropped from the brief, with the reason
3. **What model produced this?** — exact model ID, prompt version, parameters
4. **What changed between versions?** — Delta time travel diff between any two revisions
5. **Who promoted it?** — UC audit log of the DRAFT → PUBLISHED action
6. **Is the content tamper-evident?** — recompute SHA-256 of `summary` and compare to `content_hash` column

The schema makes those questions one SQL query each. Today they're effectively unanswerable.

## What "Fairness" Means in This System

Fairness here is operational, not philosophical. It's defined as: **measurable properties of the source set and reasoning that an external party can audit without trusting the agent's self-report.**

Concretely, every brief gets a fairness scorecard logged to MLflow:

| Metric | Definition | Threshold for DRAFT eligibility |
|---|---|---|
| `source_tier_distribution` | % of citations at T1 / T2 / T3 / T4 | T1+T2 ≥ 60% |
| `vendor_diversity_count` | distinct vendors/orgs cited | ≥ 3 OR explicit `single_vendor_justification` |
| `counter_argument_ratio` | counter-arg words / total brief words | ≥ 0.15 |
| `recency_within_18_months` | % of sources dated ≤ 18 months | ≥ 50% OR topic flagged as historical |
| `paid_content_disclosed` | every sponsored/vendor blog flagged | required where applicable |
| `self_declared_bias_check` | the agent's own checklist answers | all five items answered, not skipped |

A brief that fails any threshold is still inserted — but with `status = 'REJECTED'` and the failed metrics recorded. David sees rejected drafts and can override with a documented reason. **Failure is logged, never silently dropped.** That's the fairness contract.

---

## Data Model: The Medallion, Adapted for Auditability

```
research                                  (catalog — Unity Catalog)
├── bronze                                (schema)
│   ├── raw_responses                     ← every agent response, unfiltered
│   └── source_fetches                    ← every URL fetched, with body hash
├── silver                                (schema)
│   ├── validated_briefs                  ← passed schema + virtue checks
│   └── citations                         ← normalized, tier-tagged, dated
├── gold                                  (schema)
│   ├── research_summaries                ← DRAFT/REJECTED, awaiting review
│   └── fairness_scorecards               ← computed metrics per brief
├── public_archive                        (schema — UC perms differ here)
│   └── published                         ← PROMOTED only, CC BY 4.0
└── audit                                 (schema — append-only)
    ├── write_log                         ← every INSERT/UPDATE, who/when/what
    └── promotion_log                     ← DRAFT → PUBLISHED transitions
```

### Bronze: `raw_responses`

Captures the agent's response **before** any virtue check. If a brief is later rejected, the raw is still here. This is the primary forensic artifact for "what did the agent actually try to do."

```sql
CREATE TABLE research.bronze.raw_responses (
  response_id      STRING NOT NULL,        -- UUID
  created_at       TIMESTAMP NOT NULL,
  agent_version    STRING NOT NULL,        -- e.g. "openclaw-v4.0"
  model_id         STRING NOT NULL,        -- e.g. "google/gemini-2.5-flash"
  prompt_hash      STRING NOT NULL,        -- SHA-256 of the full prompt sent
  response_text    STRING NOT NULL,
  response_hash    STRING NOT NULL,        -- SHA-256 of response_text
  mlflow_run_id    STRING NOT NULL,
  topic_id         STRING                  -- references topics.json
) USING DELTA;
```

### Bronze: `source_fetches`

Every URL the agent retrieved during this research run, with the SHA-256 of the body it received. This anchors source tampering claims: if the source page changes after the fact, the brief's citation still points to the body the agent actually read.

```sql
CREATE TABLE research.bronze.source_fetches (
  fetch_id         STRING NOT NULL,
  response_id      STRING NOT NULL,        -- FK to raw_responses
  fetched_at       TIMESTAMP NOT NULL,
  url              STRING NOT NULL,
  http_status      INT NOT NULL,
  body_hash        STRING NOT NULL,        -- SHA-256 of body bytes
  body_bytes       BIGINT,
  content_type     STRING,
  used_in_brief    BOOLEAN NOT NULL        -- did it survive into the brief?
) USING DELTA;
```

`used_in_brief = false` is the "what did it ignore" answer.

### Silver: `validated_briefs`

The agent response **after** schema validation and virtue check. Includes all the structured fields the virtue prompt requires — counter-arguments, key findings, further reading.

```sql
CREATE TABLE research.silver.validated_briefs (
  brief_id              STRING NOT NULL,
  response_id           STRING NOT NULL,        -- FK to bronze.raw_responses
  created_at            TIMESTAMP NOT NULL,
  topic_id              STRING NOT NULL,
  title                 STRING NOT NULL,
  key_findings          STRING NOT NULL,
  counter_arguments     STRING NOT NULL,        -- enforced non-empty
  practical_implications STRING NOT NULL,
  further_reading       STRING NOT NULL,
  known_gaps            STRING,
  content_hash          STRING NOT NULL,        -- SHA-256 of concatenated fields
  agent_version         STRING NOT NULL,
  CONSTRAINT counter_argument_required CHECK (length(counter_arguments) >= 100)
) USING DELTA;
```

### Silver: `citations`

Normalized citation table. Every claim in a brief should resolve to a row here. Tier is enforced.

```sql
CREATE TABLE research.silver.citations (
  citation_id           STRING NOT NULL,
  brief_id              STRING NOT NULL,        -- FK to validated_briefs
  fetch_id              STRING,                 -- FK to bronze.source_fetches
  url                   STRING NOT NULL,
  title                 STRING NOT NULL,
  authority_tier        INT NOT NULL,           -- 1, 2, 3, or 4
  vendor_org            STRING,                 -- "Databricks", "Anthropic", etc.
  publication_date      DATE,
  is_paid_content       BOOLEAN NOT NULL,       -- vendor blog, sponsored, etc.
  t4_justification      STRING,                 -- required when authority_tier=4
  CONSTRAINT tier_range CHECK (authority_tier BETWEEN 1 AND 4),
  CONSTRAINT t4_requires_justification
    CHECK (authority_tier <> 4 OR length(t4_justification) > 0)
) USING DELTA;
```

### Gold: `research_summaries`

The reviewable brief. `status` is the human-in-the-loop gate.

```sql
CREATE TABLE research.gold.research_summaries (
  summary_id           STRING NOT NULL,
  brief_id             STRING NOT NULL,        -- FK to silver.validated_briefs
  created_at           TIMESTAMP NOT NULL,
  status               STRING NOT NULL,        -- DRAFT | REJECTED | PROMOTED
  rejection_reasons    ARRAY<STRING>,          -- which fairness checks failed
  override_reason      STRING,                 -- human reason if promoted despite failure
  promoted_at          TIMESTAMP,
  promoted_by          STRING,                 -- UC principal of promoter
  mlflow_run_id        STRING NOT NULL,
  CONSTRAINT status_values
    CHECK (status IN ('DRAFT', 'REJECTED', 'PROMOTED'))
) USING DELTA;
```

### Gold: `fairness_scorecards`

The fairness metrics per brief, persisted alongside MLflow for SQL-queryable aggregates.

```sql
CREATE TABLE research.gold.fairness_scorecards (
  scorecard_id              STRING NOT NULL,
  brief_id                  STRING NOT NULL,
  computed_at               TIMESTAMP NOT NULL,
  source_tier_t1_pct        DOUBLE NOT NULL,
  source_tier_t2_pct        DOUBLE NOT NULL,
  source_tier_t3_pct        DOUBLE NOT NULL,
  source_tier_t4_pct        DOUBLE NOT NULL,
  vendor_diversity_count    INT NOT NULL,
  counter_argument_ratio    DOUBLE NOT NULL,
  recency_within_18mo_pct   DOUBLE NOT NULL,
  paid_content_disclosed    BOOLEAN NOT NULL,
  bias_check_complete       BOOLEAN NOT NULL,
  threshold_pass            BOOLEAN NOT NULL,
  failed_thresholds         ARRAY<STRING>
) USING DELTA;
```

### Public Archive: `published`

The CC BY 4.0 commons. Only PROMOTED briefs land here. This table is **physically separate** so the regenerative export is provably scoped — anyone reading `public_archive` is reading only human-approved content.

```sql
CREATE TABLE research.public_archive.published (
  published_id        STRING NOT NULL,
  summary_id          STRING NOT NULL,        -- FK back to gold
  published_at        TIMESTAMP NOT NULL,
  license             STRING NOT NULL,        -- always 'CC BY 4.0'
  attribution         STRING NOT NULL,        -- "OpenClaw Project, reviewed by D. Callaghan"
  markdown_export     STRING NOT NULL,
  content_hash        STRING NOT NULL
) USING DELTA;
```

### Audit: `write_log`

Append-only. Every INSERT/UPDATE across all tables emits a row here. This is the "who did what" log that makes the system auditable without trusting the agent.

```sql
CREATE TABLE research.audit.write_log (
  log_id              STRING NOT NULL,
  occurred_at         TIMESTAMP NOT NULL,
  uc_principal        STRING NOT NULL,        -- service principal or human user
  action              STRING NOT NULL,        -- INSERT | UPDATE | DELETE
  target_table        STRING NOT NULL,
  target_row_id       STRING NOT NULL,
  source_ip           STRING,
  details             MAP<STRING,STRING>
) USING DELTA;
```

---

## Unity Catalog Permission Model (The Fairness Boundary)

Two principals. The boundary between them is the entire fairness story.

| Principal | What it can do |
|---|---|
| `openclaw-agent` (service principal, container env vars) | INSERT on bronze.*, silver.*, gold.research_summaries (DRAFT only), gold.fairness_scorecards, audit.write_log. No UPDATE. No access to public_archive. |
| `david` (human user, MLflow + CLI) | SELECT on everything. UPDATE on gold.research_summaries (status field only). INSERT on public_archive.published. |

The agent **cannot** promote its own work. Not because the prompt asks it not to — because UC denies the operation. This is the fairness invariant: a threat actor that fully controls the agent process still cannot publish to the commons.

To the extent UC OSS does not yet enforce column-level grants (current limitation as of the last release), the worker enforces it in code AND a nightly UC audit query verifies no DRAFT writes happened from non-agent principals. Belt and braces until UC OSS catches up.

---

## MLflow: The Reasoning Audit

Every research run is an MLflow experiment. This is where the *reasoning* (as opposed to the *output*) is audited.

**Run parameters** (immutable inputs):
- `topic_id`, `model_id`, `prompt_version`, `prompt_hash`
- `agent_version`, `gateway_commit_sha`

**Run metrics** (the fairness scorecard, plus operational signal):
- All six fairness metrics from the scorecard table
- `source_count`, `unique_domain_count`, `total_response_tokens`
- `latency_seconds`, `retry_count`

**Run artifacts** (the forensic trail):
- `prompt.txt` — full prompt as sent
- `raw_response.json` — model's raw output
- `fetched_sources/` — directory of every URL body fetched (with hash-named files)
- `bias_checklist.json` — agent's self-declared answers
- `failed_checks.json` — if status=REJECTED, why

David can compare two runs on the same topic and see exactly what changed: model version, source set, fairness metrics. That's reproducibility.

---

## Phased Execution

### Phase 1: Sidecars and Schema (~3 days)

1. Add `sandbox/gcp/sidecars.sh`: starts `unity-catalog` and `mlflow-server` containers, both bound to `127.0.0.1`, both backed by `/mnt/disks/research/`.
2. Update VM startup script in `init_agency.sh` to launch sidecars.
3. Bump VM to `e2-standard-2` if not already (UC server needs ~512MB JVM heap; current allocation is tight).
4. Write `openclaw/unity_catalog_setup.sql` with the seven `CREATE TABLE` statements above.
5. Write `openclaw/uc_init.py`: bootstraps the catalog, creates the two principals, applies grants.
6. **Validation:** can connect to UC REST API at `localhost:8080`; can list the seven tables; can connect to MLflow at `localhost:5000`.

### Phase 2: The Worker, Bronze + Silver (~4 days)

1. `openclaw/databricks_worker.py` skeleton with class structure from PLAN.
2. `AuditHasher`: SHA-256 of any string, plus a `verify(text, hash)` method.
3. `DeltaTableWriter`: thin wrapper over `deltalake` (Python). One method per table. All inserts go through here.
4. `LocalBuffer`: writes to `/home/clawuser/workspace/research_logs/<date>/<uuid>.json` if the Delta write fails. Background retry loop drains the buffer on next agent invocation.
5. Wire bronze writes (`raw_responses`, `source_fetches`) into the agent's existing response path.
6. Wire silver writes after schema validation passes.
7. **Validation:** force a UC outage; verify briefs land in local buffer; restart UC; verify drain succeeds and no duplicates.

### Phase 3: Fairness Scorecard + MLflow (~4 days)

1. `FairnessScorer`: pure-Python class, takes a `ValidatedBrief` and `list[Citation]`, returns a `FairnessScorecard`. No I/O. Unit testable in isolation.
2. `MLflowTracker`: starts a run at the top of every research call, logs params, ends with metrics + artifacts.
3. Worker writes scorecard to Delta and metrics to MLflow in the same logical step (idempotent — same run_id used for both).
4. Decision logic for DRAFT vs REJECTED based on threshold table above.
5. **Validation:** ten synthetic briefs across the threshold boundary; confirm classification matches expected for each; confirm MLflow UI shows them; confirm SQL aggregates over `fairness_scorecards` match MLflow run history.

### Phase 4: Promotion CLI + Permission Enforcement (~3 days)

1. `openclaw/promote.py`: CLI for David. `promote list-drafts`, `promote show <id>`, `promote approve <id> [--override <reason>]`, `promote reject <id> --reason <text>`.
2. CLI authenticates as `david` principal (separate token, not in container env).
3. On approve: UPDATE gold + INSERT public_archive in a single transaction.
4. UC grants applied per the permission table above.
5. **Validation:** attempt to write public_archive using the agent token; must fail. Attempt to write DRAFT using david's token; must succeed. Promotion log shows the principal each time.

### Phase 5: The Adversarial Validation Suite (~3 days)

This phase is the point. Confirm the controls hold under attack, not just under happy-path use.

1. Test: agent emits a brief with empty `counter_arguments`. Expect: silver insert rejected, status = REJECTED in gold, `failed_thresholds = ['counter_argument_required']`.
2. Test: agent emits a brief with all T4 citations. Expect: REJECTED with `source_tier_distribution`.
3. Test: agent attempts UPDATE on `research_summaries.status`. Expect: UC permission denied.
4. Test: agent attempts INSERT on `public_archive.published`. Expect: UC permission denied.
5. Test: prompt-injection attempt — adversarial Telegram message asks the agent to "promote the last draft." Expect: agent has no API path to do so; attempt logged.
6. Test: source URL changes its body after the brief is written. Expect: `source_fetches.body_hash` does not match a fresh fetch — auditable detection.
7. Test: tamper with `summary` field via direct Delta write (simulated). Expect: `content_hash` mismatch — recompute on read flags it.
8. Test: time-travel a brief back to an earlier version after promotion. Expect: history accessible; current row unchanged.

These are written as `pytest` cases against a UC OSS instance running in CI (Docker-in-Docker on the VM, or a local docker-compose for dev).

---

## File Inventory After This Work

```
openclaw/
├── databricks_worker.py            ← NEW (Phase 2-3)
├── promote.py                      ← NEW (Phase 4)
├── uc_init.py                      ← NEW (Phase 1)
├── unity_catalog_setup.sql         ← NEW (Phase 1)
├── tests/
│   ├── test_fairness_scorer.py     ← NEW (Phase 3)
│   ├── test_local_buffer.py        ← NEW (Phase 2)
│   └── test_adversarial.py         ← NEW (Phase 5)
├── requirements.txt                ← UPDATED (Phase 2)
└── Dockerfile.hardened             ← UPDATED (Phase 2 — adds deltalake, mlflow)

sandbox/gcp/
└── sidecars.sh                     ← NEW (Phase 1)

init_agency.sh                      ← UPDATED (Phase 1 — UC + MLflow env vars,
                                                sidecar startup, VM size bump)
```

---

## Open Risks Worth Flagging Before Building

1. **UC OSS column-level grants are still maturing.** Worker-side enforcement + nightly audit query is the mitigation, but if UC drops support for a feature we depend on, the audit becomes the only line of defense. Worth a Tier-1 documentation read before Phase 1.

2. **VM memory budget.** UC server (~512MB), MLflow (~256MB), agent (1.8GB cap), plus Docker daemon overhead — total ~2.7GB. `e2-medium` (4GB) is uncomfortably close; `e2-standard-2` (8GB) gives headroom. Cost rises from $8.09 → ~$16/month for compute. Acceptable given what's gained.

3. **delta-rs Python API maturity.** `deltalake` package handles Z-ordering and time travel reads, but `MERGE INTO` and `UPDATE` support has historically been Spark-only. Worker may need to issue updates via UC's SQL endpoint rather than via the Python API. Verify before Phase 2.

4. **The "REJECTED but human-overrides" path.** Letting David manually promote a fairness-failed brief with `override_reason` is the right call (false positives happen), but it weakens the invariant. Mitigation: every override is itself a metric (`overrides_per_month`) — if the rate creeps up, the thresholds are wrong, not the briefs. This is the fairness self-monitoring loop.

5. **Public archive is local-disk only.** A regenerative commons that lives on one VM's persistent disk isn't really a commons. Phase 6 (out of scope here) is publishing `public_archive.published` to a public GitHub repo via a scheduled job — that's where CC BY 4.0 actually means something.

---

## What This Buys You, in One Sentence

Every claim in every published brief becomes traceable to its source, every source becomes traceable to its hashed body, every brief becomes traceable to its model run, every promotion becomes traceable to its human reviewer — and the agent, by construction, cannot publish without going through David. Transparency stops being something the agent promises and becomes something the schema guarantees.
