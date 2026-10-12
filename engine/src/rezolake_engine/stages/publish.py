"""PUBLISH: freeze Lakebase drafts into immutable Delta versions; load for runs."""

from __future__ import annotations

import json

from ..definition import EntityDefinition, Strategy
from .context import RunContext


def _latest(ctx: RunContext, table: str, col: str):
    row = ctx.sql(
        f"""SELECT version, {col} AS doc FROM {ctx.cfg.meta}.{table}
            WHERE entity_type_id = {ctx.lit(ctx.entity_type_id)} ORDER BY version DESC LIMIT 1"""
    ).first()
    return (row.version, row.doc) if row else (0, None)


def publish(ctx: RunContext, published_by: str = "app") -> dict:
    assert ctx.lb is not None, "PUBLISH needs Lakebase"
    ctx.stage("publish")
    definition_doc, strategy_doc = ctx.lb.load_drafts(ctx.entity_type_id)
    definition = EntityDefinition.from_dict(definition_doc)  # validates
    strategy = Strategy.from_dict(strategy_doc)
    if not definition.active_sources:
        raise ValueError("publish needs at least one active source")
    _validate_sources(ctx, definition)

    out = {}
    for table, col, doc in (
        ("entity_definition_version", "definition", definition.to_json()),
        ("strategy_version", "strategy", strategy.to_json()),
    ):
        version, latest = _latest(ctx, table, col)
        if latest is not None and json.loads(latest) == json.loads(doc):
            out[table] = version
            continue
        version += 1
        extra_cols = ", slug, name" if table == "entity_definition_version" else ""
        extra_vals = f", {ctx.lit(definition.slug)}, {ctx.lit(definition.name)}" if extra_cols else ""
        ctx.sql(
            f"""INSERT INTO {ctx.cfg.meta}.{table}
                (entity_type_id, version{extra_cols}, {col}, published_by, published_at)
                VALUES ({ctx.lit(ctx.entity_type_id)}, {version}{extra_vals}, {ctx.lit(doc)},
                        {ctx.lit(published_by)}, current_timestamp())"""
        )
        out[table] = version
    ctx.lb.mark_published(ctx.entity_type_id, out["entity_definition_version"], out["strategy_version"])
    ctx.log(f"published definition v{out['entity_definition_version']} strategy v{out['strategy_version']}")
    return out


def _validate_sources(ctx: RunContext, d: EntityDefinition) -> None:
    for s in d.active_sources:
        cols = {f.name for f in ctx.spark.table(s.quoted_table).schema.fields}
        missing = [c for c in s.primary_key + list(s.mappings) if c not in cols]
        if missing:
            raise ValueError(f"source {s.source_id}: columns not in {s.table}: {missing}")


def load_published(ctx: RunContext) -> None:
    dv, ddoc = _latest(ctx, "entity_definition_version", "definition")
    if ddoc is None:
        raise ValueError(f"entity type {ctx.entity_type_id} has no published definition")
    sv, sdoc = _latest(ctx, "strategy_version", "strategy")
    ctx.definition = EntityDefinition.from_json(ddoc, version=dv)
    ctx.strategy = Strategy.from_dict(json.loads(sdoc) if sdoc else None, version=sv)


def write_manifest(ctx: RunContext, requested_by: str) -> None:
    d, s = ctx.definition, ctx.strategy
    manifest = {
        "entity_definition_version": d.version,
        "strategy_version": s.version,
        "blocker": s.blocker.ref,
        "matchers": [m.ref for m in s.matchers],
        "decision_policy": s.decision.ref,
        "clusterer": s.clusterer.ref,
        "survivorship": s.survivorship.ref,
        "embedding_endpoint": s.embedding_endpoint,
        "mode": ctx.mode,
        "max_records": ctx.max_records,
        "rematch_all": ctx.rematch_all,
        "sources": [src.source_id for src in d.active_sources],
    }
    ctx.sql(
        f"""INSERT INTO {ctx.cfg.meta}.resolution_run
            (run_id, entity_type_id, mode, trigger_source, requested_by, job_run_id, manifest, status, started_at)
            VALUES ({ctx.lit(ctx.run_id)}, {ctx.lit(ctx.entity_type_id)}, {ctx.lit(ctx.mode)},
                    {ctx.lit(ctx.trigger_source)}, {ctx.lit(requested_by)}, {ctx.lit(ctx.job_run_id)},
                    {ctx.lit(json.dumps(manifest))}, 'RUNNING', current_timestamp())"""
    )


def finish_manifest(ctx: RunContext, status: str, error: str | None = None) -> None:
    ctx.sql(
        f"""UPDATE {ctx.cfg.meta}.resolution_run
            SET status = {ctx.lit(status)}, error = {ctx.lit(error or '')},
                metrics = {ctx.lit(json.dumps(ctx.metrics, default=str))}, completed_at = current_timestamp()
            WHERE run_id = {ctx.lit(ctx.run_id)}"""
    )
