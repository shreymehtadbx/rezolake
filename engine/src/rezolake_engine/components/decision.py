"""Decision policies: turn matcher scores into LINK / REVIEW / NO_LINK."""

from __future__ import annotations

from .base import LINK, NO_LINK, REVIEW, Decision, ScoredPair


class ThresholdPolicy:
    component_id = "threshold_policy"

    def __init__(self, version: str, config: dict):
        self.version = version
        self.link = float(config.get("link_threshold", 0.85))
        self.review = float(config.get("review_threshold", 0.5))
        self.combine = config.get("combine", "max")  # max | min | mean
        if not 0 <= self.review <= self.link <= 1:
            raise ValueError("thresholds must satisfy 0 <= review <= link <= 1")

    @property
    def ref(self) -> str:
        return f"{self.component_id}_v{self.version}"

    def _combined(self, scores: list[ScoredPair]) -> float:
        vals = [s.score for s in scores]
        if self.combine == "min":
            return min(vals)
        if self.combine == "mean":
            return sum(vals) / len(vals)
        return max(vals)

    def decide(self, scores_by_pair: dict[tuple[str, str], list[ScoredPair]]) -> list[Decision]:
        out = []
        for (left, right), scores in scores_by_pair.items():
            s = self._combined(scores)
            if s >= self.link:
                d, why = LINK, f"score {s:.3f} >= link {self.link}"
            elif s >= self.review:
                d, why = REVIEW, f"{self.review} <= score {s:.3f} < link {self.link}"
            else:
                d, why = NO_LINK, f"score {s:.3f} < review {self.review}"
            out.append(Decision(left, right, d, s, why))
        return out
