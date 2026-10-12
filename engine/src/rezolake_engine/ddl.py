"""Delta data contracts. Idempotent; applied by the SETUP mode."""

from __future__ import annotations

from .config import EngineConfig

CDF = "TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')"


def statements(c: EngineConfig) -> list[str]:
    m, r, x, g = c.meta, c.records, c.resolution, c.master
    return [
        f"CREATE SCHEMA IF NOT EXISTS {m} COMMENT 'Rezolake v2: published config, run manifests, checkpoints'",
        f"CREATE SCHEMA IF NOT EXISTS {r} COMMENT 'Rezolake v2: source ledger, canonical records, match representation'",
        f"CREATE SCHEMA IF NOT EXISTS {x} COMMENT 'Rezolake v2: candidates, scores, decisions, constraints, membership'",
        f"CREATE SCHEMA IF NOT EXISTS {g} COMMENT 'Rezolake v2: mastered entities, golden attributes + provenance'",
        # ── meta ──────────────────────────────────────────────────────────
        f"""CREATE TABLE IF NOT EXISTS {m}.entity_definition_version (
            entity_type_id STRING NOT NULL, version INT NOT NULL, slug STRING, name STRING,
            definition STRING, published_by STRING, published_at TIMESTAMP)""",
        f"""CREATE TABLE IF NOT EXISTS {m}.strategy_version (
            entity_type_id STRING NOT NULL, version INT NOT NULL, strategy STRING,
            published_by STRING, published_at TIMESTAMP)""",
        f"""CREATE TABLE IF NOT EXISTS {m}.source_checkpoint (
            entity_type_id STRING NOT NULL, source_id STRING NOT NULL, source_table STRING,
            last_version BIGINT, read_mode STRING, last_run_id STRING, updated_at TIMESTAMP)""",
        f"""CREATE TABLE IF NOT EXISTS {m}.resolution_run (
            run_id STRING NOT NULL, entity_type_id STRING, mode STRING, trigger_source STRING,
            requested_by STRING, job_run_id STRING, manifest STRING, status STRING, error STRING,
            metrics STRING, started_at TIMESTAMP, completed_at TIMESTAMP)""",
        f"""CREATE TABLE IF NOT EXISTS {m}.resolution_run_source (
            run_id STRING NOT NULL, entity_type_id STRING, source_id STRING, source_table STRING,
            start_version BIGINT, end_version BIGINT, read_mode STRING,
            inserted BIGINT, updated BIGINT, deleted BIGINT)""",
        # ── records ───────────────────────────────────────────────────────
        f"""CREATE TABLE IF NOT EXISTS {r}.source_record_version (
            entity_type_id STRING NOT NULL, record_id STRING NOT NULL, source_id STRING,
            source_key STRING, payload STRING, record_hash STRING, source_commit_version BIGINT,
            change_type STRING, run_id STRING, observed_at TIMESTAMP)
            CLUSTER BY (entity_type_id, record_id)""",
        f"""CREATE TABLE IF NOT EXISTS {r}.source_record_current (
            entity_type_id STRING NOT NULL, record_id STRING NOT NULL, source_id STRING,
            source_key STRING, payload STRING, record_hash STRING, is_deleted BOOLEAN,
            latest_commit_version BIGINT, first_seen_at TIMESTAMP, updated_at TIMESTAMP,
            last_run_id STRING)
            CLUSTER BY (entity_type_id, record_id)""",
        f"""CREATE TABLE IF NOT EXISTS {r}.canonical_record_current (
            entity_type_id STRING NOT NULL, record_id STRING NOT NULL, source_id STRING,
            source_key STRING, attributes MAP<STRING, STRING>, additional_attributes STRING,
            record_hash STRING, source_record_hash STRING, definition_version INT,
            is_deleted BOOLEAN, updated_at TIMESTAMP, last_run_id STRING)
            CLUSTER BY (entity_type_id, record_id)""",
        f"""CREATE TABLE IF NOT EXISTS {r}.record_override (
            override_id STRING NOT NULL, entity_type_id STRING, record_id STRING,
            attribute_id STRING, value STRING, active BOOLEAN, created_by STRING,
            created_at TIMESTAMP, command_id STRING, revoked_at TIMESTAMP)""",
        f"""CREATE TABLE IF NOT EXISTS {r}.match_representation (
            record_id STRING NOT NULL, entity_type_id STRING, match_text STRING,
            features MAP<STRING, STRING>, embedding ARRAY<FLOAT>, canonical_hash STRING,
            embedding_model STRING, is_deleted BOOLEAN, updated_at TIMESTAMP, last_run_id STRING)
            {CDF}""",
        # ── resolution ────────────────────────────────────────────────────
        f"""CREATE TABLE IF NOT EXISTS {x}.candidate_pair (
            entity_type_id STRING NOT NULL, run_id STRING, pair_id STRING,
            left_record_id STRING, right_record_id STRING, query_record_id STRING,
            left_hash STRING, right_hash STRING, blocker_id STRING, blocker_version STRING,
            candidate_rank INT, block_score DOUBLE, block_reason STRING, created_at TIMESTAMP)
            CLUSTER BY (entity_type_id, run_id)""",
        f"""CREATE TABLE IF NOT EXISTS {x}.pair_score (
            entity_type_id STRING NOT NULL, run_id STRING, pair_id STRING,
            left_record_id STRING, right_record_id STRING, left_hash STRING, right_hash STRING,
            matcher_id STRING, matcher_version STRING, score DOUBLE, features STRING,
            evidence STRING, trace_id STRING, created_at TIMESTAMP)
            CLUSTER BY (entity_type_id, pair_id)""",
        f"""CREATE TABLE IF NOT EXISTS {x}.resolution_decision (
            entity_type_id STRING NOT NULL, run_id STRING, pair_id STRING,
            left_record_id STRING, right_record_id STRING, left_hash STRING, right_hash STRING,
            decision STRING, decision_source STRING, score DOUBLE, policy_id STRING,
            policy_version STRING, reason STRING, created_at TIMESTAMP,
            strategy_version INT, blocker STRING, scores STRING)
            CLUSTER BY (entity_type_id, pair_id)""",
        f"""CREATE TABLE IF NOT EXISTS {x}.manual_constraint (
            constraint_id STRING NOT NULL, entity_type_id STRING, constraint_type STRING,
            left_record_id STRING, right_record_id STRING, reason STRING, created_by STRING,
            created_at TIMESTAMP, command_id STRING, active BOOLEAN, revoked_at TIMESTAMP,
            revoked_by STRING)""",
        f"""CREATE TABLE IF NOT EXISTS {x}.record_match_state (
            entity_type_id STRING NOT NULL, record_id STRING NOT NULL, blocked_hash STRING,
            blocked_run_id STRING, updated_at TIMESTAMP)
            CLUSTER BY (entity_type_id, record_id)""",
        f"""CREATE TABLE IF NOT EXISTS {x}.entity_membership_current (
            entity_type_id STRING NOT NULL, entity_id STRING, record_id STRING,
            run_id STRING, joined_at TIMESTAMP)
            CLUSTER BY (entity_type_id, entity_id)""",
        f"""CREATE TABLE IF NOT EXISTS {x}.entity_event (
            event_id STRING NOT NULL, entity_type_id STRING, event_type STRING, entity_id STRING,
            source_entity_ids ARRAY<STRING>, target_entity_ids ARRAY<STRING>,
            run_id STRING, command_id STRING, created_at TIMESTAMP)""",
        f"""CREATE TABLE IF NOT EXISTS {x}.entity_alias (
            entity_type_id STRING NOT NULL, old_entity_id STRING, current_entity_id STRING,
            reason STRING, run_id STRING, created_at TIMESTAMP)""",
        # ── master ────────────────────────────────────────────────────────
        f"""CREATE TABLE IF NOT EXISTS {g}.entity_current (
            entity_type_id STRING NOT NULL, entity_id STRING NOT NULL, anchor_record_id STRING,
            member_count INT, source_count INT, display_name STRING, golden STRING,
            created_at TIMESTAMP, updated_at TIMESTAMP, last_run_id STRING)
            CLUSTER BY (entity_type_id, entity_id)""",
        f"""CREATE TABLE IF NOT EXISTS {g}.entity_attribute_current (
            entity_type_id STRING NOT NULL, entity_id STRING NOT NULL, attribute_id STRING,
            attribute_name STRING, value STRING, winning_record_id STRING, winning_source_id STRING,
            rule STRING, survivorship_version STRING, manual_override BOOLEAN, observed STRING,
            run_id STRING, updated_at TIMESTAMP)
            CLUSTER BY (entity_type_id, entity_id)""",
        f"""CREATE TABLE IF NOT EXISTS {g}.survivorship_override (
            override_id STRING NOT NULL, entity_type_id STRING, entity_id STRING,
            override_type STRING, attribute_id STRING, value STRING, record_id STRING,
            active BOOLEAN, created_by STRING, created_at TIMESTAMP, command_id STRING,
            revoked_at TIMESTAMP)""",
        # ── views (all read by the app through the RT warehouse) ──────────
        f"""CREATE OR REPLACE VIEW {x}.current_decision_v AS
            WITH latest AS (
              SELECT *, row_number() OVER (PARTITION BY entity_type_id, pair_id ORDER BY created_at DESC) rn
              FROM {x}.resolution_decision)
            SELECT d.entity_type_id, d.run_id, d.pair_id, d.left_record_id, d.right_record_id,
                   d.decision, d.decision_source, d.score, d.policy_version, d.reason, d.created_at,
                   d.left_hash, d.right_hash, d.strategy_version, d.blocker, d.scores,
                   (d.left_hash = l.record_hash AND d.right_hash = r.record_hash
                    AND NOT l.is_deleted AND NOT r.is_deleted) AS is_current
            FROM latest d
            JOIN {r}.canonical_record_current l ON l.entity_type_id = d.entity_type_id AND l.record_id = d.left_record_id
            JOIN {r}.canonical_record_current r ON r.entity_type_id = d.entity_type_id AND r.record_id = d.right_record_id
            WHERE d.rn = 1""",
        f"""CREATE OR REPLACE VIEW {x}.active_constraint_v AS
            SELECT * FROM {x}.manual_constraint WHERE active""",
        f"""CREATE OR REPLACE VIEW {x}.review_queue_v AS
            SELECT d.* FROM {x}.current_decision_v d
            WHERE d.is_current AND d.decision = 'REVIEW'
              AND NOT EXISTS (
                SELECT 1 FROM {x}.manual_constraint c
                WHERE c.active AND c.entity_type_id = d.entity_type_id
                  AND c.left_record_id = d.left_record_id AND c.right_record_id = d.right_record_id)""",
        f"""CREATE OR REPLACE VIEW {g}.entity_crosswalk_v AS
            SELECT m.entity_type_id, m.entity_id, m.record_id, c.source_id, c.source_key,
                   (e.anchor_record_id = m.record_id) AS is_anchor
            FROM {x}.entity_membership_current m
            JOIN {r}.canonical_record_current c ON c.entity_type_id = m.entity_type_id AND c.record_id = m.record_id
            JOIN {g}.entity_current e ON e.entity_type_id = m.entity_type_id AND e.entity_id = m.entity_id""",
    ]


# Columns added after a table was first created: (schema attr, table, column, type).
ADDED_COLUMNS = [
    ("resolution", "resolution_decision", "strategy_version", "INT"),
    ("resolution", "resolution_decision", "blocker", "STRING"),
    ("resolution", "resolution_decision", "scores", "STRING"),
]


def migrate(ctx) -> None:
    """Add columns that existing tables lack, then refresh the views that expose them. Idempotent and cheap."""
    added = False
    for schema_attr, table, column, sql_type in ADDED_COLUMNS:
        fqn = f"{getattr(ctx.cfg, schema_attr)}.{table}"
        cols = {r.col_name for r in ctx.sql(f"DESCRIBE TABLE {fqn}").collect()}
        if column not in cols:
            ctx.sql(f"ALTER TABLE {fqn} ADD COLUMNS ({column} {sql_type})")
            ctx.log(f"migrate: added {fqn}.{column}")
            added = True
    if added:
        for stmt in statements(ctx.cfg):
            if stmt.lstrip().startswith("CREATE OR REPLACE VIEW"):
                ctx.sql(stmt)


def grants(c: EngineConfig) -> list[str]:
    if not c.app_sp:
        return []
    p = f"`{c.app_sp}`"
    out = [f"GRANT USE CATALOG ON CATALOG `{c.catalog}` TO {p}"]
    for s in (c.meta, c.records, c.resolution, c.master):
        out.append(f"GRANT USE SCHEMA, SELECT ON SCHEMA {s} TO {p}")
    return out
