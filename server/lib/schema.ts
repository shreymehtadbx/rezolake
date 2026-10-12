// Lakebase operational schema (owned by the app service principal).
// Only workflow state lives here; all MDM state is in Delta (written by the engine job).

export const PG_SCHEMA = process.env.REZOLAKE_PG_SCHEMA ?? 'rezolake_v2';

export const SCHEMA_DDL = [
  `CREATE SCHEMA IF NOT EXISTS ${PG_SCHEMA}`,
  `CREATE TABLE IF NOT EXISTS ${PG_SCHEMA}.entity_type (
     id TEXT PRIMARY KEY,
     name TEXT NOT NULL UNIQUE,
     slug TEXT NOT NULL UNIQUE,
     entity_kind TEXT NOT NULL DEFAULT 'other',
     description TEXT,
     status TEXT NOT NULL DEFAULT 'DRAFT',
     published_version INT,
     strategy_version INT,
     created_by TEXT,
     created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
     updated_at TIMESTAMPTZ NOT NULL DEFAULT now())`,
  `CREATE TABLE IF NOT EXISTS ${PG_SCHEMA}.entity_definition_draft (
     entity_type_id TEXT PRIMARY KEY REFERENCES ${PG_SCHEMA}.entity_type(id) ON DELETE CASCADE,
     content JSONB NOT NULL DEFAULT '{}'::jsonb,
     updated_by TEXT,
     updated_at TIMESTAMPTZ NOT NULL DEFAULT now())`,
  `CREATE TABLE IF NOT EXISTS ${PG_SCHEMA}.strategy_draft (
     entity_type_id TEXT PRIMARY KEY REFERENCES ${PG_SCHEMA}.entity_type(id) ON DELETE CASCADE,
     content JSONB NOT NULL DEFAULT '{}'::jsonb,
     updated_by TEXT,
     updated_at TIMESTAMPTZ NOT NULL DEFAULT now())`,
  `CREATE TABLE IF NOT EXISTS ${PG_SCHEMA}.run_request (
     run_id TEXT PRIMARY KEY,
     entity_type_id TEXT NOT NULL,
     mode TEXT NOT NULL,
     status TEXT NOT NULL DEFAULT 'PENDING',
     stage TEXT,
     progress JSONB NOT NULL DEFAULT '{}'::jsonb,
     metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
     params JSONB NOT NULL DEFAULT '{}'::jsonb,
     trigger_source TEXT NOT NULL DEFAULT 'manual',
     job_run_id TEXT,
     requested_by TEXT,
     error TEXT,
     created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
     started_at TIMESTAMPTZ,
     completed_at TIMESTAMPTZ)`,
  `CREATE INDEX IF NOT EXISTS run_request_et_idx ON ${PG_SCHEMA}.run_request (entity_type_id, created_at DESC)`,
  `CREATE TABLE IF NOT EXISTS ${PG_SCHEMA}.resolution_command (
     id TEXT PRIMARY KEY,
     entity_type_id TEXT NOT NULL,
     command_type TEXT NOT NULL,
     payload JSONB NOT NULL DEFAULT '{}'::jsonb,
     status TEXT NOT NULL DEFAULT 'PENDING',
     requested_by TEXT,
     requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
     run_id TEXT,
     started_at TIMESTAMPTZ,
     applied_at TIMESTAMPTZ,
     error TEXT)`,
  `CREATE INDEX IF NOT EXISTS resolution_command_et_idx ON ${PG_SCHEMA}.resolution_command (entity_type_id, status)`,
  `CREATE TABLE IF NOT EXISTS ${PG_SCHEMA}.review_note (
     id TEXT PRIMARY KEY,
     entity_type_id TEXT NOT NULL,
     pair_id TEXT NOT NULL,
     verdict TEXT,
     flagged BOOLEAN NOT NULL DEFAULT false,
     note TEXT,
     author TEXT,
     created_at TIMESTAMPTZ NOT NULL DEFAULT now())`,
  `CREATE TABLE IF NOT EXISTS ${PG_SCHEMA}.orchestration_policy (
     entity_type_id TEXT PRIMARY KEY REFERENCES ${PG_SCHEMA}.entity_type(id) ON DELETE CASCADE,
     enabled BOOLEAN NOT NULL DEFAULT false,
     trigger_mode TEXT NOT NULL DEFAULT 'table_update',
     cron TEXT,
     timezone TEXT NOT NULL DEFAULT 'UTC',
     wait_after_last_change_seconds INT NOT NULL DEFAULT 60,
     min_time_between_triggers_seconds INT NOT NULL DEFAULT 300,
     databricks_job_id BIGINT,
     job_synced_at TIMESTAMPTZ,
     job_error TEXT,
     updated_by TEXT,
     updated_at TIMESTAMPTZ NOT NULL DEFAULT now())`,
  `CREATE INDEX IF NOT EXISTS review_note_pair_idx ON ${PG_SCHEMA}.review_note (entity_type_id, pair_id)`,
];

/** The engine job runs as its own identity; give it DML on the app-owned tables. */
export function engineGrants(role: string): string[] {
  const r = `"${role.replace(/"/g, '')}"`;
  return [
    `GRANT USAGE ON SCHEMA ${PG_SCHEMA} TO ${r}`,
    `GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ${PG_SCHEMA} TO ${r}`,
  ];
}
