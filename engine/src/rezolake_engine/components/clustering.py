"""Constrained connected components + stable entity identity (pure Python).

Authoritative inputs are edges and human constraints; cluster membership is
derived. Union-find is only the disposable implementation detail.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..ids import new_entity_id
from .base import CANNOT_LINK, ISOLATE, MUST_LINK, Constraint, Edge


class _DSU:
    def __init__(self, nodes):
        self.parent = {n: n for n in nodes}
        self.size = {n: 1 for n in nodes}
        # root -> set of records that members of this root may never join
        self.cannot: dict[str, set[str]] = defaultdict(set)

    def find(self, x: str) -> str:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def violates(self, ra: str, rb: str) -> bool:
        small, big = (ra, rb) if len(self.cannot[ra]) <= len(self.cannot[rb]) else (rb, ra)
        return any(self.find(r) == big for r in self.cannot[small])

    def union(self, a: str, b: str) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return True
        if self.violates(ra, rb):
            return False
        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.size[ra] += self.size[rb]
        self.cannot[ra] |= self.cannot.pop(rb, set())
        return True


@dataclass
class ClusterResult:
    components: list[list[str]]
    applied_edges: int = 0
    rejected_edges: int = 0  # LINK edges refused because of CANNOT_LINK / ISOLATE / a veto
    vetoes_applied: int = 0  # confident non-match pairs enforced as soft CANNOT_LINKs


def constrained_components(
    nodes: list[str], edges: list[Edge], constraints: list[Constraint],
    vetoes: list[tuple[str, str]] | None = None,
) -> ClusterResult:
    """CANNOT_LINK > MUST_LINK > automatic LINK (applied by descending score).

    ISOLATE(record) forbids automatic edges touching the record; only explicit
    MUST_LINK constraints can still attach it.

    ``vetoes`` are record pairs the matcher confidently scored as different. They act like
    CANNOT_LINK for automatic links only: a chain of LINK edges may not put the two records in one
    entity, but a human MUST_LINK (applied first) still can.
    """
    node_set = set(nodes)
    dsu = _DSU(nodes)
    isolated = {c.left for c in constraints if c.constraint_type == ISOLATE}
    for c in constraints:
        if c.constraint_type == CANNOT_LINK and c.left in node_set and c.right in node_set:
            dsu.cannot[c.left].add(c.right)
            dsu.cannot[c.right].add(c.left)

    for c in constraints:
        if c.constraint_type == MUST_LINK and c.left in node_set and c.right in node_set:
            dsu.union(c.left, c.right)

    applied_vetoes = 0
    for a, b in vetoes or []:
        if a in node_set and b in node_set and dsu.find(a) != dsu.find(b):
            dsu.cannot[dsu.find(a)].add(b)
            dsu.cannot[dsu.find(b)].add(a)
            applied_vetoes += 1

    applied = rejected = 0
    for e in sorted(edges, key=lambda e: (-e.score, e.left, e.right)):
        if e.left not in node_set or e.right not in node_set:
            continue
        if e.left in isolated or e.right in isolated:
            rejected += 1
            continue
        if dsu.union(e.left, e.right):
            applied += 1
        else:
            rejected += 1

    groups: dict[str, list[str]] = defaultdict(list)
    for n in nodes:
        groups[dsu.find(n)].append(n)
    comps = [sorted(g) for g in groups.values()]
    comps.sort(key=lambda g: g[0])
    return ClusterResult(components=comps, applied_edges=applied, rejected_edges=rejected,
                         vetoes_applied=applied_vetoes)


# ── Stable identity ─────────────────────────────────────────────────────


@dataclass
class PriorEntity:
    entity_id: str
    anchor_record_id: str
    created_at: str  # ISO timestamp; older wins on merge
    members: set[str] = field(default_factory=set)


@dataclass
class EntityEvent:
    event_type: str  # CREATE | MERGE | SPLIT | RETIRE
    entity_id: str
    source_entity_ids: list[str]
    target_entity_ids: list[str]


@dataclass
class IdentityResult:
    membership: dict[str, str]  # record_id -> entity_id
    anchors: dict[str, str]  # entity_id -> anchor record_id
    created: dict[str, bool]  # entity_id -> newly created this run
    events: list[EntityEvent]
    aliases: list[tuple[str, str, str]]  # (old_entity_id, current_entity_id, reason)
    changed_entities: set[str]  # entities whose membership changed (incl. new)
    retired_entities: set[str]


def assign_stable_ids(
    components: list[list[str]], prior: list[PriorEntity], id_factory=new_entity_id
) -> IdentityResult:
    """Preserve entity ids across reclustering.

    - new component with no prior anchor            -> new id (CREATE, or SPLIT child)
    - component containing exactly one prior anchor -> keep that id
    - component containing several prior anchors    -> oldest id survives, others alias (MERGE)
    - prior entity whose anchor was deleted gets a new anchor from its surviving members first
    """
    all_nodes = {r for comp in components for r in comp}
    prior_by_id = {p.entity_id: p for p in prior}

    # Re-anchor entities whose anchor record disappeared.
    anchors: dict[str, str] = {}
    for p in prior:
        if p.anchor_record_id in all_nodes:
            anchors[p.entity_id] = p.anchor_record_id
        else:
            survivors = sorted(m for m in p.members if m in all_nodes)
            if survivors:
                anchors[p.entity_id] = survivors[0]
    anchor_owner = {rec: eid for eid, rec in anchors.items()}

    prior_member_of: dict[str, str] = {}
    for p in prior:
        for m in p.members:
            prior_member_of[m] = p.entity_id

    membership: dict[str, str] = {}
    new_anchors: dict[str, str] = {}
    created: dict[str, bool] = {}
    events: list[EntityEvent] = []
    aliases: list[tuple[str, str, str]] = []
    changed: set[str] = set()
    surviving: set[str] = set()
    split_children: dict[str, list[str]] = defaultdict(list)

    for comp in components:
        comp_set = set(comp)
        owners = sorted({anchor_owner[r] for r in comp if r in anchor_owner})
        if not owners:
            eid = id_factory()
            anchor = comp[0]
            created[eid] = True
            parents = sorted({prior_member_of[r] for r in comp if r in prior_member_of})
            if parents:
                for parent in parents:
                    split_children[parent].append(eid)
            else:
                events.append(EntityEvent("CREATE", eid, [], [eid]))
            changed.add(eid)
        elif len(owners) == 1:
            eid = owners[0]
            anchor = anchors[eid]
            created[eid] = False
            if comp_set != prior_by_id[eid].members:
                changed.add(eid)
        else:
            owners.sort(key=lambda e: (prior_by_id[e].created_at, e))
            eid = owners[0]
            anchor = anchors[eid]
            created[eid] = False
            absorbed = owners[1:]
            for old in absorbed:
                aliases.append((old, eid, "MERGE"))
            events.append(EntityEvent("MERGE", eid, owners, [eid]))
            changed.add(eid)
        surviving.add(eid)
        new_anchors[eid] = anchor
        for r in comp:
            membership[r] = eid

    for parent, children in split_children.items():
        if parent in surviving:
            events.append(EntityEvent("SPLIT", parent, [parent], [parent] + sorted(children)))
            changed.add(parent)
        else:
            events.append(EntityEvent("SPLIT", parent, [parent], sorted(children)))

    merged_away = {a[0] for a in aliases}
    retired = {p.entity_id for p in prior if p.entity_id not in surviving and p.entity_id not in merged_away}
    for eid in sorted(retired):
        events.append(EntityEvent("RETIRE", eid, [eid], []))

    return IdentityResult(
        membership=membership,
        anchors=new_anchors,
        created=created,
        events=events,
        aliases=aliases,
        changed_entities=changed,
        retired_entities=retired,
    )
