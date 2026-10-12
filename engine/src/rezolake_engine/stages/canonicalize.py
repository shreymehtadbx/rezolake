"""CANONICALIZE: source payload -> canonical attributes (by stable attribute_id).

Mapped columns become canonical attributes; unmapped columns default to
source-namespaced extensions (``additional_attributes``); record-level steward
overrides are applied last so they survive re-ingest.
"""

from __future__ import annotations

from ..definition import EXTENSION, IGNORE, Source
from .context import RunContext


def _json_path(col: str) -> str:
    esc = col.replace("\\", "\\\\").replace("'", "\\'")
    return f"$['{esc}']"


_CASE = {"title": "initcap({})", "upper": "upper({})", "lower": "lower({})"}


def _source_expr(ctx: RunContext, s: Source, all_columns: list[str]) -> tuple[str, str]:
    """(attributes map expr, additional_attributes json expr) for one source."""
    by_attr: dict[str, list[str]] = {}
    ext: list[str] = []
    for col in all_columns:
        if col in s.primary_key and col not in s.mappings:
            continue
        target = s.mappings.get(col, EXTENSION)
        if target == IGNORE:
            continue
        if target == EXTENSION:
            ext.append(col)
        else:
            by_attr.setdefault(target, []).append(col)
    parts = []
    for aid, cols in by_attr.items():
        vals = ", ".join(
            f"nullif(trim(get_json_object(s.payload, {ctx.lit(_json_path(c))})), '')" for c in cols
        )
        value = f"coalesce({vals})" if len(cols) > 1 else vals
        fmt = _CASE.get(ctx.definition.attribute(aid).standardize)
        parts.append(f"{ctx.lit(aid)}, {fmt.format(value) if fmt else value}")
    attrs = f"map_filter(map({', '.join(parts)}), (k, v) -> v IS NOT NULL)" if parts else "map()"
    if ext:
        ext_parts = ", ".join(
            f"{ctx.lit(c)}, get_json_object(s.payload, {ctx.lit(_json_path(c))})" for c in ext
        )
        extras = f"to_json(map({ctx.lit(s.source_id)}, map_filter(map({ext_parts}), (k, v) -> v IS NOT NULL)))"
    else:
        extras = "'{}'"
    return attrs, extras


def canonicalize(ctx: RunContext) -> None:
    ctx.stage("canonicalize")
    d, et, run = ctx.definition, ctx.lit(ctx.entity_type_id), ctx.lit(ctx.run_id)
    full = ctx.mode == "FULL"
    override_ids = ", ".join(ctx.lit(r) for r in sorted(ctx.override_records)) or "NULL"

    branches = []
    for s in d.active_sources:
        cols = [f.name for f in ctx.spark.table(s.quoted_table).schema.fields
                if f.name not in ("_change_type", "_commit_version", "_commit_timestamp")]
        attrs, extras = _source_expr(ctx, s, cols)
        branches.append(f"""
            SELECT s.record_id, s.source_id, s.source_key, s.is_deleted, s.record_hash AS source_record_hash,
                   {attrs} AS base_attrs, {extras} AS additional_attributes
            FROM {ctx.cfg.records}.source_record_current s
            LEFT JOIN {ctx.cfg.records}.canonical_record_current c
              ON c.entity_type_id = s.entity_type_id AND c.record_id = s.record_id
            WHERE s.entity_type_id = {et} AND s.source_id = {ctx.lit(s.source_id)}
              AND ({'TRUE' if full else 'FALSE'}
                   OR c.record_id IS NULL
                   OR c.source_record_hash IS DISTINCT FROM s.record_hash
                   OR c.is_deleted <> s.is_deleted
                   OR c.definition_version <> {d.version}
                   OR s.record_id IN ({override_ids}))""")
    union = "\nUNION ALL\n".join(branches)

    stg = f"{ctx.cfg.records}.`_stg_canon_{ctx.run_id[:8]}`"
    ctx.sql(f"""
        CREATE OR REPLACE TABLE {stg} AS
        WITH changed AS ({union}),
        ov AS (
          SELECT record_id, map_from_entries(collect_list(struct(attribute_id, value))) AS ov
          FROM (SELECT *, row_number() OVER (PARTITION BY record_id, attribute_id ORDER BY created_at DESC) rn
                FROM {ctx.cfg.records}.record_override WHERE entity_type_id = {et} AND active)
          WHERE rn = 1 GROUP BY record_id),
        merged AS (
          SELECT c.*, CASE WHEN ov.ov IS NULL THEN c.base_attrs ELSE
                   map_filter(map_concat(map_filter(c.base_attrs, (k, v) -> NOT array_contains(map_keys(ov.ov), k)), ov.ov),
                              (k, v) -> v IS NOT NULL) END AS attributes
          FROM changed c LEFT JOIN ov ON ov.record_id = c.record_id)
        SELECT record_id, source_id, source_key, is_deleted, source_record_hash, attributes, additional_attributes,
               sha2(to_json(map_from_entries(array_sort(map_entries(attributes)))), 256) AS record_hash
        FROM merged""")

    n = ctx.sql(f"SELECT count(*) n FROM {stg}").first().n
    if n:
        ctx.sql(f"""
            MERGE INTO {ctx.cfg.records}.canonical_record_current t
            USING {stg} c ON t.entity_type_id = {et} AND t.record_id = c.record_id
            WHEN MATCHED THEN UPDATE SET
              attributes = c.attributes, additional_attributes = c.additional_attributes,
              record_hash = c.record_hash, source_record_hash = c.source_record_hash,
              definition_version = {d.version}, is_deleted = c.is_deleted,
              updated_at = CASE WHEN t.record_hash IS DISTINCT FROM c.record_hash OR t.is_deleted <> c.is_deleted
                                THEN current_timestamp() ELSE t.updated_at END,
              last_run_id = CASE WHEN t.record_hash IS DISTINCT FROM c.record_hash OR t.is_deleted <> c.is_deleted
                                 THEN {run} ELSE t.last_run_id END
            WHEN NOT MATCHED THEN INSERT
              (entity_type_id, record_id, source_id, source_key, attributes, additional_attributes, record_hash,
               source_record_hash, definition_version, is_deleted, updated_at, last_run_id)
              VALUES ({et}, c.record_id, c.source_id, c.source_key, c.attributes, c.additional_attributes,
                      c.record_hash, c.source_record_hash, {d.version}, c.is_deleted, current_timestamp(), {run})""")
    ctx.sql(f"DROP TABLE IF EXISTS {stg}")
    changed = ctx.sql(f"""SELECT count(*) n FROM {ctx.cfg.records}.canonical_record_current
                          WHERE entity_type_id = {et} AND last_run_id = {run}""").first().n
    ctx.metric(canonical_evaluated=n, canonical_changed=changed)
    ctx.log(f"canonicalize: evaluated {n}, changed {changed}")
    _write_typed_view(ctx)


def _write_typed_view(ctx: RunContext) -> None:
    """Consumer-facing typed view <slug>_canonical over the generic table."""
    d = ctx.definition
    casts = {"integer": "BIGINT", "float": "DOUBLE", "date": "DATE", "boolean": "BOOLEAN"}
    cols = ",\n".join(
        (f"try_cast(attributes[{ctx.lit(a.attribute_id)}] AS {casts[a.data_type]})" if a.data_type in casts
         else f"attributes[{ctx.lit(a.attribute_id)}]") + f" AS `{a.name}`"
        for a in d.attributes
    )
    ctx.sql(f"""
        CREATE OR REPLACE VIEW {ctx.cfg.records}.`{d.slug}_canonical` AS
        SELECT record_id, source_id, source_key, {cols}, additional_attributes, updated_at
        FROM {ctx.cfg.records}.canonical_record_current
        WHERE entity_type_id = {ctx.lit(ctx.entity_type_id)} AND NOT is_deleted""")
