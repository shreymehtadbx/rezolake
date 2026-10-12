"""COMMANDS: steward intents (Lakebase) -> durable Delta constraints / overrides.

Stewardship edits resolution *truth* (constraints, overrides), never the
derived cluster tables, so the next automated run cannot undo it.
"""

from __future__ import annotations

import json

from ..ids import new_id, ordered
from .context import RunContext


class CommandError(ValueError):
    pass


def apply_commands(ctx: RunContext) -> dict:
    if ctx.lb is None:
        return {}
    cmds = ctx.lb.claim_commands(ctx.entity_type_id, ctx.run_id)
    if not cmds:
        return {}
    ctx.stage("commands", count=len(cmds))
    applied, failed = 0, 0
    for c in cmds:
        payload = c["payload"] if isinstance(c["payload"], dict) else json.loads(c["payload"] or "{}")
        try:
            before = ctx.constraint_records | ctx.override_records | ctx.rematch_records
            before_entities = set(ctx.override_entities)
            _apply(ctx, c["id"], c["command_type"], payload, c.get("requested_by") or "")
            touched = (ctx.constraint_records | ctx.override_records | ctx.rematch_records) - before
            for eid in ctx.override_entities - before_entities:
                touched |= set(_members(ctx, eid))
            ctx.command_records[c["id"]] = touched
            ctx.command_ids.append(c["id"])
            ctx.pending_command_ids = getattr(ctx, "pending_command_ids", []) + [c["id"]]
            applied += 1
        except CommandError as e:
            failed += 1
            ctx.lb.finish_command(c["id"], False, str(e))
            ctx.log(f"command {c['id']} {c['command_type']} rejected: {e}")
    ctx.metric(commands_applied=applied, commands_failed=failed)
    return {"applied": applied, "failed": failed}


def finish_commands(ctx: RunContext, ok: bool, error: str | None = None) -> None:
    for cid in getattr(ctx, "pending_command_ids", []):
        ctx.lb.finish_command(cid, ok, error)


# ── helpers ────────────────────────────────────────────────────────────


def _live_records(ctx: RunContext, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    inlist = ", ".join(ctx.lit(i) for i in ids)
    return {r.record_id for r in ctx.sql(
        f"""SELECT record_id FROM {ctx.cfg.records}.canonical_record_current
            WHERE entity_type_id = {ctx.lit(ctx.entity_type_id)} AND record_id IN ({inlist}) AND NOT is_deleted"""
    ).collect()}


def _current_entity(ctx: RunContext, entity_id: str) -> str:
    row = ctx.sql(f"""SELECT current_entity_id FROM {ctx.cfg.resolution}.entity_alias
                      WHERE entity_type_id = {ctx.lit(ctx.entity_type_id)} AND old_entity_id = {ctx.lit(entity_id)}
                      LIMIT 1""").first()
    return row.current_entity_id if row else entity_id


def _members(ctx: RunContext, entity_id: str) -> list[str]:
    eid = _current_entity(ctx, entity_id)
    return [r.record_id for r in ctx.sql(
        f"""SELECT record_id FROM {ctx.cfg.resolution}.entity_membership_current
            WHERE entity_type_id = {ctx.lit(ctx.entity_type_id)} AND entity_id = {ctx.lit(eid)}""").collect()]


def _anchor(ctx: RunContext, entity_id: str) -> str | None:
    row = ctx.sql(f"""SELECT anchor_record_id FROM {ctx.cfg.master}.entity_current
                      WHERE entity_type_id = {ctx.lit(ctx.entity_type_id)}
                        AND entity_id = {ctx.lit(_current_entity(ctx, entity_id))}""").first()
    return row.anchor_record_id if row else None


def _insert_constraints(ctx, cmd_id, actor, reason, rows: list[tuple[str, str, str | None]]) -> None:
    if not rows:
        return
    values = ",\n".join(
        f"({ctx.lit(new_id())}, {ctx.lit(ctx.entity_type_id)}, {ctx.lit(t)}, {ctx.lit(a)}, "
        f"{ctx.lit(b) if b else 'NULL'}, {ctx.lit(reason)}, {ctx.lit(actor)}, current_timestamp(), "
        f"{ctx.lit(cmd_id)}, TRUE, NULL, NULL)"
        for t, a, b in rows
    )
    ctx.sql(f"INSERT INTO {ctx.cfg.resolution}.manual_constraint VALUES {values}")


def _revoke(ctx, actor, where: str) -> None:
    ctx.sql(f"""UPDATE {ctx.cfg.resolution}.manual_constraint
                SET active = FALSE, revoked_at = current_timestamp(), revoked_by = {ctx.lit(actor)}
                WHERE entity_type_id = {ctx.lit(ctx.entity_type_id)} AND active AND ({where})""")


def _pair_where(ctx, ctype: str, a: str, b: str) -> str:
    left, right = ordered(a, b)
    return (f"constraint_type = {ctx.lit(ctype)} AND left_record_id = {ctx.lit(left)} "
            f"AND right_record_id = {ctx.lit(right)}")


def _cross_where(ctx, ctype: str, side_a: list[str], side_b: list[str]) -> str:
    la = ", ".join(ctx.lit(x) for x in side_a) or "NULL"
    lb = ", ".join(ctx.lit(x) for x in side_b) or "NULL"
    return (f"constraint_type = {ctx.lit(ctype)} AND ((left_record_id IN ({la}) AND right_record_id IN ({lb})) "
            f"OR (left_record_id IN ({lb}) AND right_record_id IN ({la})))")


def _apply(ctx: RunContext, cmd_id: str, ctype: str, p: dict, actor: str) -> None:
    reason = p.get("reason") or ctype.lower()
    et = ctx.lit(ctx.entity_type_id)

    if ctype in ("ACCEPT_MATCH", "REJECT_MATCH"):
        a, b = p.get("left_record_id"), p.get("right_record_id")
        if not a or not b or a == b or _live_records(ctx, [a, b]) != {a, b}:
            raise CommandError("both records must exist and differ")
        left, right = ordered(a, b)
        if ctype == "ACCEPT_MATCH":
            _revoke(ctx, actor, _pair_where(ctx, "CANNOT_LINK", left, right))
            _revoke(ctx, actor, _pair_where(ctx, "MUST_LINK", left, right))
            _insert_constraints(ctx, cmd_id, actor, reason, [("MUST_LINK", left, right)])
        else:
            _revoke(ctx, actor, _pair_where(ctx, "MUST_LINK", left, right))
            _revoke(ctx, actor, _pair_where(ctx, "CANNOT_LINK", left, right))
            _insert_constraints(ctx, cmd_id, actor, reason, [("CANNOT_LINK", left, right)])
        ctx.constraint_records |= {a, b}

    elif ctype == "MERGE_ENTITIES":
        eids = list(dict.fromkeys(p.get("entity_ids") or []))
        if len(eids) < 2:
            raise CommandError("merge needs at least two entities")
        groups = [_members(ctx, e) for e in eids]
        anchors = [_anchor(ctx, e) for e in eids]
        if any(not g for g in groups) or any(a is None for a in anchors):
            raise CommandError("unknown entity in merge")
        for i in range(1, len(eids)):
            _revoke(ctx, actor, _cross_where(ctx, "CANNOT_LINK", groups[0], groups[i]))
        # Pin every record of the merged entities to the surviving anchor, so the merge holds on human links
        # alone and no automatic rule (e.g. the clusterer's non-match guard) can take part of it apart.
        members = sorted({r for g in groups for r in g} - {anchors[0]})
        rows = [("MUST_LINK", *ordered(anchors[0], r)) for r in members]
        _revoke(ctx, actor, "constraint_type = 'ISOLATE' AND left_record_id IN ({})".format(
            ", ".join(ctx.lit(a) for a in anchors)))
        _insert_constraints(ctx, cmd_id, actor, reason, rows)
        ctx.constraint_records |= {r for g in groups for r in g}

    elif ctype == "SPLIT_ENTITY":
        members = set(_members(ctx, p.get("entity_id", "")))
        moved = set(p.get("record_ids") or [])
        if not members or not moved or not moved < members:
            raise CommandError("split needs a proper, non-empty subset of the entity's records")
        stay = sorted(members - moved)
        moved_l = sorted(moved)
        _revoke(ctx, actor, _cross_where(ctx, "MUST_LINK", moved_l, stay))
        rows = [("CANNOT_LINK", *ordered(m, s)) for m in moved_l for s in stay]
        rows += [("MUST_LINK", *ordered(moved_l[0], m)) for m in moved_l[1:]]
        _insert_constraints(ctx, cmd_id, actor, reason, rows)
        ctx.constraint_records |= members

    elif ctype == "ISOLATE_RECORD":
        rid = p.get("record_id")
        if not rid or not _live_records(ctx, [rid]):
            raise CommandError("record not found")
        _revoke(ctx, actor, f"constraint_type = 'MUST_LINK' AND (left_record_id = {ctx.lit(rid)} "
                            f"OR right_record_id = {ctx.lit(rid)})")
        _revoke(ctx, actor, f"constraint_type = 'ISOLATE' AND left_record_id = {ctx.lit(rid)}")
        _insert_constraints(ctx, cmd_id, actor, reason, [("ISOLATE", rid, None)])
        ctx.constraint_records.add(rid)

    elif ctype == "REVOKE_CONSTRAINT":
        cid = p.get("constraint_id")
        if not cid:
            raise CommandError("constraint_id required")
        _revoke(ctx, actor, f"constraint_id = {ctx.lit(cid)}")

    elif ctype in ("PIN_GOLDEN_RECORD", "OVERRIDE_ATTRIBUTE"):
        eid = _current_entity(ctx, p.get("entity_id", ""))
        members = set(_members(ctx, eid))
        if not members:
            raise CommandError("entity not found")
        g = ctx.cfg.master
        if ctype == "PIN_GOLDEN_RECORD":
            rid = p.get("record_id")
            ctx.sql(f"""UPDATE {g}.survivorship_override SET active = FALSE, revoked_at = current_timestamp()
                        WHERE entity_type_id = {et} AND active AND override_type = 'PIN_RECORD'
                          AND (entity_id = {ctx.lit(eid)} OR record_id IN ({', '.join(ctx.lit(m) for m in members)}))""")
            if rid:
                if rid not in members:
                    raise CommandError("pinned record must be a member of the entity")
                ctx.sql(f"""INSERT INTO {g}.survivorship_override VALUES ({ctx.lit(new_id())}, {et}, {ctx.lit(eid)},
                            'PIN_RECORD', NULL, NULL, {ctx.lit(rid)}, TRUE, {ctx.lit(actor)}, current_timestamp(),
                            {ctx.lit(cmd_id)}, NULL)""")
        else:
            aid = p.get("attribute_id")
            try:
                ctx.definition.attribute(aid)
            except KeyError:
                raise CommandError(f"unknown attribute {aid}")
            ctx.sql(f"""UPDATE {g}.survivorship_override SET active = FALSE, revoked_at = current_timestamp()
                        WHERE entity_type_id = {et} AND active AND override_type = 'OVERRIDE_VALUE'
                          AND entity_id = {ctx.lit(eid)} AND attribute_id = {ctx.lit(aid)}""")
            if p.get("value") is not None:
                ctx.sql(f"""INSERT INTO {g}.survivorship_override VALUES ({ctx.lit(new_id())}, {et}, {ctx.lit(eid)},
                            'OVERRIDE_VALUE', {ctx.lit(aid)}, {ctx.lit(str(p['value']))}, NULL, TRUE,
                            {ctx.lit(actor)}, current_timestamp(), {ctx.lit(cmd_id)}, NULL)""")
        ctx.override_entities.add(eid)

    elif ctype == "EDIT_RECORD":
        rid, aid = p.get("record_id"), p.get("attribute_id")
        if not rid or not _live_records(ctx, [rid]):
            raise CommandError("record not found")
        try:
            ctx.definition.attribute(aid)
        except KeyError:
            raise CommandError(f"unknown attribute {aid}")
        r = ctx.cfg.records
        ctx.sql(f"""UPDATE {r}.record_override SET active = FALSE, revoked_at = current_timestamp()
                    WHERE entity_type_id = {et} AND active AND record_id = {ctx.lit(rid)}
                      AND attribute_id = {ctx.lit(aid)}""")
        if p.get("value") is not None:
            ctx.sql(f"""INSERT INTO {r}.record_override VALUES ({ctx.lit(new_id())}, {et}, {ctx.lit(rid)},
                        {ctx.lit(aid)}, {ctx.lit(str(p['value']))}, TRUE, {ctx.lit(actor)}, current_timestamp(),
                        {ctx.lit(cmd_id)}, NULL)""")
        ctx.override_records.add(rid)
        ctx.rematch_records.add(rid)

    elif ctype == "REMATCH":
        ids = list(p.get("record_ids") or [])
        if p.get("entity_id"):
            ids += _members(ctx, p["entity_id"])
        live = _live_records(ctx, ids)
        if not live:
            raise CommandError("no live records to rematch")
        ctx.rematch_records |= live

    else:
        raise CommandError(f"unknown command type {ctype}")
