import itertools

from rezolake_engine.components import registry
from rezolake_engine.components.ai_decide_matcher import AIDecideMatcher, make_questions
from rezolake_engine.components.base import CANNOT_LINK, ISOLATE, MUST_LINK, Constraint, Edge, ScoredPair
from rezolake_engine.components.clustering import (
    PriorEntity,
    assign_stable_ids,
    constrained_components,
)
from rezolake_engine.components.decision import ThresholdPolicy
from rezolake_engine.components.survivorship import AttributeRule, MemberRecord, resolve_entity
from rezolake_engine.components.nearest_by_blocker import NearestByBlocker
from rezolake_engine.definition import ComponentRef, DefinitionError, EntityDefinition, Strategy
from rezolake_engine.ids import pair_id, record_id


def ids():
    c = itertools.count(1)
    return lambda: f"E{next(c)}"


def test_ids_deterministic_and_order_free():
    assert record_id("t", "s", "1") == record_id("t", "s", "1")
    assert record_id("t", "s", "1") != record_id("t", "s2", "1")
    assert pair_id("a", "b") == pair_id("b", "a")


def test_components_basic_transitive():
    r = constrained_components(["a", "b", "c", "d"], [Edge("a", "b", 0.9), Edge("b", "c", 0.95)], [])
    assert r.components == [["a", "b", "c"], ["d"]]


def test_cannot_link_blocks_transitive_merge():
    edges = [Edge("a", "b", 0.99), Edge("b", "c", 0.9)]
    r = constrained_components(["a", "b", "c"], edges, [Constraint(CANNOT_LINK, "a", "c")])
    assert r.components == [["a", "b"], ["c"]]
    assert r.rejected_edges == 1


def test_must_link_and_isolate():
    edges = [Edge("a", "b", 0.99), Edge("c", "d", 0.99)]
    cons = [Constraint(ISOLATE, "b"), Constraint(MUST_LINK, "a", "c")]
    r = constrained_components(["a", "b", "c", "d"], edges, cons)
    assert r.components == [["a", "c", "d"], ["b"]]


def test_cannot_beats_must():
    cons = [Constraint(CANNOT_LINK, "a", "b"), Constraint(MUST_LINK, "a", "b")]
    r = constrained_components(["a", "b"], [], cons)
    assert r.components == [["a"], ["b"]]


def test_stable_ids_create_keep_merge_split():
    f = ids()
    r1 = assign_stable_ids([["a", "b"], ["c"]], [], f)
    e_ab, e_c = r1.membership["a"], r1.membership["c"]
    assert {ev.event_type for ev in r1.events} == {"CREATE"}
    prior = [
        PriorEntity(e_ab, r1.anchors[e_ab], "2026-01-01", {"a", "b"}),
        PriorEntity(e_c, r1.anchors[e_c], "2026-01-02", {"c"}),
    ]
    # unchanged
    r2 = assign_stable_ids([["a", "b"], ["c"]], prior, f)
    assert r2.membership == r1.membership and not r2.changed_entities
    # merge: older id survives, other aliased
    r3 = assign_stable_ids([["a", "b", "c"]], prior, f)
    assert set(r3.membership.values()) == {e_ab}
    assert r3.aliases == [(e_c, e_ab, "MERGE")]
    # split: anchor side keeps id, other side gets a new id
    r4 = assign_stable_ids([["a"], ["b"], ["c"]], prior, f)
    assert r4.membership["a"] == e_ab and r4.membership["b"] not in (e_ab, e_c)
    assert any(ev.event_type == "SPLIT" and ev.entity_id == e_ab for ev in r4.events)


def test_stable_ids_reanchor_when_anchor_deleted():
    prior = [PriorEntity("E1", "a", "2026-01-01", {"a", "b", "c"})]
    r = assign_stable_ids([["b", "c"]], prior, ids())
    assert r.membership == {"b": "E1", "c": "E1"} and r.anchors["E1"] == "b"


def test_survivorship_rules_and_overrides():
    m = [
        MemberRecord("r1", "crm", 1, "2026-01-01", {"name": "Jon Smith", "email": None, "phone": "111"}),
        MemberRecord("r2", "erp", 2, "2026-03-01", {"name": "Jonathan Smith", "email": "j@x.com", "phone": "222"}),
        MemberRecord("r3", "web", 3, "2026-02-01", {"name": "Jon Smith", "email": "j@x.com", "phone": None}),
    ]
    rules = [
        AttributeRule("name", "SOURCE_PRIORITY"),
        AttributeRule("email", "SOURCE_PRIORITY"),
        AttributeRule("phone", "MOST_RECENT_NON_NULL"),
    ]
    w = {x.attribute_id: x for x in resolve_entity(m, rules)}
    assert w["name"].value == "Jon Smith" and w["name"].winning_record_id == "r1"
    assert w["email"].winning_source_id == "erp"
    assert w["phone"].value == "222"
    w2 = {x.attribute_id: x for x in resolve_entity(m, rules, {"phone": "999"}, "r2")}
    assert w2["phone"].value == "999" and w2["phone"].rule == "MANUAL_OVERRIDE"
    assert w2["name"].value == "Jonathan Smith" and w2["name"].rule == "PREFERRED_RECORD"
    most_common = resolve_entity(m, [AttributeRule("name", "MOST_COMMON")])[0]
    assert most_common.value == "Jon Smith"


def test_threshold_policy():
    p = ThresholdPolicy("1", {"link_threshold": 0.8, "review_threshold": 0.5})
    out = p.decide(
        {
            ("a", "b"): [ScoredPair("a", "b", 0.9)],
            ("a", "c"): [ScoredPair("a", "c", 0.6)],
            ("a", "d"): [ScoredPair("a", "d", 0.1), ScoredPair("a", "d", 0.2)],
        }
    )
    assert [d.decision for d in out] == ["LINK", "REVIEW", "NO_LINK"]


def test_definition_validation():
    good = {
        "entity_type_id": "customer",
        "slug": "customer",
        "name": "Customer",
        "entity_kind": "people",
        "attributes": [{"attribute_id": "a1", "name": "name"}],
        "sources": [
            {"source_id": "crm", "table": "c.s.t", "primary_key": ["id"], "mappings": {"Name": "a1", "x": "__extension__"}}
        ],
    }
    d = EntityDefinition.from_dict(good)
    assert d.sources[0].quoted_table == "`c`.`s`.`t`"
    bad = dict(good, sources=[dict(good["sources"][0], primary_key=[])])
    try:
        EntityDefinition.from_dict(bad)
        raise AssertionError("expected DefinitionError")
    except DefinitionError:
        pass


def test_nearest_by_blocker_sql():
    b = NearestByBlocker("1", {"num_results": 5, "score_threshold": 0.6})
    sql = b.sql("`c`.rezolake2_records", "'customer'")
    assert "EXACT NEAREST 6 BY SIMILARITY vector_cosine_similarity(q.embedding, t.embedding)" in sql
    assert "cand_id <> query_id AND score >= 0.6" in sql and "rank <= 5" in sql
    assert sql.count("entity_type_id = 'customer' AND NOT is_deleted") == 2
    assert "APPROX NEAREST" in NearestByBlocker("1", {"search": "approx"}).sql("r", "'x'")
    assert not b.needs_query_vectors
    try:
        NearestByBlocker("1", {"num_results": 100_000})
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


class _Ctx:
    def __init__(self, responses):
        self.calls = []
        test = self

        class Api:
            def do(self, method, path, body=None):
                test.calls.append((method, path, body))
                r = responses.pop(0)
                if isinstance(r, Exception):
                    raise r
                return r

        self.ws = type("WS", (), {"api_client": Api()})()

    def log(self, msg):
        pass


def _answer(p):
    return {"response": {"answers": {"same_entity": {"type": "noul", "probability": p}}}, "metadata": {"version": "1.0"}}


def test_ai_decide_matcher_scores_and_retries(monkeypatch):
    monkeypatch.setattr("rezolake_engine.components.ai_decide_matcher.time.sleep", lambda s: None)
    m = AIDecideMatcher("1", {"concurrency": 1, "max_attempts": 2}, "people", "Customer", "")
    ctx = _Ctx([_answer(0.95), RuntimeError("429 rate limit"), _answer(0.1), RuntimeError("x"), RuntimeError("y")])
    out = m.score_groups(ctx, {"a": ["b", "c"], "d": ["e"]}, {"a": "A", "b": "B", "c": "C", "d": "D", "e": "E"})
    assert [(s.left_record_id, s.right_record_id, s.score) for s in out] == [("a", "b", 0.95), ("a", "c", 0.1), ("d", "e", 0.0)]
    assert out[0].evidence["model"] == "ai_decide" and out[0].evidence["model_version"] == "1.0"
    assert "error" in out[2].evidence  # resolve() treats this query as failed and retries it next run
    method, path, body = ctx.calls[0]
    assert (method, path) == ("POST", "/api/2.0/ai-functions/ai-decide")
    assert body["state"] == {"record_a": "A", "record_b": "B"} and body["questions"]["same_entity"]["type"] == "noul"
    assert m.ref == "ai_decide_matcher_v1.d1"


def test_ai_decide_error_message_is_a_failure(monkeypatch):
    monkeypatch.setattr("rezolake_engine.components.ai_decide_matcher.time.sleep", lambda s: None)
    m = AIDecideMatcher("1", {"concurrency": 1, "max_attempts": 1}, "people", "", "")
    ctx = _Ctx([{"response": None, "error_message": "bad state", "metadata": {}}])
    (sp,) = m.score_groups(ctx, {"a": ["b"]}, {})
    assert sp.score == 0.0 and "bad state" in sp.evidence["error"]


def test_ai_decide_questions_include_identifier_rule():
    q = make_questions("people", "Customer", "B2C customers")["same_entity"]["instructions"]
    assert q.startswith("You are resolving records in the 'Customer' domain.")
    assert "NAME is the PRIMARY identifier" in q and "emails differ, AND both have a phone" in q
    assert "different entities even when" in make_questions("unknown_kind", None, None)["same_entity"]["instructions"]


def test_default_strategy_and_registry():
    s = Strategy.from_dict(None)
    assert (s.blocker.component, s.matchers[0].component) == ("nearest_by_blocker", "llm_entity_matcher")
    d = EntityDefinition.from_dict({"entity_type_id": "c", "slug": "c", "attributes": [{"attribute_id": "a", "name": "a"}]})
    assert registry.blocker(ComponentRef("ai_search_blocker", "1")).needs_query_vectors
    assert not registry.blocker(s.blocker).needs_query_vectors
    assert registry.matcher(ComponentRef("ai_decide_matcher", "1"), d).component_id == "ai_decide_matcher"


class _BlockCtx:
    def __init__(self, changed, index_exists):
        self.representations_changed, self.logs, self.calls = changed, [], []
        self.cfg = type("Cfg", (), {"vs_index": "c.s.idx"})()
        test = self

        class Idx:
            def get_index(self, name):
                test.calls.append("get")
                if not index_exists:
                    raise RuntimeError("Index not found")

        self.ws = type("WS", (), {"vector_search_indexes": Idx()})()

    def log(self, msg):
        self.logs.append(msg)


def test_blockers_prepare():
    NearestByBlocker("1", {}).prepare(object())  # no-op, needs nothing from ctx
    ctx = _BlockCtx(changed=0, index_exists=True)
    registry.blocker(ComponentRef("ai_search_blocker", "1")).prepare(ctx)  # up to date: no sync
    assert ctx.calls == ["get"] and "up to date" in ctx.logs[0]


def test_llm_backoff_waits_longer_on_rate_limits():
    from rezolake_engine.components.llm_matcher import _backoff_s

    limited = RuntimeError('{"error_code":"REQUEST_LIMIT_EXCEEDED","message":"output tokens per minute"}')
    assert 10 <= _backoff_s(limited, 0) <= 15 and 60 <= _backoff_s(limited, 5) <= 65
    assert _backoff_s(ValueError("bad json"), 1) == 4


def test_no_link_veto_stops_transitive_merge():
    # a-b and b-c are links, but the matcher confidently said a and c are different.
    edges = [Edge("a", "b", 0.95), Edge("b", "c", 0.90)]
    r = constrained_components(["a", "b", "c"], edges, [], vetoes=[("a", "c")])
    assert r.components == [["a", "b"], ["c"]]
    assert r.vetoes_applied == 1 and r.rejected_edges == 1
    # without the veto the chain merges everything
    assert constrained_components(["a", "b", "c"], edges, []).components == [["a", "b", "c"]]


def test_must_link_beats_veto():
    r = constrained_components(["a", "b"], [], [Constraint(MUST_LINK, "a", "b")], vetoes=[("a", "b")])
    assert r.components == [["a", "b"]] and r.vetoes_applied == 0


def test_new_strategies_veto_by_default_existing_ones_do_not():
    assert Strategy.from_dict(None).clusterer.config == {"no_link_veto_below": 0.2}
    published = {"clusterer": {"component": "constrained_components", "version": "1", "config": {}}}
    assert Strategy.from_dict(published).clusterer.config == {}


def test_long_runs_yield_to_queued_steward_work():
    from rezolake_engine.stages.resolve import _should_yield

    class LB:
        def __init__(self, waiting=False, boom=False):
            self.waiting, self.boom = waiting, boom

        def steward_work_waiting(self, run_id):
            if self.boom:
                raise RuntimeError("lakebase down")
            return self.waiting

    def ctx(mode, lb):
        return type("Ctx", (), {"mode": mode, "lb": lb, "run_id": "r1", "log": lambda self, m: None})()

    assert _should_yield(ctx("FULL", LB(waiting=True)))
    assert not _should_yield(ctx("FULL", LB(waiting=False)))
    assert not _should_yield(ctx("COMMAND", LB(waiting=True)))  # steward runs never yield
    assert not _should_yield(ctx("INCREMENTAL", LB(boom=True)))  # a failed check never fails the run
    assert not _should_yield(ctx("INCREMENTAL", None))
