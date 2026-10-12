"""CLUSTER: effective edges + human constraints -> entity membership with stable ids."""

from __future__ import annotations

from pyspark.sql import Row

from ..components.base import Constraint, Edge
from ..components.clustering import PriorEntity, assign_stable_ids, constrained_components
from ..ids import new_id
from .context import RunContext


def cluster(ctx: RunContext) -> None:
    ctx.stage("cluster")
    et = ctx.lit(ctx.entity_type_id)
    x, r, g = ctx.cfg.resolution, ctx.cfg.records, ctx.cfg.master

    nodes = [row.record_id for row in ctx.sql(
        f"SELECT record_id FROM {r}.canonical_record_current WHERE entity_type_id = {et} AND NOT is_deleted").collect()]
    edges = [Edge(row.left_record_id, row.right_record_id, row.score) for row in ctx.sql(
        f"""SELECT left_record_id, right_record_id, score FROM {x}.current_decision_v
            WHERE entity_type_id = {et} AND is_current AND decision = 'LINK'""").collect()]
    constraints = [Constraint(row.constraint_type, row.left_record_id, row.right_record_id) for row in ctx.sql(
        f"""SELECT constraint_type, left_record_id, right_record_id FROM {x}.manual_constraint
            WHERE entity_type_id = {et} AND active""").collect()]
    # Opt-in (strategy clusterer config): confident automatic non-matches keep records apart.
    veto_below = ctx.strategy.clusterer.config.get("no_link_veto_below")
    vetoes = [(row.left_record_id, row.right_record_id) for row in ctx.sql(
        f"""SELECT left_record_id, right_record_id FROM {x}.current_decision_v
            WHERE entity_type_id = {et} AND is_current AND decision = 'NO_LINK'
              AND score < {float(veto_below)}""").collect()] if veto_below else []

    prior: dict[str, PriorEntity] = {}
    for row in ctx.sql(f"""
            SELECT e.entity_id, e.anchor_record_id, cast(e.created_at AS STRING) created_at,
                   collect_set(m.record_id) members
            FROM {g}.entity_current e
            JOIN {x}.entity_membership_current m ON m.entity_type_id = e.entity_type_id AND m.entity_id = e.entity_id
            WHERE e.entity_type_id = {et}
            GROUP BY e.entity_id, e.anchor_record_id, e.created_at""").collect():
        prior[row.entity_id] = PriorEntity(row.entity_id, row.anchor_record_id, row.created_at or "", set(row.members))

    result = constrained_components(nodes, edges, constraints, vetoes)
    ident = assign_stable_ids(result.components, list(prior.values()))
    ctx.log(f"cluster: {len(nodes)} records, {len(edges)} LINK edges, {len(constraints)} constraints -> "
            f"{len(result.components)} entities ({len(ident.changed_entities)} changed, "
            f"{len(ident.aliases)} merged away, {len(ident.retired_entities)} retired, "
            f"{result.rejected_edges} edges blocked by constraints, {result.vetoes_applied} non-match vetoes)")

    # Membership: rewrite this entity type's slice.
    prev_member_of = {m: p.entity_id for p in prior.values() for m in p.members}
    mrows = [Row(record_id=rec, entity_id=eid, moved=prev_member_of.get(rec) != eid)
             for rec, eid in ident.membership.items()]
    ctx.view("rz_membership", mrows, "record_id STRING, entity_id STRING, moved BOOLEAN")
    ctx.sql(f"""
        CREATE OR REPLACE TABLE {x}.`_stg_mem_{ctx.run_id[:8]}` AS
        SELECT {et} AS entity_type_id, n.entity_id, n.record_id, {ctx.lit(ctx.run_id)} AS run_id,
               CASE WHEN n.moved OR o.joined_at IS NULL THEN current_timestamp() ELSE o.joined_at END AS joined_at
        FROM rz_membership n
        LEFT JOIN {x}.entity_membership_current o ON o.entity_type_id = {et} AND o.record_id = n.record_id""")
    ctx.sql(f"DELETE FROM {x}.entity_membership_current WHERE entity_type_id = {et}")
    ctx.sql(f"INSERT INTO {x}.entity_membership_current SELECT * FROM {x}.`_stg_mem_{ctx.run_id[:8]}`")
    ctx.sql(f"DROP TABLE IF EXISTS {x}.`_stg_mem_{ctx.run_id[:8]}`")

    if ident.events:
        # Attribute each event to the steward commands whose records it involves.
        new_members: dict[str, set[str]] = {}
        for rec, eid in ident.membership.items():
            new_members.setdefault(eid, set()).add(rec)

        def commands_for(e) -> str | None:
            recs: set[str] = set()
            for eid in e.source_entity_ids:
                recs |= prior[eid].members if eid in prior else set()
            for eid in e.target_entity_ids:
                recs |= new_members.get(eid, set())
            ids = [cid for cid in ctx.command_ids if ctx.command_records.get(cid, set()) & recs]
            return ",".join(ids) or None

        erows = [Row(event_id=new_id(), event_type=e.event_type, entity_id=e.entity_id,
                     source_entity_ids=e.source_entity_ids, target_entity_ids=e.target_entity_ids,
                     command_id=commands_for(e))
                 for e in ident.events]
        ctx.view("rz_events", erows, "event_id STRING, event_type STRING, entity_id STRING, "
                 "source_entity_ids ARRAY<STRING>, target_entity_ids ARRAY<STRING>, command_id STRING")
        ctx.sql(f"""INSERT INTO {x}.entity_event
                    SELECT event_id, {et}, event_type, entity_id, source_entity_ids, target_entity_ids,
                           {ctx.lit(ctx.run_id)}, command_id, current_timestamp() FROM rz_events""")
    if ident.aliases:
        arows = [Row(old=a, cur=b, reason=why) for a, b, why in ident.aliases]
        ctx.view("rz_aliases", arows, "old STRING, cur STRING, reason STRING")
        # Re-point older aliases at the new survivor so lookups are always one hop.
        ctx.sql(f"""MERGE INTO {x}.entity_alias t USING rz_aliases a
                    ON t.entity_type_id = {et} AND t.current_entity_id = a.old
                    WHEN MATCHED THEN UPDATE SET current_entity_id = a.cur""")
        ctx.sql(f"""INSERT INTO {x}.entity_alias
                    SELECT {et}, old, cur, reason, {ctx.lit(ctx.run_id)}, current_timestamp() FROM rz_aliases""")

    ctx.cluster_result = ident
    counts = {}
    for e in ident.events:
        counts[e.event_type] = counts.get(e.event_type, 0) + 1
    ctx.metric(entities=len(result.components), entities_changed=len(ident.changed_entities),
               entity_events=counts, edges_blocked_by_constraints=result.rejected_edges,
               no_link_vetoes=result.vetoes_applied)
