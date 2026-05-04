-- =============================================================================
-- OPENCLAW UNITY CATALOG SCHEMA — canonical reference
-- =============================================================================
-- This file is the source-of-truth schema for the OpenClaw research datastore.
--
-- Phase 1 (uc_init.py): creates the catalog and the five schemas below.
-- Phase 2+ (databricks_worker.py): creates the tables on first write via
--   delta-rs / Unity Catalog table-create API. Table DDL is preserved here
--   for human review and as the spec the worker code must match.
--
-- Designed against:
--   - EXECUTION_databricks_integration.md  (master plan)
--   - EXECUTION_integrity_engine.md        (audit chain replaces write_log)
--   - EXECUTION_public_commons.md          (gold.run_summaries added)
-- =============================================================================

-- =============================================================================
-- CATALOG + SCHEMAS (Phase 1 — created by uc_init.py)
-- =============================================================================

CREATE CATALOG IF NOT EXISTS research
  COMMENT 'OpenClaw research datastore — bronze/silver/gold medallion + audit';

CREATE SCHEMA IF NOT EXISTS research.bronze
  COMMENT 'Raw, unfiltered: every agent response and source fetch';

CREATE SCHEMA IF NOT EXISTS research.silver
  COMMENT 'Validated and structured: passes schema and virtue checks';

CREATE SCHEMA IF NOT EXISTS research.gold
  COMMENT 'Curated: research summaries, fairness scorecards, run summaries';

CREATE SCHEMA IF NOT EXISTS research.public_archive
  COMMENT 'Human-promoted, CC BY 4.0 — only PROMOTED briefs land here';

CREATE SCHEMA IF NOT EXISTS research.audit
  COMMENT 'Append-only integrity chain — tamper-evident audit trail';


-- =============================================================================
-- BRONZE — captures everything before any virtue check
-- =============================================================================

CREATE TABLE IF NOT EXISTS research.bronze.raw_responses (
  response_id      STRING NOT NULL,
  created_at       TIMESTAMP NOT NULL,
  agent_version    STRING NOT NULL,
  model_id         STRING NOT NULL,
  prompt_hash      STRING NOT NULL,
  response_text    STRING NOT NULL,
  response_hash    STRING NOT NULL,
  mlflow_run_id    STRING NOT NULL,
  topic_id         STRING
) USING DELTA;

CREATE TABLE IF NOT EXISTS research.bronze.source_fetches (
  fetch_id         STRING NOT NULL,
  response_id      STRING NOT NULL,
  fetched_at       TIMESTAMP NOT NULL,
  url              STRING NOT NULL,
  http_status      INT NOT NULL,
  body_hash        STRING NOT NULL,
  body_bytes       BIGINT,
  content_type     STRING,
  used_in_brief    BOOLEAN NOT NULL
) USING DELTA;


-- =============================================================================
-- SILVER — schema- and virtue-validated
-- =============================================================================

CREATE TABLE IF NOT EXISTS research.silver.validated_briefs (
  brief_id                STRING NOT NULL,
  response_id             STRING NOT NULL,
  created_at              TIMESTAMP NOT NULL,
  topic_id                STRING NOT NULL,
  title                   STRING NOT NULL,
  key_findings            STRING NOT NULL,
  counter_arguments       STRING NOT NULL,
  practical_implications  STRING NOT NULL,
  further_reading         STRING NOT NULL,
  known_gaps              STRING,
  content_hash            STRING NOT NULL,
  agent_version           STRING NOT NULL,
  CONSTRAINT counter_argument_required CHECK (length(counter_arguments) >= 100)
) USING DELTA;

CREATE TABLE IF NOT EXISTS research.silver.citations (
  citation_id       STRING NOT NULL,
  brief_id          STRING NOT NULL,
  fetch_id          STRING,
  url               STRING NOT NULL,
  title             STRING NOT NULL,
  authority_tier    INT NOT NULL,
  vendor_org        STRING,
  publication_date  DATE,
  is_paid_content   BOOLEAN NOT NULL,
  t4_justification  STRING,
  CONSTRAINT tier_range CHECK (authority_tier BETWEEN 1 AND 4),
  CONSTRAINT t4_requires_justification
    CHECK (authority_tier <> 4 OR length(t4_justification) > 0)
) USING DELTA;


-- =============================================================================
-- GOLD — curated, reviewable, scored
-- =============================================================================

CREATE TABLE IF NOT EXISTS research.gold.research_summaries (
  summary_id         STRING NOT NULL,
  brief_id           STRING NOT NULL,
  created_at         TIMESTAMP NOT NULL,
  status             STRING NOT NULL,
  rejection_reasons  ARRAY<STRING>,
  override_reason    STRING,
  promoted_at        TIMESTAMP,
  promoted_by        STRING,
  mlflow_run_id      STRING NOT NULL,
  CONSTRAINT status_values CHECK (status IN ('DRAFT', 'REJECTED', 'PROMOTED'))
) USING DELTA;

CREATE TABLE IF NOT EXISTS research.gold.fairness_scorecards (
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

CREATE TABLE IF NOT EXISTS research.gold.run_summaries (
  run_summary_id        STRING NOT NULL,
  mlflow_run_id         STRING NOT NULL,
  brief_id              STRING NOT NULL,
  started_at            TIMESTAMP NOT NULL,
  ended_at              TIMESTAMP NOT NULL,
  model_id              STRING NOT NULL,
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


-- =============================================================================
-- PUBLIC ARCHIVE — human-promoted, CC BY 4.0
-- =============================================================================

CREATE TABLE IF NOT EXISTS research.public_archive.published (
  published_id     STRING NOT NULL,
  summary_id       STRING NOT NULL,
  published_at     TIMESTAMP NOT NULL,
  license          STRING NOT NULL,
  attribution      STRING NOT NULL,
  markdown_export  STRING NOT NULL,
  content_hash     STRING NOT NULL
) USING DELTA;


-- =============================================================================
-- AUDIT — append-only integrity chain (replaces flat write_log)
-- =============================================================================
-- Schema and verification semantics: see EXECUTION_integrity_engine.md.
-- Every state-changing write across the catalog produces an entry here whose
-- row_signature includes the prior entry's signature, making the entire
-- history tamper-evident.

CREATE TABLE IF NOT EXISTS research.audit.integrity_chain (
  sequence_id          BIGINT NOT NULL,
  recorded_at          TIMESTAMP NOT NULL,
  author_identity      STRING NOT NULL,
  operation            STRING NOT NULL,
  target_table         STRING,
  target_row_id        STRING,
  target_payload_hash  STRING NOT NULL,
  previous_hash        STRING NOT NULL,
  row_signature        STRING NOT NULL,
  salt_version         INT NOT NULL,
  CONSTRAINT operation_values CHECK (operation IN
    ('GENESIS', 'INSERT', 'UPDATE', 'DELETE', 'CHECKPOINT')),
  CONSTRAINT sequence_monotonic CHECK (sequence_id >= 0),
  CONSTRAINT salt_version_positive CHECK (salt_version >= 1)
) USING DELTA
PARTITIONED BY (DATE(recorded_at));

CREATE TABLE IF NOT EXISTS research.audit.promotion_log (
  promotion_id     STRING NOT NULL,
  summary_id       STRING NOT NULL,
  promoted_at      TIMESTAMP NOT NULL,
  promoted_by      STRING NOT NULL,
  override_reason  STRING,
  prior_status     STRING NOT NULL,
  new_status       STRING NOT NULL
) USING DELTA;
