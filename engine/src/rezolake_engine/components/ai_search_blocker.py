"""AI Search (Vector Search) ANN blocker: record -> top-k nearest records.

Owns its Delta Sync index over match_representation: ``prepare`` creates it on
first use and syncs it when representations changed, then
``generate_candidates`` queries it once per work-set record. Needs the AI
Search endpoint (``vs_endpoint``; bootstrap creates it with --with-ai-search).
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

from databricks.sdk.service.vectorsearch import (
    DeltaSyncVectorIndexSpecRequest,
    EmbeddingVectorColumn,
    PipelineType,
    VectorIndexType,
)

from .base import CandidatePair


class AISearchBlocker:
    component_id = "ai_search_blocker"
    needs_query_vectors = True

    def __init__(self, version: str, config: dict):
        self.version = version
        self.num_results = int(config.get("num_results", 10))
        self.score_threshold = float(config.get("score_threshold", 0.5))
        self.concurrency = int(config.get("concurrency", 16))
        self.sync_timeout_s = int(config.get("sync_timeout_s", 1800))

    @property
    def ref(self) -> str:
        return f"{self.component_id}_v{self.version}"

    # ── index ────────────────────────────────────────────────────────
    def prepare(self, ctx) -> None:
        existed = self._ensure_index(ctx)
        if existed and not ctx.representations_changed:
            ctx.log("AI Search index up to date (no representation changes)")
            return
        self._sync_index(ctx, existed)

    def _embedding_dim(self, ctx) -> int:
        row = ctx.sql(f"""SELECT size(embedding) d FROM {ctx.cfg.records}.match_representation
                          WHERE embedding IS NOT NULL LIMIT 1""").first()
        return int(row.d) if row else 1024

    def _ensure_index(self, ctx) -> bool:
        """Create the Delta Sync index if missing. Returns True if it already existed."""
        try:
            ctx.ws.vector_search_indexes.get_index(ctx.cfg.vs_index)
            return True
        except Exception as e:  # NotFound
            if "not found" not in str(e).lower() and "does not exist" not in str(e).lower():
                raise
        ctx.log(f"creating AI Search index {ctx.cfg.vs_index} on {ctx.cfg.vs_endpoint}")
        ctx.ws.vector_search_indexes.create_index(
            name=ctx.cfg.vs_index,
            endpoint_name=ctx.cfg.vs_endpoint,
            primary_key="record_id",
            index_type=VectorIndexType.DELTA_SYNC,
            delta_sync_index_spec=DeltaSyncVectorIndexSpecRequest(
                source_table=ctx.cfg.match_table_uc,
                pipeline_type=PipelineType.TRIGGERED,
                embedding_vector_columns=[EmbeddingVectorColumn(name="embedding",
                                                                embedding_dimension=self._embedding_dim(ctx))],
                columns_to_sync=["record_id", "entity_type_id", "match_text", "is_deleted"],
            ),
        )
        return False

    def _sync_index(self, ctx, existed: bool) -> None:
        ctx.stage("index_sync")
        expected = ctx.sql(f"""SELECT count(*) n FROM {ctx.cfg.records}.match_representation
                               WHERE embedding IS NOT NULL""").first().n
        started = time.time()
        triggered = False
        while True:
            idx = ctx.ws.vector_search_indexes.get_index(ctx.cfg.vs_index)
            st = idx.status
            ready = bool(st and st.ready)
            if existed and ready and not triggered:
                try:
                    ctx.ws.vector_search_indexes.sync_index(ctx.cfg.vs_index)
                    triggered = True
                    time.sleep(20)
                    continue
                except Exception as e:
                    ctx.log(f"sync_index: {e}")
            indexed = (st.indexed_row_count or 0) if st else 0
            msg = (st.message or "") if st else ""
            if ready and indexed >= expected and (triggered or not existed) and "pending" not in msg.lower():
                ctx.log(f"index ready: {indexed}/{expected} rows")
                return
            if time.time() - started > self.sync_timeout_s:
                raise TimeoutError(f"AI Search index not synced after {self.sync_timeout_s}s ({indexed}/{expected}): {msg}")
            ctx.log(f"waiting for index: ready={ready} indexed={indexed}/{expected} {msg[:120]}")
            time.sleep(15)

    # ── candidates ───────────────────────────────────────────────────
    def generate_candidates(self, ctx, queries: dict[str, list[float]]) -> list[CandidatePair]:
        """queries: record_id -> normalized embedding."""
        filters = json.dumps({"entity_type_id": ctx.entity_type_id, "is_deleted": False})

        def one(item):
            rid, vec = item
            for attempt in range(4):
                try:
                    resp = ctx.ws.vector_search_indexes.query_index(
                        index_name=ctx.cfg.vs_index,
                        columns=["record_id"],
                        query_vector=list(vec),
                        num_results=self.num_results + 1,
                        filters_json=filters,
                    )
                    break
                except Exception as e:
                    if attempt == 3:
                        raise
                    ctx.log(f"vs query retry {attempt + 1} for {rid[:8]}: {e}")
            cols = [c.name for c in resp.manifest.columns]
            rows = resp.result.data_array or [] if resp.result else []
            out, rank = [], 0
            for row in rows:
                rec = dict(zip(cols, row))
                cand, score = rec["record_id"], float(rec.get("score", 0))
                if cand == rid or score < self.score_threshold:
                    continue
                rank += 1
                out.append(CandidatePair(rid, cand, rank, score, f"ann cosine-rank {rank}"))
            return out

        pairs: list[CandidatePair] = []
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            for i, res in enumerate(pool.map(one, queries.items()), 1):
                pairs.extend(res)
                if i % 200 == 0:
                    ctx.log(f"blocked {i}/{len(queries)} records")
        return pairs
