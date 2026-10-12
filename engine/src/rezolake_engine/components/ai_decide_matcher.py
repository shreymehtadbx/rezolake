"""ai_decide pair matcher: one decision-model call per candidate pair.

ai_decide returns probabilities, not language, so score = P(same entity)
directly and evidence carries no rationale. Calls go to the AI Functions REST
endpoint from a thread pool: the SQL ai_decide() function runs ~2-3 calls/s per
query however the input is partitioned, while the endpoint takes the same
state/questions and scales to ~100 calls/s (then answers 429).

Caveat (Beta, measured 2026-10): sustained calls from a serverless job ran far
slower (~2.5 pairs/s, p99 latency ~50s) than the same load from outside the
workspace (~54 pairs/s), so the LLM matcher stays the default for batch runs.
"""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor

from .base import ScoredPair
from .rules import rules_for

PROMPT_VERSION = "d1"
_PATH = "/api/2.0/ai-functions/ai-decide"
_QUESTION = "same_entity"

# The LLM weighs conflicting identifiers on its own; the decision model links
# same-name records with different emails AND phones unless told otherwise.
_IDENTIFIER_RULES = {
    "people": "If both records have an email and the emails differ, AND both have a phone and the phones differ, "
              "they are different people even when the names are identical. Common names (e.g. John Smith) "
              "require an agreeing identifier such as an email, phone or ID number.",
    "companies": "If both records have a tax ID or registration number and the values differ, they are different "
                 "companies even when the names are identical.",
    "products": "If both records have a SKU, UPC, EAN or ASIN and the values differ, they are different products "
                "even when the names are identical.",
    "other": "If both records have the same kind of unique identifier and the values differ, they are different "
             "entities even when the names are identical.",
}


def make_questions(entity_kind: str, entity_name: str | None, description: str | None) -> dict:
    parts = []
    if entity_name:
        parts.append(f"You are resolving records in the '{entity_name}' domain.")
    if description:
        parts.append(f"Domain description: {description}")
    parts.append("You are an entity resolution expert. Do record_a and record_b refer to the SAME real-world entity?")
    parts.append(rules_for(entity_kind))
    parts.append(_IDENTIFIER_RULES.get(entity_kind, _IDENTIFIER_RULES["other"]))
    return {_QUESTION: {
        "type": "noul",
        "instructions": "\n\n".join(parts),
        "criteria": {"true": "Both records describe the same real-world entity.",
                     "false": "The records describe different real-world entities."},
    }}


class AIDecideMatcher:
    component_id = "ai_decide_matcher"

    def __init__(self, version: str, config: dict, entity_kind: str, entity_name: str, description: str):
        self.version = f"{version}.{PROMPT_VERSION}"
        self.concurrency = int(config.get("concurrency", 32))  # headroom under the ~100 calls/s rate limit
        self.max_attempts = int(config.get("max_attempts", 5))
        self.questions = make_questions(entity_kind, entity_name, description)

    @property
    def ref(self) -> str:
        return f"{self.component_id}_v{self.version}"

    def _decide(self, ws, text_a: str, text_b: str) -> tuple[float, str]:
        resp = ws.api_client.do("POST", _PATH, body={
            "state": {"record_a": text_a, "record_b": text_b},
            "questions": self.questions,
        })
        if resp.get("error_message") or not resp.get("response"):
            raise RuntimeError(f"ai_decide error: {resp.get('error_message') or 'empty response'}")
        p = float(resp["response"]["answers"][_QUESTION]["probability"])
        return min(max(p, 0.0), 1.0), (resp.get("metadata") or {}).get("version", "")

    def score_groups(self, ctx, groups: dict[str, list[str]], texts: dict[str, str]) -> list[ScoredPair]:
        """groups: query record_id -> candidate record_ids (each pair appears once overall)."""
        pairs = [(q, c) for q, cands in groups.items() for c in cands]

        def one(pair):
            qid, cid = pair
            for attempt in range(self.max_attempts):
                try:
                    p, model_version = self._decide(ctx.ws, texts.get(qid, ""), texts.get(cid, ""))
                    return ScoredPair(qid, cid, p, evidence={
                        "probability": p, "model": "ai_decide", "model_version": model_version,
                        "prompt_version": PROMPT_VERSION})
                except Exception as e:  # 429s included: back off with jitter so threads don't retry in lockstep
                    if attempt == self.max_attempts - 1:
                        ctx.log(f"ai_decide failed for {qid[:8]}/{cid[:8]}: {e}")
                        return ScoredPair(qid, cid, 0.0, evidence={"error": str(e)[:500]})
                    time.sleep(min(2 ** attempt, 16) + random.random())

        scored: list[ScoredPair] = []
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            for i, sp in enumerate(pool.map(one, pairs), 1):
                scored.append(sp)
                if i % 500 == 0:
                    ctx.log(f"ai_decide scored {i}/{len(pairs)} pairs")
        return scored
