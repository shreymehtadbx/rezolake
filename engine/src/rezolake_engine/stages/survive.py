"""SURVIVE: members + rules + steward overrides -> golden attributes with provenance."""

from __future__ import annotations

import json
from collections import defaultdict

from pyspark.sql import Row

from ..components.survivorship import AttributeRule, MemberRecord, resolve_entity
from .context import RunContext


def _ids_view(ctx: RunContext, name: str, ids: set[str]) -> None:
    ctx.view(name, [{"id": i} for i in ids], "id STRING")


def survive(ctx: RunContext) -> None:
    ctx.stage("survive")
    d = ctx.definition
    et = ctx.lit(ctx.entity_type_id)
    x, r, g = ctx.cfg.resolution, ctx.cfg.records, ctx.cfg.master
    ident = getattr(ctx, "cluster_result", None)

    # Which entities need their golden record recomputed?
    affected = set(ident.changed_entities) if ident else set()
    for row in ctx.sql(f"""
            SELECT DISTINCT m.entity_id FROM {x}.entity_membership_current m
            JOIN {r}.canonical_record_current c ON c.entity_type_id = m.entity_type_id AND c.record_id = m.record_id
            WHERE m.entity_type_id = {et} AND c.last_run_id = {ctx.lit(ctx.run_id)}""").collect():
        affected.add(row.entity_id)
    for row in ctx.sql(f"""
            SELECT m.entity_id FROM {x}.entity_membership_current m
            LEFT JOIN {g}.entity_current e ON e.entity_type_id = m.entity_type_id AND e.entity_id = m.entity_id
            WHERE m.entity_type_id = {et} AND (e.entity_id IS NULL OR {'TRUE' if ctx.mode == 'FULL' else 'FALSE'})
            GROUP BY m.entity_id""").collect():
        affected.add(row.entity_id)
    affected |= _resolve_aliases(ctx, ctx.override_entities)
    if ctx.override_records:
        _ids_view(ctx, "rz_ovr_recs", ctx.override_records)
        affected |= {row.entity_id for row in ctx.sql(
            f"""SELECT entity_id FROM {x}.entity_membership_current m JOIN rz_ovr_recs o ON o.id = m.record_id
                WHERE m.entity_type_id = {et}""").collect()}

    gone = set()
    if ident:
        gone = set(ident.retired_entities) | {a[0] for a in ident.aliases}

    ctx.log(f"survive: {len(affected)} entities to (re)compute, {len(gone)} to remove")
    ctx.metric(golden_updated=len(affected), golden_removed=len(gone))
    if not affected and not gone:
        _write_typed_master(ctx)
        return

    _ids_view(ctx, "rz_affected", affected)
    priority = {s.source_id: s.priority for s in d.sources}
    members: dict[str, list[MemberRecord]] = defaultdict(list)
    anchors: dict[str, str] = {}
    for row in ctx.sql(f"""
            SELECT m.entity_id, c.record_id, c.source_id, cast(c.updated_at AS STRING) updated_at, c.attributes
            FROM {x}.entity_membership_current m
            JOIN rz_affected a ON a.id = m.entity_id
            JOIN {r}.canonical_record_current c ON c.entity_type_id = m.entity_type_id AND c.record_id = m.record_id
            WHERE m.entity_type_id = {et}""").collect():
        members[row.entity_id].append(MemberRecord(row.record_id, row.source_id, priority.get(row.source_id, 100),
                                                   row.updated_at or "", dict(row.attributes or {})))
    if ident:
        anchors = dict(ident.anchors)
    else:
        anchors = {row.entity_id: row.anchor_record_id for row in ctx.sql(
            f"SELECT entity_id, anchor_record_id FROM {g}.entity_current WHERE entity_type_id = {et}").collect()}

    value_ovr, pins = _overrides(ctx)
    rules = [AttributeRule(a.attribute_id, a.survivorship) for a in d.attributes]
    names = {a.attribute_id: a.name for a in d.attributes}
    display_ids = [a.attribute_id for a in d.attributes if a.display][:2]
    member_of = {m.record_id: eid for eid, ms in members.items() for m in ms}
    pin_by_entity = {member_of[rec]: rec for rec in pins if rec in member_of}

    attr_rows, ent_rows = [], []
    for eid, ms in members.items():
        winners = resolve_entity(ms, rules, value_ovr.get(eid), pin_by_entity.get(eid))
        golden = {}
        for w in winners:
            golden[names[w.attribute_id]] = w.value
            attr_rows.append(Row(entity_id=eid, attribute_id=w.attribute_id, attribute_name=names[w.attribute_id],
                                 value=w.value, winning_record_id=w.winning_record_id,
                                 winning_source_id=w.winning_source_id, rule=w.rule,
                                 manual_override=w.manual_override, observed=json.dumps(w.observed)))
        by_id = {w.attribute_id: w.value for w in winners}
        display = " ".join(str(by_id[a]) for a in display_ids if by_id.get(a)) or eid[:8]
        ent_rows.append(Row(entity_id=eid, anchor_record_id=anchors.get(eid, ms[0].record_id),
                            member_count=len(ms), source_count=len({m.source_id for m in ms}),
                            display_name=display, golden=json.dumps(golden)))

    _ids_view(ctx, "rz_gone", gone | affected)
    ctx.sql(f"""DELETE FROM {g}.entity_attribute_current WHERE entity_type_id = {et}
                AND entity_id IN (SELECT id FROM rz_gone)""")
    ctx.sql(f"""DELETE FROM {g}.entity_current WHERE entity_type_id = {et}
                AND entity_id IN (SELECT id FROM rz_gone) AND entity_id NOT IN (SELECT id FROM rz_affected)""")
    sv = ctx.lit(ctx.strategy.survivorship.ref)
    run = ctx.lit(ctx.run_id)
    if attr_rows:
        ctx.view("rz_attrs", attr_rows,
                 "entity_id STRING, attribute_id STRING, attribute_name STRING, value STRING, "
                 "winning_record_id STRING, winning_source_id STRING, rule STRING, manual_override BOOLEAN, "
                 "observed STRING")
        ctx.sql(f"""INSERT INTO {g}.entity_attribute_current
                    SELECT {et}, entity_id, attribute_id, attribute_name, value, winning_record_id, winning_source_id,
                           rule, {sv}, manual_override, observed, {run}, current_timestamp() FROM rz_attrs""")
    if ent_rows:
        ctx.view("rz_ents", ent_rows,
                 "entity_id STRING, anchor_record_id STRING, member_count INT, source_count INT, "
                 "display_name STRING, golden STRING")
        ctx.sql(f"""
            MERGE INTO {g}.entity_current t USING rz_ents s
            ON t.entity_type_id = {et} AND t.entity_id = s.entity_id
            WHEN MATCHED THEN UPDATE SET anchor_record_id = s.anchor_record_id, member_count = s.member_count,
              source_count = s.source_count, display_name = s.display_name, golden = s.golden,
              updated_at = current_timestamp(), last_run_id = {run}
            WHEN NOT MATCHED THEN INSERT (entity_type_id, entity_id, anchor_record_id, member_count, source_count,
              display_name, golden, created_at, updated_at, last_run_id) VALUES ({et}, s.entity_id, s.anchor_record_id, s.member_count,
              s.source_count, s.display_name, s.golden, current_timestamp(), current_timestamp(), {run})""")
    _write_typed_master(ctx)


def _resolve_aliases(ctx: RunContext, ids: set[str]) -> set[str]:
    if not ids:
        return set()
    _ids_view(ctx, "rz_alias_in", ids)
    rows = ctx.sql(f"""SELECT i.id, coalesce(a.current_entity_id, i.id) cur FROM rz_alias_in i
                       LEFT JOIN {ctx.cfg.resolution}.entity_alias a
                         ON a.entity_type_id = {ctx.lit(ctx.entity_type_id)} AND a.old_entity_id = i.id""").collect()
    return {r.cur for r in rows}


def _overrides(ctx: RunContext) -> tuple[dict[str, dict[str, str]], set[str]]:
    et = ctx.lit(ctx.entity_type_id)
    rows = ctx.sql(f"""
        SELECT coalesce(a.current_entity_id, o.entity_id) entity_id, o.override_type, o.attribute_id, o.value,
               o.record_id
        FROM (SELECT *, row_number() OVER (PARTITION BY entity_id, override_type, attribute_id
                                           ORDER BY created_at DESC) rn
              FROM {ctx.cfg.master}.survivorship_override WHERE entity_type_id = {et} AND active) o
        LEFT JOIN {ctx.cfg.resolution}.entity_alias a ON a.entity_type_id = {et} AND a.old_entity_id = o.entity_id
        WHERE o.rn = 1""").collect()
    values: dict[str, dict[str, str]] = defaultdict(dict)
    pins: set[str] = set()
    for r in rows:
        if r.override_type == "OVERRIDE_VALUE":
            values[r.entity_id][r.attribute_id] = r.value
        elif r.override_type == "PIN_RECORD" and r.record_id:
            pins.add(r.record_id)
    return values, pins


def _write_typed_master(ctx: RunContext) -> None:
    """Consumer-facing wide table rezolake2_master.<slug>_entity (typed columns)."""
    d = ctx.definition
    casts = {"integer": "BIGINT", "float": "DOUBLE", "date": "DATE", "boolean": "BOOLEAN"}
    cols = ",\n".join(
        (f"try_cast(max(CASE WHEN a.attribute_id = {ctx.lit(x.attribute_id)} THEN a.value END) AS {casts[x.data_type]})"
         if x.data_type in casts else f"max(CASE WHEN a.attribute_id = {ctx.lit(x.attribute_id)} THEN a.value END)")
        + f" AS `{x.name}`"
        for x in d.attributes
    )
    et = ctx.lit(ctx.entity_type_id)
    ctx.sql(f"""
        CREATE OR REPLACE TABLE {ctx.cfg.master}.`{d.slug}_entity` AS
        SELECT e.entity_id, {cols}, e.member_count, e.source_count, e.created_at, e.updated_at
        FROM {ctx.cfg.master}.entity_current e
        LEFT JOIN {ctx.cfg.master}.entity_attribute_current a
          ON a.entity_type_id = e.entity_type_id AND a.entity_id = e.entity_id
        WHERE e.entity_type_id = {et}
        GROUP BY e.entity_id, e.member_count, e.source_count, e.created_at, e.updated_at""")
