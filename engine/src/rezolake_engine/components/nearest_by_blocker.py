"""Delta-native ANN blocker: record -> top-k nearest records via one NEAREST BY join.

Reads embeddings straight from match_representation, so no AI Search index or
endpoint is needed and there is no index sync to wait on. block_score is cosine
similarity. (AI Search reports 1 / (1 + L2^2) = 1 / (3 - 2 cos) for normalized
vectors; the two scales agree only at 0.5, the default threshold.)
"""

from __future__ import annotations

from .base import CandidatePair


class NearestByBlocker:
    component_id = "nearest_by_blocker"
    needs_query_vectors = False  # the join reads embeddings itself; the work set only supplies ids

    def __init__(self, version: str, config: dict):
        self.version = version
        self.num_results = int(config.get("num_results", 10))
        self.score_threshold = float(config.get("score_threshold", 0.5))
        self.search = "APPROX" if str(config.get("search", "exact")).lower() == "approx" else "EXACT"
        if not 1 <= self.num_results < 100_000:  # NEAREST BY allows 1..100000 and we ask for one extra (self)
            raise ValueError("num_results must be between 1 and 99999")

    @property
    def ref(self) -> str:
        return f"{self.component_id}_v{self.version}"

    def prepare(self, ctx) -> None:
        """Nothing to build: the join reads match_representation directly."""

    def sql(self, records: str, entity_type_lit: str) -> str:
        """Candidate query over the ``rz_block_queries`` (record_id) view."""
        live = f"""SELECT record_id, embedding FROM {records}.match_representation
                   WHERE entity_type_id = {entity_type_lit} AND NOT is_deleted AND embedding IS NOT NULL"""
        # The query record is its own nearest neighbour, so fetch k + 1 and drop it.
        return f"""
            WITH q AS (
              SELECT m.record_id, m.embedding FROM rz_block_queries w JOIN ({live}) m ON m.record_id = w.record_id
            ),
            nn AS (
              SELECT q.record_id AS query_id, t.record_id AS cand_id,
                     vector_cosine_similarity(q.embedding, t.embedding) AS score
              FROM q JOIN ({live}) t
                {self.search} NEAREST {self.num_results + 1} BY SIMILARITY vector_cosine_similarity(q.embedding, t.embedding)
            ),
            ranked AS (
              SELECT query_id, cand_id, score,
                     row_number() OVER (PARTITION BY query_id ORDER BY score DESC, cand_id) AS rank
              FROM nn WHERE cand_id <> query_id AND score >= {self.score_threshold}
            )
            SELECT query_id, cand_id, score, rank FROM ranked WHERE rank <= {self.num_results}"""

    def generate_candidates(self, ctx, queries: dict) -> list[CandidatePair]:
        """queries: record_id -> embedding (unused; ids only)."""
        ctx.view("rz_block_queries", [{"record_id": r} for r in queries], "record_id STRING")
        rows = ctx.sql(self.sql(ctx.cfg.records, ctx.lit(ctx.entity_type_id))).collect()
        return [CandidatePair(r.query_id, r.cand_id, int(r.rank), float(r.score), f"nearest-by cosine-rank {r.rank}")
                for r in rows]
