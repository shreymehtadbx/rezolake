"""LLM pair matcher: a DSPy EntityMatcher behind the Matcher contract.

* input is the canonical record text (matching-enabled attributes) of a query record and up to
  ``max_candidates_per_call`` candidates, aliased 1..n for the LLM and mapped back to record ids
* every candidate yields a ScoredPair — negatives are kept (hard negatives are training / eval data)
* score = P(match): confidence if is_match else 1 - confidence; the rationale is the evidence
* the prompt version is part of the component version recorded on each score
"""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List

import dspy
import mlflow
from mlflow.entities import SpanType
from pydantic import BaseModel, Field

from .base import ScoredPair
from .rules import PREAMBLE, rules_for

PROMPT_VERSION = "p2"


def _backoff_s(error: Exception, attempt: int) -> float:
    """Seconds to wait before retrying. Per-minute token/request limits need long, jittered waits."""
    msg = str(error)
    if "REQUEST_LIMIT_EXCEEDED" in msg or "RateLimit" in type(error).__name__ or "429" in msg:
        return min(10 * 2 ** attempt, 60) + random.uniform(0, 5)
    return 2 * (attempt + 1)


class MatchCandidate(BaseModel):
    candidate_id: str = Field(description="The candidate's id (1, 2, 3, ...) as given in the candidates list")
    is_match: bool = Field(description="True if the candidate is the same entity")
    confidence: float = Field(
        ge=0, le=1,
        description="How sure you are that your is_match verdict is correct, from 0.5 (coin flip) to 1.0 "
        "(certain). This is NOT the probability of a match: a confident non-match is is_match=false, "
        "confidence=0.95.",
    )
    reason: str = Field(description="Brief explanation of why this is/isn't a match")


def make_signature(entity_kind: str, entity_name: str | None, description: str | None) -> type:
    instructions = f"{PREAMBLE}\n\n{rules_for(entity_kind)}\n\nReturn your assessment for EVERY candidate."
    ctx = []
    if entity_name:
        ctx.append(f"You are resolving records in the '{entity_name}' domain.")
    if description:
        ctx.append(f"Domain description: {description}")
    if ctx:
        instructions = "\n".join(ctx) + "\n\n" + instructions
    instructions += (
        "\n\nEach candidate is labeled with an id (1, 2, 3, ...). "
        "In your matches output, use that same id for the candidate_id field."
    )
    return dspy.Signature(
        {
            "entity_text": (str, dspy.InputField(desc="The input record's key-value text representation.")),
            "candidates": (list[dict], dspy.InputField(
                desc="Candidate records, each with candidate_id (1, 2, 3, ...) and record_text.")),
            "matches": (List[MatchCandidate], dspy.OutputField(
                desc="One MatchCandidate per candidate assessed")),
        },
        instructions,
    )


class LLMEntityMatcher:
    component_id = "llm_entity_matcher"

    def __init__(self, version: str, config: dict, entity_kind: str, entity_name: str, description: str):
        self.version = f"{version}.{PROMPT_VERSION}"
        self.endpoint = config.get("llm_endpoint", "databricks-claude-sonnet-4-6")
        self.concurrency = int(config.get("concurrency", 16))
        self.max_candidates = int(config.get("max_candidates_per_call", 10))
        self.max_attempts = int(config.get("max_attempts", 6))
        self.signature = make_signature(entity_kind, entity_name, description)
        self._token = None
        self._token_at = 0.0

    @property
    def ref(self) -> str:
        return f"{self.component_id}_v{self.version}"

    def _lm(self, ws) -> dspy.LM:
        if self._token is None or time.time() - self._token_at > 1800:
            headers = ws.config.authenticate()
            self._token = headers["Authorization"].split(" ", 1)[1]
            self._token_at = time.time()
        return dspy.LM(
            f"databricks/{self.endpoint}",
            api_base=f"{ws.config.host.rstrip('/')}/serving-endpoints",
            api_key=self._token,
            drop_params=True,
            cache=False,
            temperature=0.0,
            max_tokens=4000,
        )

    def score_groups(self, ctx, groups: dict[str, list[str]], texts: dict[str, str]) -> list[ScoredPair]:
        """groups: query record_id -> candidate record_ids (each pair appears once overall)."""
        predictor = dspy.Predict(self.signature)

        @mlflow.trace(name="EntityMatcher", span_type=SpanType.CHAIN)
        def classify(query_id: str, cand_ids: list[str]):
            span = mlflow.get_current_active_span()
            if span:
                span.set_attributes({"rezolake.record_id": query_id, "rezolake.run_id": ctx.run_id,
                                     "rezolake.entity_type_id": ctx.entity_type_id, "ai.model.name": self.endpoint})
            alias = {str(i + 1): cid for i, cid in enumerate(cand_ids)}
            cands = [{"candidate_id": a, "record_text": texts.get(cid, "")} for a, cid in alias.items()]
            with dspy.context(lm=self._lm(ctx.ws)):
                pred = predictor(entity_text=texts.get(query_id, ""), candidates=cands)
            trace_id = span.trace_id if span else None
            out, seen = [], set()
            for m in pred.matches or []:
                md = m.model_dump() if hasattr(m, "model_dump") else dict(m)
                cid = alias.get(str(md.get("candidate_id", "")).strip())
                if cid is None or cid in seen:
                    continue
                seen.add(cid)
                # Confidence is in the verdict (>= 0.5). Values below 0.5 mean the model reported P(verdict)
                # on the other scale; fold them so score stays a calibrated P(match).
                conf = min(max(float(md.get("confidence", 0.0)), 0.0), 1.0)
                conf = max(conf, 1.0 - conf)
                is_match = bool(md.get("is_match", False))
                out.append(ScoredPair(
                    query_id, cid, conf if is_match else 1.0 - conf,
                    evidence={"is_match": is_match, "confidence": conf, "reason": md.get("reason", ""),
                              "endpoint": self.endpoint, "prompt_version": PROMPT_VERSION},
                    trace_id=trace_id))
            for cid in cand_ids:
                if cid not in seen:  # model skipped it: record an explicit non-decision score
                    out.append(ScoredPair(query_id, cid, 0.0,
                                          evidence={"is_match": None, "reason": "not assessed by model",
                                                    "endpoint": self.endpoint}, trace_id=trace_id))
            return out

        def run(item):
            qid, cids = item
            results = []
            for i in range(0, len(cids), self.max_candidates):
                chunk = cids[i:i + self.max_candidates]
                for attempt in range(self.max_attempts):
                    try:
                        results.extend(classify(qid, chunk))
                        break
                    except Exception as e:
                        if attempt == self.max_attempts - 1:
                            ctx.log(f"LLM failed for {qid[:8]}: {e}")
                            results.extend(ScoredPair(qid, c, 0.0, evidence={"error": str(e)[:500]}) for c in chunk)
                        else:
                            time.sleep(_backoff_s(e, attempt))
            return results

        scored: list[ScoredPair] = []
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            for i, res in enumerate(pool.map(run, groups.items()), 1):
                scored.extend(res)
                if i % 50 == 0:
                    ctx.log(f"LLM matched {i}/{len(groups)} records")
        return scored
