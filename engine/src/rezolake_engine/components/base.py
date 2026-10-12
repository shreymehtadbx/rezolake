"""Resolution component contracts.

The engine orchestrates stages; components implement one stage each and only
exchange these plain data contracts. Nothing outside ``components/`` may import
a concrete component — they are resolved by name through ``registry``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

LINK = "LINK"
NO_LINK = "NO_LINK"
REVIEW = "REVIEW"

MUST_LINK = "MUST_LINK"
CANNOT_LINK = "CANNOT_LINK"
ISOLATE = "ISOLATE"


@dataclass
class CandidatePair:
    query_record_id: str
    candidate_record_id: str
    rank: int
    block_score: float
    block_reason: str = ""


@dataclass
class ScoredPair:
    left_record_id: str
    right_record_id: str
    score: float  # calibrated P(match) in [0, 1]
    evidence: dict[str, Any] = field(default_factory=dict)
    features: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None
    # Set by the resolve stage (not by matchers): which matcher ref and run produced this score.
    matcher: str | None = None
    score_run_id: str | None = None


@dataclass
class Decision:
    left_record_id: str
    right_record_id: str
    decision: str
    score: float
    reason: str


@dataclass
class Edge:
    left: str
    right: str
    score: float


@dataclass
class Constraint:
    constraint_type: str  # MUST_LINK | CANNOT_LINK | ISOLATE
    left: str
    right: str | None = None


class RunContext(Protocol):
    """What a component may see of the running job (see stages.context)."""

    entity_type_id: str
    run_id: str

    def log(self, msg: str) -> None: ...


class Blocker(Protocol):
    """Work-set records -> candidate pairs, over the embeddings in match_representation."""

    component_id: str
    version: str
    needs_query_vectors: bool  # True: the work set carries each record's embedding (else None)

    def prepare(self, ctx: Any) -> None:
        """Build/refresh whatever the blocker queries (e.g. an index); runs before generate_candidates."""
        ...

    def generate_candidates(self, ctx: Any, queries: dict[str, list[float] | None]) -> list[CandidatePair]: ...


class Matcher(Protocol):
    """Candidate pairs -> one ScoredPair per pair (score = P(match)); negatives are kept.

    A pair that could not be scored carries ``evidence["error"]``; its query record is retried next run.
    """

    component_id: str
    version: str

    def score_groups(self, ctx: Any, groups: dict[str, list[str]], texts: dict[str, str]) -> list[ScoredPair]:
        """groups: query record_id -> candidate record_ids; texts: record_id -> match text."""
        ...


class DecisionPolicy(Protocol):
    def decide(self, scores_by_pair: dict[tuple[str, str], list[ScoredPair]]) -> list[Decision]: ...
