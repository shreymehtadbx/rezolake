"""Engine configuration resolved from job parameters (with env fallbacks)."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class EngineConfig:
    catalog: str
    lakebase_endpoint: str  # projects/<p>/branches/<b>/endpoints/<e>
    pg_database: str
    pg_schema: str
    vs_endpoint: str
    app_sp: str  # app service principal application id (grants)
    trace_experiment: str

    @property
    def meta(self) -> str:
        return f"`{self.catalog}`.rezolake2_meta"

    @property
    def records(self) -> str:
        return f"`{self.catalog}`.rezolake2_records"

    @property
    def resolution(self) -> str:
        return f"`{self.catalog}`.rezolake2_resolution"

    @property
    def master(self) -> str:
        return f"`{self.catalog}`.rezolake2_master"

    @property
    def vs_index(self) -> str:
        # UC three-level name, unquoted, for the Vector Search API.
        return f"{self.catalog}.rezolake2_records.match_representation_index"

    @property
    def match_table_uc(self) -> str:
        return f"{self.catalog}.rezolake2_records.match_representation"

    @classmethod
    def from_args(cls, args: dict[str, str]) -> "EngineConfig":
        def get(name: str, default: str = "") -> str:
            v = args.get(name) or os.environ.get(f"REZOLAKE_{name.upper()}") or default
            return v

        cfg = cls(
            catalog=get("catalog"),
            lakebase_endpoint=get("lakebase_endpoint"),
            pg_database=get("pg_database", "databricks_postgres"),
            pg_schema=get("pg_schema", "rezolake_v2"),
            vs_endpoint=get("vs_endpoint", "rezolake-vs-endpoint"),
            app_sp=get("app_sp"),
            trace_experiment=get("trace_experiment", "/Shared/Rezolake2/traces"),
        )
        if not cfg.catalog:
            raise ValueError("catalog is required")
        return cfg
