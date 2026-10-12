"""INGEST: source Delta table -> source record ledger (append history + current).

Change detection is a source contract the engine owns:
  * first run / FULL / source without CDF  -> snapshot diff (detects deletes by absence)
  * otherwise                              -> table_changes(last+1 .. current)
The checkpoint only advances after the whole run succeeds (``commit_checkpoints``).
"""

from __future__ import annotations

from ..definition import Source
from ..ids import sql_record_id, sql_source_key
from .context import RunContext

CDF_COLS = ("_change_type", "_commit_version", "_commit_timestamp")


def _current_version(ctx: RunContext, s: Source) -> int:
    return int(ctx.sql(f"DESCRIBE HISTORY {s.quoted_table} LIMIT 1").first().version)


def _cdf_enabled(ctx: RunContext, s: Source) -> bool:
    props = {r.key: r.value for r in ctx.sql(f"SHOW TBLPROPERTIES {s.quoted_table}").collect()}
    return props.get("delta.enableChangeDataFeed", "false").lower() == "true"


def _checkpoint(ctx: RunContext, s: Source):
    return ctx.sql(
        f"""SELECT last_version FROM {ctx.cfg.meta}.source_checkpoint
            WHERE entity_type_id = {ctx.lit(ctx.entity_type_id)} AND source_id = {ctx.lit(s.source_id)}"""
    ).first()


def ingest(ctx: RunContext) -> None:
    ctx.stage("ingest")
    ctx.pending_checkpoints = []
    totals = {"inserted": 0, "updated": 0, "deleted": 0}
    for s in ctx.definition.active_sources:
        stats = _ingest_source(ctx, s)
        for k in totals:
            totals[k] += stats[k]
    ctx.metric(source_records_inserted=totals["inserted"], source_records_updated=totals["updated"],
               source_records_deleted=totals["deleted"])


def _ingest_source(ctx: RunContext, s: Source) -> dict:
    et, run = ctx.lit(ctx.entity_type_id), ctx.lit(ctx.run_id)
    cur = _current_version(ctx, s)
    cp = _checkpoint(ctx, s)
    cdf = _cdf_enabled(ctx, s)
    last = cp.last_version if cp else None

    if last is not None and last >= cur and ctx.mode != "FULL":
        ctx.log(f"source {s.source_id}: no new versions (at {cur})")
        return {"inserted": 0, "updated": 0, "deleted": 0}

    if last is None or ctx.mode == "FULL" or not cdf:
        read_mode = "snapshot"
        if last is not None and not cdf:
            ctx.log(f"WARNING source {s.source_id}: CDF disabled on {s.table}; using snapshot diff "
                    "(enable delta.enableChangeDataFeed for incremental reads)")
        src = f"(SELECT *, NULL AS _change_type, {cur} AS _commit_version FROM {s.quoted_table} VERSION AS OF {cur})"
    else:
        read_mode = "cdf"
        src = f"""(SELECT * FROM table_changes({ctx.lit(s.table.replace('`', ''))}, {last + 1}, {cur})
                  WHERE _change_type IN ('insert', 'update_postimage', 'delete'))"""

    key = sql_source_key(s.primary_key)
    rid = sql_record_id(ctx.entity_type_id, s.source_id, "_key")
    payload_cols = ", ".join(
        f"`{f.name}`" for f in ctx.spark.table(s.quoted_table).schema.fields if f.name not in CDF_COLS
    )
    staged = f"rz_stage_{s.source_id.replace('-', '_')}"
    # Latest change per key within the window; deletes carry no payload.
    ctx.sql(f"""
        CREATE OR REPLACE TEMP VIEW {staged} AS
        WITH raw AS (
          SELECT {key} AS _key, _change_type, _commit_version,
                 to_json(struct({payload_cols})) AS payload
          FROM {src}),
        ranked AS (
          SELECT *, row_number() OVER (PARTITION BY _key ORDER BY _commit_version DESC,
                   CASE _change_type WHEN 'delete' THEN 0 ELSE 1 END DESC) AS rn
          FROM raw WHERE _key IS NOT NULL AND _key <> '')
        SELECT {rid} AS record_id, _key AS source_key, _commit_version AS commit_version,
               coalesce(_change_type = 'delete', FALSE) AS is_delete,
               CASE WHEN _change_type = 'delete' THEN NULL ELSE payload END AS payload,
               CASE WHEN _change_type = 'delete' THEN NULL ELSE sha2(payload, 256) END AS record_hash
        FROM ranked WHERE rn = 1""")

    existing = f"""(SELECT record_id, record_hash, is_deleted FROM {ctx.cfg.records}.source_record_current
                    WHERE entity_type_id = {et} AND source_id = {ctx.lit(s.source_id)})"""
    snapshot_deletes = ""
    if read_mode == "snapshot":
        # Absence from a full snapshot means the source deleted the record.
        snapshot_deletes = f"""
          UNION ALL
          SELECT e.record_id, NULL AS source_key, {cur} AS commit_version, TRUE AS is_delete,
                 NULL AS payload, NULL AS record_hash, 'delete' AS change_type
          FROM {existing} e LEFT ANTI JOIN {staged} n ON n.record_id = e.record_id
          WHERE NOT e.is_deleted"""
    changes = f"{ctx.cfg.records}.`_stg_{s.source_id}_{ctx.run_id[:8]}`"
    ctx.sql(f"""
        CREATE OR REPLACE TABLE {changes} AS
        SELECT n.record_id, n.source_key, n.commit_version, n.is_delete, n.payload, n.record_hash,
               CASE WHEN n.is_delete THEN 'delete' WHEN e.record_id IS NULL OR e.is_deleted THEN 'insert'
                    ELSE 'update' END AS change_type
        FROM {staged} n LEFT JOIN {existing} e ON e.record_id = n.record_id
        WHERE (n.is_delete AND e.record_id IS NOT NULL AND NOT e.is_deleted)
           OR (NOT n.is_delete AND (e.record_id IS NULL OR e.is_deleted OR e.record_hash <> n.record_hash))
        {snapshot_deletes}""")

    counts = {r.change_type: r.n for r in ctx.sql(
        f"SELECT change_type, count(*) n FROM {changes} GROUP BY change_type").collect()}
    stats = {"inserted": counts.get("insert", 0), "updated": counts.get("update", 0), "deleted": counts.get("delete", 0)}
    ctx.log(f"source {s.source_id}: {read_mode} {last}->{cur} {stats}")

    if sum(stats.values()):
        ctx.sql(f"""
            INSERT INTO {ctx.cfg.records}.source_record_version
            SELECT {et}, record_id, {ctx.lit(s.source_id)}, source_key, payload, record_hash,
                   commit_version, change_type, {run}, current_timestamp()
            FROM {changes}""")
        ctx.sql(f"""
            MERGE INTO {ctx.cfg.records}.source_record_current t
            USING {changes} c
            ON t.entity_type_id = {et} AND t.record_id = c.record_id
            WHEN MATCHED AND c.is_delete THEN UPDATE SET
              is_deleted = TRUE, latest_commit_version = c.commit_version,
              updated_at = current_timestamp(), last_run_id = {run}
            WHEN MATCHED THEN UPDATE SET
              payload = c.payload, record_hash = c.record_hash, is_deleted = FALSE,
              latest_commit_version = c.commit_version, updated_at = current_timestamp(), last_run_id = {run}
            WHEN NOT MATCHED AND NOT c.is_delete THEN INSERT
              (entity_type_id, record_id, source_id, source_key, payload, record_hash, is_deleted,
               latest_commit_version, first_seen_at, updated_at, last_run_id)
              VALUES ({et}, c.record_id, {ctx.lit(s.source_id)}, c.source_key, c.payload, c.record_hash,
                      FALSE, c.commit_version, current_timestamp(), current_timestamp(), {run})""")

    ctx.sql(f"""
        INSERT INTO {ctx.cfg.meta}.resolution_run_source VALUES
        ({run}, {et}, {ctx.lit(s.source_id)}, {ctx.lit(s.table)}, {last if last is not None else 'NULL'}, {cur},
         {ctx.lit(read_mode)}, {stats['inserted']}, {stats['updated']}, {stats['deleted']})""")
    ctx.sql(f"DROP TABLE IF EXISTS {changes}")
    ctx.pending_checkpoints.append((s, cur, read_mode))
    return stats


def commit_checkpoints(ctx: RunContext) -> None:
    et, run = ctx.lit(ctx.entity_type_id), ctx.lit(ctx.run_id)
    for s, version, read_mode in getattr(ctx, "pending_checkpoints", []):
        ctx.sql(f"""
            MERGE INTO {ctx.cfg.meta}.source_checkpoint t
            USING (SELECT {et} AS entity_type_id, {ctx.lit(s.source_id)} AS source_id) k
            ON t.entity_type_id = k.entity_type_id AND t.source_id = k.source_id
            WHEN MATCHED THEN UPDATE SET last_version = {version}, read_mode = {ctx.lit(read_mode)},
              source_table = {ctx.lit(s.table)}, last_run_id = {run}, updated_at = current_timestamp()
            WHEN NOT MATCHED THEN INSERT (entity_type_id, source_id, source_table, last_version, read_mode, last_run_id,
              updated_at) VALUES ({et}, {ctx.lit(s.source_id)}, {ctx.lit(s.table)},
              {version}, {ctx.lit(read_mode)}, {run}, current_timestamp())""")
