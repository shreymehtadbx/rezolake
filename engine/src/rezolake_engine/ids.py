"""Deterministic identifiers shared by the engine and its SQL.

The Python helpers here must stay byte-for-byte equivalent to the Spark SQL
expressions in ``sql_record_id`` / ``sql_pair_id`` so a record or pair gets
the same id no matter which side computed it.
"""

from __future__ import annotations

import hashlib
import uuid

KEY_SEP = "||"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record_id(entity_type_id: str, source_id: str, source_key: str) -> str:
    """Stable Rezolake record id: sha256(entity_type | source | normalized pk)."""
    return _sha256(f"{entity_type_id}|{source_id}|{source_key}")


def ordered(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def pair_id(a: str, b: str) -> str:
    """Order-independent pair id (left < right)."""
    left, right = ordered(a, b)
    return _sha256(f"{left}|{right}")


def new_entity_id() -> str:
    """Persistent, business-neutral master entity id."""
    return str(uuid.uuid4())


def new_id() -> str:
    return str(uuid.uuid4())


# ── Spark SQL equivalents ───────────────────────────────────────────────


def sql_source_key(pk_columns: list[str], alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    parts = ", ".join(f"trim(cast({prefix}`{c}` AS STRING))" for c in pk_columns)
    return f"concat_ws('{KEY_SEP}', {parts})"


def sql_record_id(entity_type_id: str, source_id: str, key_expr: str) -> str:
    return (
        f"sha2(concat_ws('|', '{entity_type_id}', '{source_id}', {key_expr}), 256)"
    )


def sql_pair_id(a: str, b: str) -> str:
    return f"sha2(concat(least({a}, {b}), '|', greatest({a}, {b})), 256)"
