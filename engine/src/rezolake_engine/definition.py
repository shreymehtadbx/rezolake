"""Entity Definition and Resolution Strategy documents.

Both are authored as JSON drafts in Lakebase by the app and frozen into
immutable Delta versions by the PUBLISH mode. The engine only ever runs
against a published version.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
SAFE_TABLE = re.compile(r"^`?[A-Za-z0-9_\-]+`?\.`?[A-Za-z0-9_\-]+`?\.`?[A-Za-z0-9_\-]+`?$")
SAFE_COLUMN = re.compile(r"^[^`\x00]{1,255}$")

EXTENSION = "__extension__"
IGNORE = "__ignore__"

ENTITY_KINDS = {"people", "companies", "products", "other"}
DATA_TYPES = {"string", "integer", "float", "date", "boolean"}
SEMANTIC_TYPES = {"text", "name", "email", "phone", "address", "id", "date", "number", "code"}
SURVIVORSHIP_RULES = {"SOURCE_PRIORITY", "MOST_RECENT_NON_NULL", "MOST_COMMON", "LONGEST"}
STANDARDIZE = {"none", "title", "upper", "lower"}


class DefinitionError(ValueError):
    pass


def _require_id(value: Any, what: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.match(value):
        raise DefinitionError(f"{what} must match {SAFE_ID.pattern}: {value!r}")
    return value


@dataclass
class Attribute:
    attribute_id: str
    name: str
    display_name: str
    data_type: str = "string"
    semantic_type: str = "text"
    matching: bool = True
    display: bool = True
    survivorship: str = "SOURCE_PRIORITY"
    standardize: str = "none"  # canonical casing applied to every source value

    @classmethod
    def from_dict(cls, d: dict) -> "Attribute":
        a = cls(
            attribute_id=_require_id(d.get("attribute_id"), "attribute_id"),
            name=_require_id(d.get("name"), "attribute name"),
            display_name=d.get("display_name") or d.get("name"),
            data_type=d.get("data_type", "string"),
            semantic_type=d.get("semantic_type", "text"),
            matching=bool(d.get("matching", True)),
            display=bool(d.get("display", True)),
            survivorship=(d.get("survivorship") or {}).get("rule", "SOURCE_PRIORITY")
            if isinstance(d.get("survivorship"), dict)
            else (d.get("survivorship") or "SOURCE_PRIORITY"),
            standardize=d.get("standardize") or "none",
        )
        if a.data_type not in DATA_TYPES:
            raise DefinitionError(f"attribute {a.name}: unknown data_type {a.data_type}")
        if a.semantic_type not in SEMANTIC_TYPES:
            raise DefinitionError(f"attribute {a.name}: unknown semantic_type {a.semantic_type}")
        if a.standardize not in STANDARDIZE:
            raise DefinitionError(f"attribute {a.name}: unknown standardize {a.standardize}")
        if a.survivorship not in SURVIVORSHIP_RULES:
            raise DefinitionError(f"attribute {a.name}: unknown survivorship {a.survivorship}")
        return a

    def to_dict(self) -> dict:
        return {
            "attribute_id": self.attribute_id,
            "name": self.name,
            "display_name": self.display_name,
            "data_type": self.data_type,
            "semantic_type": self.semantic_type,
            "matching": self.matching,
            "display": self.display,
            "survivorship": {"rule": self.survivorship},
            "standardize": self.standardize,
        }


@dataclass
class Source:
    source_id: str
    name: str
    table: str
    primary_key: list[str]
    mappings: dict[str, str]  # source column -> attribute_id | __extension__ | __ignore__
    priority: int = 100
    active: bool = True

    @classmethod
    def from_dict(cls, d: dict, attribute_ids: set[str]) -> "Source":
        s = cls(
            source_id=_require_id(d.get("source_id"), "source_id"),
            name=d.get("name") or d.get("source_id"),
            table=d.get("table", ""),
            primary_key=list(d.get("primary_key") or []),
            mappings=dict(d.get("mappings") or {}),
            priority=int(d.get("priority", 100)),
            active=bool(d.get("active", True)),
        )
        if not SAFE_TABLE.match(s.table):
            raise DefinitionError(f"source {s.source_id}: table must be catalog.schema.table")
        if not s.primary_key:
            raise DefinitionError(f"source {s.source_id}: a primary key is required")
        for col in list(s.primary_key) + list(s.mappings):
            if not SAFE_COLUMN.match(col):
                raise DefinitionError(f"source {s.source_id}: bad column name {col!r}")
        for col, target in s.mappings.items():
            if target not in attribute_ids and target not in (EXTENSION, IGNORE):
                raise DefinitionError(
                    f"source {s.source_id}: column {col} maps to unknown attribute {target}"
                )
        return s

    @property
    def quoted_table(self) -> str:
        return ".".join(f"`{p.strip('`')}`" for p in self.table.split("."))

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "name": self.name,
            "table": self.table,
            "primary_key": self.primary_key,
            "mappings": self.mappings,
            "priority": self.priority,
            "active": self.active,
        }


@dataclass
class EntityDefinition:
    entity_type_id: str
    name: str
    slug: str
    entity_kind: str
    description: str
    attributes: list[Attribute]
    sources: list[Source]
    version: int = 0

    @classmethod
    def from_dict(cls, d: dict, version: int = 0) -> "EntityDefinition":
        attrs = [Attribute.from_dict(a) for a in d.get("attributes") or []]
        if not attrs:
            raise DefinitionError("an entity definition needs at least one attribute")
        ids = [a.attribute_id for a in attrs]
        names = [a.name for a in attrs]
        if len(set(ids)) != len(ids) or len(set(names)) != len(names):
            raise DefinitionError("attribute ids and names must be unique")
        id_set = set(ids)
        sources = [Source.from_dict(s, id_set) for s in d.get("sources") or []]
        sids = [s.source_id for s in sources]
        if len(set(sids)) != len(sids):
            raise DefinitionError("source ids must be unique")
        kind = d.get("entity_kind", "other")
        if kind not in ENTITY_KINDS:
            raise DefinitionError(f"unknown entity_kind {kind}")
        return cls(
            entity_type_id=_require_id(d.get("entity_type_id"), "entity_type_id"),
            name=d.get("name") or d["entity_type_id"],
            slug=_require_id(d.get("slug"), "slug"),
            entity_kind=kind,
            description=d.get("description") or "",
            attributes=attrs,
            sources=sources,
            version=version,
        )

    @classmethod
    def from_json(cls, text: str, version: int = 0) -> "EntityDefinition":
        return cls.from_dict(json.loads(text), version)

    def to_dict(self) -> dict:
        return {
            "entity_type_id": self.entity_type_id,
            "name": self.name,
            "slug": self.slug,
            "entity_kind": self.entity_kind,
            "description": self.description,
            "attributes": [a.to_dict() for a in self.attributes],
            "sources": [s.to_dict() for s in self.sources],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @property
    def active_sources(self) -> list[Source]:
        return [s for s in self.sources if s.active]

    def attribute(self, attribute_id: str) -> Attribute:
        for a in self.attributes:
            if a.attribute_id == attribute_id:
                return a
        raise KeyError(attribute_id)

    def source(self, source_id: str) -> Source:
        for s in self.sources:
            if s.source_id == source_id:
                return s
        raise KeyError(source_id)


# ── Resolution Strategy ────────────────────────────────────────────────

DEFAULT_STRATEGY: dict[str, Any] = {
    "representation": {"embedding_endpoint": "databricks-bge-large-en"},
    "blocker": {
        "component": "nearest_by_blocker",
        "version": "1",
        "config": {"num_results": 10, "score_threshold": 0.5},
    },
    "matchers": [
        {
            "component": "llm_entity_matcher",
            "version": "1",
            "config": {"llm_endpoint": "databricks-claude-sonnet-4-6", "concurrency": 16},
        }
    ],
    "decision": {
        "component": "threshold_policy",
        "version": "1",
        "config": {"link_threshold": 0.85, "review_threshold": 0.5, "combine": "max"},
    },
    # no_link_veto_below: a NO_LINK scored below this keeps the two records apart (off when absent).
    "clusterer": {"component": "constrained_components", "version": "2", "config": {"no_link_veto_below": 0.2}},
    "survivorship": {"component": "attribute_rules", "version": "1", "config": {}},
}


@dataclass
class ComponentRef:
    component: str
    version: str
    config: dict = field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"{self.component}_v{self.version}"

    @classmethod
    def from_dict(cls, d: dict) -> "ComponentRef":
        return cls(component=d["component"], version=str(d.get("version", "1")), config=dict(d.get("config") or {}))


@dataclass
class Strategy:
    version: int
    embedding_endpoint: str
    blocker: ComponentRef
    matchers: list[ComponentRef]
    decision: ComponentRef
    clusterer: ComponentRef
    survivorship: ComponentRef
    raw: dict

    @classmethod
    def from_dict(cls, d: dict | None, version: int = 0) -> "Strategy":
        merged = json.loads(json.dumps(DEFAULT_STRATEGY))
        for k, v in (d or {}).items():
            merged[k] = v
        if not merged.get("matchers"):
            raise DefinitionError("strategy needs at least one matcher")
        return cls(
            version=version,
            embedding_endpoint=merged["representation"].get("embedding_endpoint", "databricks-bge-large-en"),
            blocker=ComponentRef.from_dict(merged["blocker"]),
            matchers=[ComponentRef.from_dict(m) for m in merged["matchers"]],
            decision=ComponentRef.from_dict(merged["decision"]),
            clusterer=ComponentRef.from_dict(merged["clusterer"]),
            survivorship=ComponentRef.from_dict(merged["survivorship"]),
            raw=merged,
        )

    def to_json(self) -> str:
        return json.dumps(self.raw, sort_keys=True)
