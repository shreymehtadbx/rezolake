"""Attribute-level survivorship with provenance (pure Python).

Precedence per attribute:
  1. MANUAL_OVERRIDE  (steward-set golden value)
  2. PREFERRED_RECORD (steward pinned a member as the golden source; non-null values win)
  3. the attribute's rule: SOURCE_PRIORITY | MOST_RECENT_NON_NULL | MOST_COMMON | LONGEST
Ties always break deterministically (source priority, then recency, then record id).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


@dataclass
class MemberRecord:
    record_id: str
    source_id: str
    source_priority: int  # lower = more trusted
    updated_at: str  # ISO timestamp
    attributes: dict[str, str | None]


@dataclass
class AttributeRule:
    attribute_id: str
    rule: str


@dataclass
class Winner:
    attribute_id: str
    value: str | None
    winning_record_id: str | None
    winning_source_id: str | None
    rule: str
    manual_override: bool
    observed: list[dict]


def _present(v) -> bool:
    return v is not None and str(v).strip() != ""


def _tiebreak(m: MemberRecord):
    # Negative-sorting on timestamp strings: sort by priority asc, then updated_at desc.
    return (m.source_priority, _neg(m.updated_at), m.record_id)


def _neg(ts: str) -> tuple:
    return tuple(-ord(c) for c in (ts or ""))


def resolve_entity(
    members: list[MemberRecord],
    rules: list[AttributeRule],
    value_overrides: dict[str, str] | None = None,
    pinned_record_id: str | None = None,
) -> list[Winner]:
    value_overrides = value_overrides or {}
    pinned = next((m for m in members if m.record_id == pinned_record_id), None)
    out: list[Winner] = []
    for r in rules:
        aid = r.attribute_id
        candidates = [m for m in members if _present(m.attributes.get(aid))]
        observed = [
            {"record_id": m.record_id, "source_id": m.source_id, "value": m.attributes.get(aid)}
            for m in sorted(candidates, key=_tiebreak)
        ]
        if aid in value_overrides:
            out.append(Winner(aid, value_overrides[aid], None, None, "MANUAL_OVERRIDE", True, observed))
            continue
        if pinned is not None and _present(pinned.attributes.get(aid)):
            out.append(
                Winner(aid, pinned.attributes[aid], pinned.record_id, pinned.source_id, "PREFERRED_RECORD", True, observed)
            )
            continue
        if not candidates:
            out.append(Winner(aid, None, None, None, r.rule, False, observed))
            continue
        winner = _apply_rule(r.rule, aid, candidates)
        out.append(Winner(aid, winner.attributes[aid], winner.record_id, winner.source_id, r.rule, False, observed))
    return out


def _apply_rule(rule: str, aid: str, candidates: list[MemberRecord]) -> MemberRecord:
    if rule == "MOST_RECENT_NON_NULL":
        return sorted(candidates, key=lambda m: (_neg(m.updated_at), m.source_priority, m.record_id))[0]
    if rule == "MOST_COMMON":
        counts = Counter(str(m.attributes[aid]).strip().lower() for m in candidates)
        top = max(counts.values())
        tied = [m for m in candidates if counts[str(m.attributes[aid]).strip().lower()] == top]
        return sorted(tied, key=_tiebreak)[0]
    if rule == "LONGEST":
        return sorted(candidates, key=lambda m: (-len(str(m.attributes[aid])),) + _tiebreak(m))[0]
    # SOURCE_PRIORITY (default)
    return sorted(candidates, key=_tiebreak)[0]
