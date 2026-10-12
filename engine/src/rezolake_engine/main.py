"""Rezolake engine entry point (python_wheel_task ``main``).

Modes:
  SETUP        create Delta contracts, grants, trace experiment
  PUBLISH      freeze Lakebase drafts into Delta versions
  INCREMENTAL  apply commands, ingest CDF, resolve only what changed
  FULL         apply commands, snapshot sources, re-embed and rebuild entities; still re-matches only
               changed / never-resolved records unless rematch_all=true
  LIMITED      INCREMENTAL with a work-set cap (max_records)
  COMMAND      apply commands, then resolve/cluster/survive without ingesting

rematch_all=true (any resolve mode) re-matches every live record with the current strategy.
"""

from __future__ import annotations

import argparse
import sys
import traceback
import uuid

from databricks.sdk import WorkspaceClient

from . import ddl
from .config import EngineConfig
from .lakebase import Lakebase
from .stages.context import RunContext

RESOLVE_MODES = {"INCREMENTAL", "FULL", "LIMITED", "COMMAND"}


def _parse(argv: list[str]) -> dict[str, str]:
    p = argparse.ArgumentParser()
    for name in ("mode", "entity_type_id", "run_id", "catalog", "lakebase_endpoint", "pg_database", "pg_schema",
                 "vs_endpoint", "app_sp", "trace_experiment", "max_records", "trigger_source", "requested_by",
                 "job_run_id", "rematch_all"):
        p.add_argument(f"--{name}", default="")
    args, _unknown = p.parse_known_args(argv)
    return {k: v for k, v in vars(args).items() if v not in (None, "")}


def _spark():
    import os

    if os.environ.get("REZOLAKE_LOCAL"):  # dev loop: run the engine locally against serverless compute
        from databricks.connect import DatabricksSession

        return DatabricksSession.builder.serverless().getOrCreate()
    from pyspark.sql import SparkSession

    return SparkSession.builder.getOrCreate()


def setup(ctx: RunContext) -> None:
    # Tables first, then add any newer columns, then the views that read them.
    stmts = ddl.statements(ctx.cfg)
    is_view = lambda s: s.lstrip().startswith("CREATE OR REPLACE VIEW")  # noqa: E731
    for stmt in stmts:
        if not is_view(stmt):
            ctx.sql(stmt)
    ddl.migrate(ctx)
    for stmt in stmts:
        if is_view(stmt):
            ctx.sql(stmt)
    for stmt in ddl.grants(ctx.cfg):
        try:
            ctx.sql(stmt)
        except Exception as e:
            ctx.log(f"grant skipped ({e.__class__.__name__}): {stmt}: {str(e)[:200]}")
    try:
        import mlflow

        mlflow.set_tracking_uri("databricks")
        ctx.ws.workspace.mkdirs("/".join(ctx.cfg.trace_experiment.split("/")[:-1]))
        mlflow.set_experiment(ctx.cfg.trace_experiment)
    except Exception as e:
        ctx.log(f"trace experiment setup skipped: {e}")
    ctx.log("setup complete")


def _setup_tracing(ctx: RunContext) -> None:
    try:
        import mlflow

        mlflow.set_tracking_uri("databricks")
        mlflow.set_experiment(ctx.cfg.trace_experiment)
    except Exception as e:
        ctx.log(f"tracing disabled: {e}")


def resolve_run(ctx: RunContext, requested_by: str) -> None:
    from .stages import canonicalize, cluster, commands, ingest, publish, represent, resolve, survive

    ddl.migrate(ctx)
    publish.load_published(ctx)
    publish.write_manifest(ctx, requested_by)
    _setup_tracing(ctx)
    commands.apply_commands(ctx)
    if ctx.mode != "COMMAND":
        ingest.ingest(ctx)
    canonicalize.canonicalize(ctx)
    represent.represent(ctx)
    resolve.resolve(ctx)
    cluster.cluster(ctx)
    survive.survive(ctx)
    ingest.commit_checkpoints(ctx)
    if ctx.yielded:
        _queue_continuation(ctx)


def _queue_continuation(ctx: RunContext) -> None:
    """After yielding to steward work, queue an INCREMENTAL run that scores the deferred records (they carry a
    cleared blocked_hash). It lands behind the steward run in the job queue."""
    try:
        run_id = str(uuid.uuid4())
        params = {"mode": "INCREMENTAL", "entity_type_id": ctx.entity_type_id, "run_id": run_id,
                  "trigger_source": "continuation", "requested_by": "engine"}
        if ctx.lb is not None:
            ctx.lb.queue_run(run_id, ctx.entity_type_id, "INCREMENTAL", "continuation", {"continues": ctx.run_id})
        job_id = ctx.ws.jobs.get_run(int(ctx.job_run_id)).job_id
        job_run = ctx.ws.jobs.run_now(job_id=job_id, job_parameters=params)
        if ctx.lb is not None:
            ctx.lb.run_update(run_id, job_run_id=str(job_run.run_id))
        ctx.metric(continuation_run_id=run_id)
        ctx.log(f"queued continuation run {run_id} (job run {job_run.run_id})")
    except Exception as e:  # the deferred records are still retried by the next run of any kind
        ctx.log(f"could not queue a continuation run: {e}")


def main(argv: list[str] | None = None) -> None:
    args = _parse(sys.argv[1:] if argv is None else argv)
    mode = (args.get("mode") or "INCREMENTAL").upper()
    cfg = EngineConfig.from_args(args)
    ws = WorkspaceClient()
    lb = Lakebase(cfg, ws) if cfg.lakebase_endpoint else None
    ctx = RunContext(
        spark=_spark(), ws=ws, cfg=cfg, lb=lb,
        run_id=args.get("run_id") or str(uuid.uuid4()),
        entity_type_id=args.get("entity_type_id", ""),
        mode=mode,
        trigger_source=args.get("trigger_source", "manual"),
        job_run_id=args.get("job_run_id", ""),
        max_records=int(args["max_records"]) if args.get("max_records") else None,
        rematch_all=args.get("rematch_all", "").lower() == "true",
    )
    ctx.log(f"mode={mode} entity_type={ctx.entity_type_id} run={ctx.run_id} rematch_all={ctx.rematch_all}")

    if mode == "SETUP":
        setup(ctx)
        return
    if not ctx.entity_type_id:
        raise ValueError("entity_type_id is required")

    if lb is not None:
        lb.ensure_run(ctx.run_id, ctx.entity_type_id, mode, ctx.trigger_source, ctx.job_run_id)
    try:
        if mode == "PUBLISH":
            from .stages import publish

            for stmt in ddl.grants(cfg):  # keep the app SP able to read everything it serves
                try:
                    ctx.sql(stmt)
                except Exception as e:
                    ctx.log(f"grant skipped: {str(e)[:160]}")

            out = publish.publish(ctx, args.get("requested_by", "app"))
            ctx.metric(**out)
        elif mode in RESOLVE_MODES:
            resolve_run(ctx, args.get("requested_by", "app"))
        else:
            raise ValueError(f"unknown mode {mode}")
    except Exception as e:
        err = f"{e.__class__.__name__}: {e}"
        ctx.log("FAILED: " + err + "\n" + traceback.format_exc())
        _finish(ctx, "FAILED", err)
        raise
    _finish(ctx, "SUCCEEDED", None)
    ctx.log(f"done: {ctx.metrics}")


def _finish(ctx: RunContext, status: str, error: str | None) -> None:
    from .stages import commands, publish

    if ctx.mode in RESOLVE_MODES and ctx.definition is not None:
        try:
            publish.finish_manifest(ctx, status, error)
        except Exception as e:
            ctx.log(f"manifest finish failed: {e}")
    if ctx.lb is not None:
        try:
            commands.finish_commands(ctx, status == "SUCCEEDED", error)
            ctx.lb.run_update(ctx.run_id, status=status, error=error, metrics=ctx.metrics, completed_at="now()")
        except Exception as e:
            ctx.log(f"lakebase finish failed: {e}")


if __name__ == "__main__":
    main()
