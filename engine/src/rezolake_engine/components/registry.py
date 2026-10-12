"""Component registry: strategy component names -> implementations.

Adding a blocker/matcher means registering it here; no stage code changes.
"""

from __future__ import annotations

from ..definition import ComponentRef, EntityDefinition


def blocker(ref: ComponentRef):
    if ref.component == "nearest_by_blocker":
        from .nearest_by_blocker import NearestByBlocker

        return NearestByBlocker(ref.version, ref.config)
    if ref.component == "ai_search_blocker":
        from .ai_search_blocker import AISearchBlocker

        return AISearchBlocker(ref.version, ref.config)
    raise ValueError(f"unknown blocker component {ref.component}")


def matcher(ref: ComponentRef, d: EntityDefinition):
    if ref.component == "ai_decide_matcher":
        from .ai_decide_matcher import AIDecideMatcher

        return AIDecideMatcher(ref.version, ref.config, d.entity_kind, d.name, d.description)
    if ref.component == "llm_entity_matcher":
        from .llm_matcher import LLMEntityMatcher

        return LLMEntityMatcher(ref.version, ref.config, d.entity_kind, d.name, d.description)
    raise ValueError(f"unknown matcher component {ref.component}")


def decision_policy(ref: ComponentRef):
    if ref.component == "threshold_policy":
        from .decision import ThresholdPolicy

        return ThresholdPolicy(ref.version, ref.config)
    raise ValueError(f"unknown decision component {ref.component}")


def check(ref: ComponentRef, known: set[str]) -> None:
    if ref.component not in known:
        raise ValueError(f"unknown component {ref.component}")
