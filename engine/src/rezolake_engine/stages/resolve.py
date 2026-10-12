"""RESOLVE: work set -> candidate pairs (blocker) -> pair scores (matchers) -> decisions (policy).

Work set = live records whose canonical hash hasn't been blocked yet (or FULL,
or explicit REMATCH). Scores are reused when a matcher/version already scored
the same pair at the same record hashes, so reruns cost nothing extra.
"""

from __future__ import annotations

import json
from collections import defaultdict

from pyspark.sql import Row

from ..components import registry
from ..components.base import ScoredPair
from ..ids import ordered, pair_id
from .context import RunContext


def _work_set(ctx: RunContext, with_vectors: bool) -> list[Row]:
    et = ctx.lit(ctx.entity_type_id)
    rematch = ", ".join(ctx.lit(r) for r in sorted(ctx.rematch_records)) or "NULL"
    # Only records that changed (or were never resolved) are re-matched, so publishing a new strategy never
    # rewrites settled matches. rematch_all re-matches every live record with the current strategy.
    every = "TRUE" if ctx.rematch_all else "FALSE"
    limit = f"LIMIT {int(ctx.max_records)}" if ctx.max_records else ""
    vec = "m.embedding" if with_vectors else "CAST(NULL AS ARRAY<FLOAT>) AS embedding"
    return ctx.sql(f"""
        SELECT m.record_id, {vec}, c.record_hash
        FROM {ctx.cfg.records}.match_representation m
        JOIN {ctx.cfg.records}.canonical_record_current c
          ON c.entity_type_id = m.entity_type_id AND c.record_id = m.record_id
        LEFT JOIN {ctx.cfg.resolution}.record_match_state s
          ON s.entity_type_id = m.entity_type_id AND s.record_id = m.record_id
        WHERE m.entity_type_id = {et} AND NOT c.is_deleted AND m.embedding IS NOT NULL
          AND m.canonical_hash = c.record_hash
          AND ({every} OR s.blocked_hash IS DISTINCT FROM c.record_hash OR m.record_id IN ({rematch}))
        ORDER BY c.updated_at, m.record_id {limit}""").collect()


def resolve(ctx: RunContext) -> None:
    ctx.stage("block")
    et, run = ctx.entity_type_id, ctx.run_id
    blocker = registry.blocker(ctx.strategy.blocker)
    work = _work_set(ctx, with_vectors=blocker.needs_query_vectors)
    ctx.metric(work_set=len(work))
    if not work:
        ctx.log("resolve: empty work set")
        return

    blocker.prepare(ctx)
    ctx.stage("block")
    cands = blocker.generate_candidates(ctx, {r.record_id: r.embedding for r in work})
    ctx.log(f"block: {len(work)} records -> {len(cands)} raw candidates")

    # Normalize + dedupe (keep best rank / score per pair, remember who queried it).
    pairs: dict[str, dict] = {}
    for c in cands:
        left, right = ordered(c.query_record_id, c.candidate_record_id)
        pid = pair_id(left, right)
        cur = pairs.get(pid)
        if cur is None or c.block_score > cur["block_score"]:
            pairs[pid] = dict(pair_id=pid, left=left, right=right, query=c.query_record_id,
                              cand=c.candidate_record_id, rank=c.rank, block_score=c.block_score,
                              reason=c.block_reason)
    involved = {p["left"] for p in pairs.values()} | {p["right"] for p in pairs.values()}
    info = _record_info(ctx, involved)
    pairs = {k: p for k, p in pairs.items() if p["left"] in info and p["right"] in info}
    ctx.metric(candidate_pairs=len(pairs))

    if pairs:
        rows = [Row(entity_type_id=et, run_id=run, pair_id=p["pair_id"], left_record_id=p["left"],
                    right_record_id=p["right"], query_record_id=p["query"], left_hash=info[p["left"]][0],
                    right_hash=info[p["right"]][0], blocker_id=blocker.component_id,
                    blocker_version=blocker.version, candidate_rank=int(p["rank"]),
                    block_score=float(p["block_score"]), block_reason=p["reason"]) for p in pairs.values()]
        ctx.view("rz_candidates", rows,
                 "entity_type_id STRING, run_id STRING, pair_id STRING, left_record_id STRING, "
                 "right_record_id STRING, query_record_id STRING, left_hash STRING, right_hash STRING, "
                 "blocker_id STRING, blocker_version STRING, candidate_rank INT, block_score DOUBLE, "
                 "block_reason STRING")
        ctx.sql(f"""INSERT INTO {ctx.cfg.resolution}.candidate_pair
                    SELECT entity_type_id, run_id, pair_id, left_record_id, right_record_id, query_record_id,
                           left_hash, right_hash, blocker_id, blocker_version, candidate_rank, block_score,
                           block_reason, current_timestamp() FROM rz_candidates""")

    # ── score ─────────────────────────────────────────────────────────
    # Scored in batches of query records, each written as soon as it is scored, so a cancelled or failed run
    # keeps its work (the next run reuses it). Between batches a long run yields to queued steward work.
    ctx.stage("match", candidate_pairs=len(pairs))
    scores_by_pair: dict[tuple[str, str], list[ScoredPair]] = defaultdict(list)
    failed_queries: set[str] = set()
    deferred_queries: set[str] = set()
    new_scores = reused = 0
    texts = {r: info[r][1] for r in involved if r in info}
    for mref in ctx.strategy.matchers:
        m = registry.matcher(mref, ctx.definition)
        existing = _existing_scores(ctx, m) if pairs else {}
        todo = [p for p in pairs.values() if p["pair_id"] not in existing]
        for pid, sp in existing.items():
            sp.matcher = m.ref
            scores_by_pair[ordered(sp.left_record_id, sp.right_record_id)].append(sp)
        reused += len(existing)
        groups: dict[str, list[str]] = defaultdict(list)
        for p in sorted(todo, key=lambda p: (p["query"], p["rank"])):
            groups[p["query"]].append(p["cand"])
        items = list(groups.items())
        ctx.log(f"match[{m.ref}]: {len(todo)} pairs to score in {len(groups)} calls, {len(existing)} reused")
        for start in range(0, len(items), SCORE_BATCH):
            if start and _should_yield(ctx):
                deferred_queries |= {q for q, _ in items[start:]}
                ctx.yielded = True
                ctx.log(f"match[{m.ref}]: yielding to queued steward work; {len(items) - start} records deferred")
                break
            batch = dict(items[start:start + SCORE_BATCH])
            new_scores += _score_batch(ctx, m, batch, texts, info, scores_by_pair, failed_queries)
            done_calls = min(start + SCORE_BATCH, len(items))
            ctx.stage("match", candidate_pairs=len(pairs), records_scored=done_calls, records_to_score=len(items))
        if ctx.yielded:
            break
    if deferred_queries:
        # Deferred records get no decisions this run; the continuation run scores and decides them.
        for p in pairs.values():
            if p["query"] in deferred_queries:
                scores_by_pair.pop((p["left"], p["right"]), None)
    ctx.metric(pairs_scored=new_scores, scores_reused=reused, llm_failed_records=len(failed_queries),
               deferred_records=len(deferred_queries))
    failed_queries |= deferred_queries  # both are retried by the next run

    # ── decide ────────────────────────────────────────────────────────
    ctx.stage("decide", pairs=len(scores_by_pair))
    policy = registry.decision_policy(ctx.strategy.decision)
    decisions = policy.decide(dict(scores_by_pair))
    if decisions:
        # Provenance: every decision names the strategy version, blocker and the exact scores behind it
        # (reused scores keep the run that produced them).
        def scores_json(d) -> str:
            return json.dumps([{"matcher": sp.matcher, "score": round(float(sp.score), 4), "run_id": sp.score_run_id}
                               for sp in scores_by_pair[(d.left_record_id, d.right_record_id)]])

        drows = [Row(entity_type_id=et, run_id=run, pair_id=pair_id(d.left_record_id, d.right_record_id),
                     left_record_id=d.left_record_id, right_record_id=d.right_record_id,
                     left_hash=info[d.left_record_id][0], right_hash=info[d.right_record_id][0],
                     decision=d.decision, decision_source="POLICY", score=float(d.score),
                     policy_id=policy.component_id, policy_version=policy.version, reason=d.reason,
                     strategy_version=int(ctx.strategy.version), blocker=blocker.ref, scores=scores_json(d))
                 for d in decisions]
        ctx.view("rz_decisions", drows,
                 "entity_type_id STRING, run_id STRING, pair_id STRING, left_record_id STRING, "
                 "right_record_id STRING, left_hash STRING, right_hash STRING, decision STRING, "
                 "decision_source STRING, score DOUBLE, policy_id STRING, policy_version STRING, reason STRING, "
                 "strategy_version INT, blocker STRING, scores STRING")
        ctx.sql(f"""INSERT INTO {ctx.cfg.resolution}.resolution_decision
                      (entity_type_id, run_id, pair_id, left_record_id, right_record_id, left_hash, right_hash,
                       decision, decision_source, score, policy_id, policy_version, reason, created_at,
                       strategy_version, blocker, scores)
                    SELECT entity_type_id, run_id, pair_id, left_record_id, right_record_id, left_hash, right_hash,
                           decision, decision_source, score, policy_id, policy_version, reason, current_timestamp(),
                           strategy_version, blocker, scores
                    FROM rz_decisions""")
    counts = defaultdict(int)
    for d in decisions:
        counts[d.decision] += 1
    ctx.metric(decisions_link=counts["LINK"], decisions_review=counts["REVIEW"], decisions_no_link=counts["NO_LINK"])

    # ── mark work set as resolved at its current hash ─────────────────
    done = [Row(record_id=r.record_id, blocked_hash=r.record_hash) for r in work if r.record_id not in failed_queries]
    if done:
        ctx.view("rz_done", done, "record_id STRING, blocked_hash STRING")
        ctx.sql(f"""
            MERGE INTO {ctx.cfg.resolution}.record_match_state t
            USING rz_done d ON t.entity_type_id = {ctx.lit(et)} AND t.record_id = d.record_id
            WHEN MATCHED THEN UPDATE SET blocked_hash = d.blocked_hash, blocked_run_id = {ctx.lit(run)},
                                         updated_at = current_timestamp()
            WHEN NOT MATCHED THEN INSERT (entity_type_id, record_id, blocked_hash, blocked_run_id, updated_at)
              VALUES ({ctx.lit(et)}, d.record_id, d.blocked_hash, {ctx.lit(run)},
                                                 current_timestamp())""")
    if failed_queries:
        # A record resolved by an earlier run still carries that run's blocked_hash; clear it so the
        # next INCREMENTAL run retries the scoring that failed here.
        ctx.view("rz_failed", [{"record_id": r} for r in failed_queries], "record_id STRING")
        ctx.sql(f"""
            MERGE INTO {ctx.cfg.resolution}.record_match_state t
            USING rz_failed f ON t.entity_type_id = {ctx.lit(et)} AND t.record_id = f.record_id
            WHEN MATCHED THEN UPDATE SET blocked_hash = NULL, blocked_run_id = {ctx.lit(run)},
                                         updated_at = current_timestamp()""")
    ctx.log(f"resolve: {dict(counts)}; failed or deferred records={len(failed_queries)}")


SCORE_BATCH = 200  # query records per scoring batch (one write and one yield check per batch)


def _should_yield(ctx: RunContext) -> bool:
    """Steward commands queued behind a long run go first: yield between batches (never inside a COMMAND run)."""
    if ctx.mode == "COMMAND" or ctx.lb is None:
        return False
    try:
        return ctx.lb.steward_work_waiting(ctx.run_id)
    except Exception as e:  # a progress check must never fail a run
        ctx.log(f"yield check failed: {e}")
        return False


def _score_batch(ctx: RunContext, m, batch: dict[str, list[str]], texts: dict[str, str], info: dict,
                 scores_by_pair: dict, failed_queries: set[str]) -> int:
    """Score one batch with matcher ``m`` and append its scores to pair_score. Returns rows written."""
    et, run = ctx.entity_type_id, ctx.run_id
    out_rows = []
    for sp in m.score_groups(ctx, batch, texts):
        if "error" in sp.evidence:
            failed_queries.add(sp.left_record_id)
            continue
        left, right = ordered(sp.left_record_id, sp.right_record_id)
        sp.left_record_id, sp.right_record_id = left, right
        sp.matcher, sp.score_run_id = m.ref, run
        scores_by_pair[(left, right)].append(sp)
        out_rows.append(Row(entity_type_id=et, run_id=run, pair_id=pair_id(left, right),
                            left_record_id=left, right_record_id=right, left_hash=info[left][0],
                            right_hash=info[right][0], matcher_id=m.component_id,
                            matcher_version=m.version, score=float(sp.score),
                            features=json.dumps(sp.features), evidence=json.dumps(sp.evidence),
                            trace_id=sp.trace_id or ""))
    if out_rows:
        ctx.view("rz_scores", out_rows,
                 "entity_type_id STRING, run_id STRING, pair_id STRING, left_record_id STRING, "
                 "right_record_id STRING, left_hash STRING, right_hash STRING, matcher_id STRING, "
                 "matcher_version STRING, score DOUBLE, features STRING, evidence STRING, trace_id STRING")
        ctx.sql(f"""INSERT INTO {ctx.cfg.resolution}.pair_score
                    SELECT entity_type_id, run_id, pair_id, left_record_id, right_record_id, left_hash,
                           right_hash, matcher_id, matcher_version, score, features, evidence,
                           nullif(trace_id, ''), current_timestamp() FROM rz_scores""")
    return len(out_rows)


def _record_info(ctx: RunContext, ids: set[str]) -> dict[str, tuple[str, str]]:
    """record_id -> (canonical hash, match_text) for live records."""
    if not ids:
        return {}
    ctx.view("rz_ids", [{"record_id": r} for r in ids], "record_id STRING")
    rows = ctx.sql(f"""
        SELECT c.record_id, c.record_hash, m.match_text
        FROM rz_ids i
        JOIN {ctx.cfg.records}.canonical_record_current c
          ON c.entity_type_id = {ctx.lit(ctx.entity_type_id)} AND c.record_id = i.record_id
        JOIN {ctx.cfg.records}.match_representation m ON m.record_id = c.record_id
        WHERE NOT c.is_deleted""").collect()
    return {r.record_id: (r.record_hash, r.match_text) for r in rows}


def _existing_scores(ctx: RunContext, m) -> dict[str, ScoredPair]:
    rows = ctx.sql(f"""
        SELECT s.pair_id, s.left_record_id, s.right_record_id, s.score, s.evidence, s.trace_id, s.run_id
        FROM (SELECT *, row_number() OVER (PARTITION BY pair_id ORDER BY created_at DESC) rn
              FROM {ctx.cfg.resolution}.pair_score
              WHERE entity_type_id = {ctx.lit(ctx.entity_type_id)}
                AND matcher_id = {ctx.lit(m.component_id)} AND matcher_version = {ctx.lit(m.version)}) s
        JOIN rz_candidates c ON c.pair_id = s.pair_id AND c.left_hash = s.left_hash AND c.right_hash = s.right_hash
        WHERE s.rn = 1""").collect()
    return {r.pair_id: ScoredPair(r.left_record_id, r.right_record_id, r.score,
                                  evidence=json.loads(r.evidence or "{}"), trace_id=r.trace_id,
                                  score_run_id=r.run_id) for r in rows}
