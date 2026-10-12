"""Lakebase access for the engine (operational state only).

The engine reads drafts at PUBLISH time and pending commands, and reports run
progress / command status. All MDM state it computes goes to Delta.
The schema and tables are owned by the app service principal (created at app
startup); the engine identity is granted DML on them.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any

import psycopg
from databricks.sdk import WorkspaceClient

from .config import EngineConfig


class Lakebase:
    def __init__(self, cfg: EngineConfig, ws: WorkspaceClient):
        self.cfg = cfg
        self.ws = ws
        self._user = None
        self._host = None

    def _conninfo(self) -> dict[str, Any]:
        if self._host is None:
            ep = self.ws.postgres.get_endpoint(name=self.cfg.lakebase_endpoint)
            self._host = ep.status.hosts.host
            me = self.ws.current_user.me()
            # Service principals authenticate with their application id; users with their email.
            self._user = me.user_name if "@" in (me.user_name or "") else (me.application_id or me.user_name)
        cred = self.ws.postgres.generate_database_credential(endpoint=self.cfg.lakebase_endpoint)
        return dict(
            host=self._host,
            port=5432,
            dbname=self.cfg.pg_database,
            user=self._user,
            password=cred.token,
            sslmode="require",
            connect_timeout=30,
        )

    @contextmanager
    def conn(self):
        with psycopg.connect(**self._conninfo(), autocommit=False) as c:
            with c.cursor() as cur:
                cur.execute(f'SET search_path TO "{self.cfg.pg_schema}"')
            yield c

    def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        with self.conn() as c, c.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self.conn() as c, c.cursor() as cur:
            cur.execute(sql, params)
            c.commit()
            return cur.rowcount

    # ── drafts / publish ─────────────────────────────────────────────────

    def load_drafts(self, entity_type_id: str) -> tuple[dict, dict | None]:
        row = self.fetchone(
            """SELECT t.id, t.name, t.slug, t.entity_kind, t.description,
                      d.content AS definition, s.content AS strategy
               FROM entity_type t
               LEFT JOIN entity_definition_draft d ON d.entity_type_id = t.id
               LEFT JOIN strategy_draft s ON s.entity_type_id = t.id
               WHERE t.id = %s""",
            (entity_type_id,),
        )
        if row is None:
            raise ValueError(f"entity type {entity_type_id} not found in Lakebase")
        definition = dict(row["definition"] or {})
        definition.update(
            entity_type_id=row["id"],
            name=row["name"],
            slug=row["slug"],
            entity_kind=row["entity_kind"],
            description=row["description"] or "",
        )
        return definition, row["strategy"]

    def mark_published(self, entity_type_id: str, def_version: int, strategy_version: int) -> None:
        self.execute(
            """UPDATE entity_type SET published_version = %s, strategy_version = %s,
                      status = 'PUBLISHED', updated_at = now() WHERE id = %s""",
            (def_version, strategy_version, entity_type_id),
        )

    # ── runs ─────────────────────────────────────────────────────────────

    def run_update(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        sets, vals = [], []
        for k, v in fields.items():
            if k in ("progress", "metrics", "params"):
                sets.append(f"{k} = COALESCE({k}, '{{}}'::jsonb) || %s::jsonb")
                vals.append(json.dumps(v))
            elif v == "now()":
                sets.append(f"{k} = now()")
            else:
                sets.append(f"{k} = %s")
                vals.append(v)
        vals.append(run_id)
        self.execute(f"UPDATE run_request SET {', '.join(sets)} WHERE run_id = %s", tuple(vals))

    def ensure_run(self, run_id: str, entity_type_id: str, mode: str, trigger_source: str, job_run_id: str) -> None:
        self.execute(
            """INSERT INTO run_request (run_id, entity_type_id, mode, status, trigger_source, job_run_id, requested_by)
               VALUES (%s, %s, %s, 'RUNNING', %s, %s, 'engine')
               ON CONFLICT (run_id) DO UPDATE SET status = 'RUNNING', job_run_id = EXCLUDED.job_run_id,
                                                  started_at = now()""",
            (run_id, entity_type_id, mode, trigger_source, job_run_id),
        )

    def steward_work_waiting(self, run_id: str) -> bool:
        """True when a steward command (any entity type) is waiting on an engine run queued behind this one."""
        row = self.fetchone(
            """SELECT 1 AS waiting FROM resolution_command c
               JOIN run_request r ON r.entity_type_id = c.entity_type_id
               WHERE c.status = 'PENDING' AND r.status = 'PENDING' AND r.run_id <> %s
                 AND r.created_at > now() - interval '1 day'
               LIMIT 1""",
            (run_id,),
        )
        return row is not None

    def queue_run(self, run_id: str, entity_type_id: str, mode: str, trigger_source: str, params: dict) -> None:
        self.execute(
            """INSERT INTO run_request (run_id, entity_type_id, mode, status, params, requested_by, trigger_source)
               VALUES (%s, %s, %s, 'PENDING', %s::jsonb, 'engine', %s)""",
            (run_id, entity_type_id, mode, json.dumps(params), trigger_source),
        )

    # ── commands ────────────────────────────────────────────────────────

    def claim_commands(self, entity_type_id: str, run_id: str) -> list[dict]:
        with self.conn() as c, c.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                """UPDATE resolution_command SET status = 'RUNNING', run_id = %s, started_at = now()
                   WHERE id IN (SELECT id FROM resolution_command
                                WHERE entity_type_id = %s AND status = 'PENDING'
                                ORDER BY requested_at FOR UPDATE SKIP LOCKED)
                   RETURNING id, command_type, payload, requested_by, requested_at""",
                (run_id, entity_type_id),
            )
            rows = cur.fetchall()
            c.commit()
        return sorted(rows, key=lambda r: r["requested_at"])

    def finish_command(self, command_id: str, ok: bool, error: str | None = None) -> None:
        self.execute(
            """UPDATE resolution_command SET status = %s, error = %s, applied_at = now() WHERE id = %s""",
            ("APPLIED" if ok else "FAILED", error, command_id),
        )
