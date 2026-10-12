"""Per-run context shared by all stages."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from databricks.sdk import WorkspaceClient

from ..config import EngineConfig
from ..definition import EntityDefinition, Strategy
from ..lakebase import Lakebase


@dataclass
class RunContext:
    spark: Any
    ws: WorkspaceClient
    cfg: EngineConfig
    lb: Lakebase | None
    run_id: str
    entity_type_id: str
    mode: str
    trigger_source: str = "manual"
    job_run_id: str = ""
    max_records: int | None = None
    rematch_all: bool = False  # re-match every live record with the current strategy (opt-in)
    representations_changed: int = 0  # set by represent; lets a blocker skip refreshing its index
    yielded: bool = False  # matching stopped early for queued steward work; a continuation run is queued
    definition: EntityDefinition | None = None
    strategy: Strategy | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    # records / entities touched by commands this run
    rematch_records: set[str] = field(default_factory=set)
    override_records: set[str] = field(default_factory=set)
    override_entities: set[str] = field(default_factory=set)
    constraint_records: set[str] = field(default_factory=set)
    command_ids: list[str] = field(default_factory=list)
    # command id -> records it touched (for attributing entity events to steward actions)
    command_records: dict[str, set[str]] = field(default_factory=dict)
    _t0: float = field(default_factory=time.time)

    def log(self, msg: str) -> None:
        print(f"[rezolake {time.time() - self._t0:7.1f}s] {msg}", flush=True)

    def sql(self, statement: str):
        return self.spark.sql(statement)

    def lit(self, value: str) -> str:
        """SQL string literal (single-quote escaped)."""
        return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"

    def stage(self, name: str, **progress: Any) -> None:
        self.log(f"stage={name} {json.dumps(progress) if progress else ''}")
        if self.lb is not None:
            try:
                self.lb.run_update(self.run_id, stage=name, progress={name: progress or {"status": "running"}})
            except Exception as e:  # progress reporting must never fail a run
                self.log(f"progress update failed: {e}")

    def view(self, name: str, rows: list, schema: str) -> None:
        """Register driver-side rows as a temp view with an explicit schema (no type inference).

        ``rows`` are pyspark Rows or dicts; ``schema`` is a DDL string whose column order is used.
        """
        cols = [c.strip().split(" ", 1)[0] for c in schema.split(",")]
        data = [tuple((r.asDict() if hasattr(r, "asDict") else r).get(c) for c in cols) for r in rows]
        self.spark.createDataFrame(data, schema).createOrReplaceTempView(name)

    def metric(self, **kv: Any) -> None:
        self.metrics.update(kv)
