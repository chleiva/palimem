"""Unit tests of the matcher and the runner, using tiny synthetic fixtures and a fake implementation."""

from __future__ import annotations

import os
from typing import Any

import pytest

from . import runner
from .checks import compat_authority_coincide
from .matcher import MISSING, State, add_virtuals, match, resolve_deep
from .runner import (
    FAIL,
    PASS,
    SKIP,
    Outcome,
    ReferenceStub,
    run_all,
    run_scenario,
    summary,
)

# --- matcher -----------------------------------------------------------------------------------------------


def ok(exp: Any, act: Any) -> bool:
    return not match(exp, act)


def test_objects_match_as_subsets() -> None:
    assert ok({"a": 1}, {"a": 1, "b": 2})
    assert not ok({"a": 1, "c": 3}, {"a": 1, "b": 2})
    assert not ok({"a": 1}, {"a": 2})


def test_lists_match_in_order_and_length() -> None:
    assert ok([1, {"x": 1}], [1, {"x": 1, "y": 2}])
    assert not ok([1, 2], [2, 1])
    assert not ok([1], [1, 2])


def test_bool_is_not_int() -> None:
    assert not ok(True, 1) and not ok(1, True) and ok(True, True)


def test_any_and_absent() -> None:
    assert ok("$any", 0) and ok("$any", None) and not ok("$any", MISSING)
    assert ok({"k": "$absent"}, {}) and ok({"k": "$absent"}, {"k": None}) and not ok({"k": "$absent"}, {"k": 1})
    assert not ok({"k": "$any"}, {})


def test_operators() -> None:
    assert ok({"$unordered": [1, 2, 3]}, [3, 1, 2]) and not ok({"$unordered": [1, 2]}, [1, 1])
    assert ok({"$unordered": [{"a": 1}, {"a": 2}]}, [{"a": 2, "z": 0}, {"a": 1}])
    assert ok({"$contains": {"a": 1}}, [{"a": 0}, {"a": 1}]) and not ok({"$contains": 5}, [1, 2])
    assert ok({"$none": 5}, [1, 2]) and not ok({"$none": 1}, [1, 2])
    assert ok({"$len": 2}, [1, 2]) and not ok({"$len": 3}, [1, 2])
    assert ok({"$oneof": ["a", "b"]}, "b") and not ok({"$oneof": ["a", "b"]}, "c")
    assert ok({"$lte": 7}, 7) and not ok({"$lt": 7}, 7) and ok({"$gt": 7}, 8) and ok({"$gte": 8}, 8)
    assert ok({"$lte": 1}, 0) and not ok({"$lte": 1}, True)


def test_unordered_needs_a_perfect_matching() -> None:
    assert ok({"$unordered": [{"a": "$any"}, {"a": 1}]}, [{"a": 1}, {"a": 2}])
    assert not ok({"$unordered": [{"a": 1}, {"a": 1}]}, [{"a": 1}, {"a": 2}])


def test_reference_resolution_and_comparison() -> None:
    st = State()
    st.refs["r1"] = {"id": "ID1", "lsn": 3, "generation": 7}
    st.named["q1"] = {"justified": {"version": 4}, "rows": [{"id": "X"}]}
    assert resolve_deep("$r1", st) == "ID1" and resolve_deep("$r1.lsn", st) == 3
    assert resolve_deep("$q1.justified.version", st) == 4 and resolve_deep("$q1.rows.0.id", st) == "X"
    assert resolve_deep("$any", st) == "$any" and resolve_deep("$nope", st) == "$nope"
    assert ok(resolve_deep({"id": "$eq:$r1"}, st), {"id": "ID1"})
    assert not ok(resolve_deep({"id": "$eq:$r1"}, st), {"id": "ID2"})
    assert ok(resolve_deep({"n": "$eq:$r1.lsn"}, st), {"n": 3}) and not ok(resolve_deep({"n": "$eq:$r1.lsn"}, st), {"n": True})
    assert ok(resolve_deep({"v": "$not:$r1"}, st), {"v": "ID2"})
    assert match("$nope", "x"), "an unresolved reference is a failure, not a silent pass"


def test_after() -> None:
    assert ok("$after:2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z") and not ok("$after:2026-03-01T00:00:00Z", "2026-02-01T00:00:00Z")


def test_virtual_fields() -> None:
    a = {"justified": {"segment": {"established": {"id": "c1", "form": {}}, "alternatives": [{"id": "c2"}],
                                    "support": {"c1": [{"environment": ["b", "a"]}], "c2": [{"environment": ["c"]}, {"environment": ["d"]}]}}},
         "assertion": {"id": "c1"}, "alternatives": [{"id": "c2"}], "provenance": [{"environment": ["b", "a"]}, {"environment": ["c"]}]}
    v = add_virtuals(a)
    assert [c["id"] for c in v["_candidates"]] == ["c1", "c2"], "deduplicated by id"
    assert v["_environments"] == [["a", "b"], ["c"]] and v["_support_max_len"] == 2
    assert add_virtuals({"decision": "resource_limited"}) == {"decision": "resource_limited"}


# --- runner ------------------------------------------------------------------------------------------------


def scenario(ops: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    return {"id": "t", "gate": "G1", "area": "retraction", "kind": "scenario", "requires": [], "setup": {"profile": "open-world", "schema": {}},
            "ops": ops, **kw}


class Fake:
    """Returns canned results in order; records what it was sent."""

    name = "Fake"

    def __init__(self, results: list[Any], caps: set[str] | None = None, start: Any = None) -> None:
        self.results, self.sent, self.caps, self._start, self.closed = list(results), [], caps or set(), start, False

    def capabilities(self) -> set[str]:
        return self.caps

    def start(self, setup: dict[str, Any]) -> Any:
        return self._start

    def execute(self, op: dict[str, Any]) -> Any:
        self.sent.append(op)
        return self.results.pop(0)

    def close(self) -> None:
        self.closed = True


def test_reference_stub_skips_everything_executable() -> None:
    outs = run_all(ReferenceStub())
    assert not [o for o in outs if o.status == FAIL]
    scenarios = [o for o in outs if o.kind == "scenario" and o.status not in ("pending-decision", "shell")]
    assert scenarios and all(o.status == SKIP and "ReferenceStub" in o.reason for o in scenarios)
    assert {o.status for o in outs} <= {SKIP, runner.PENDING, runner.SHELL, runner.REF, PASS}


def test_refs_are_bound_and_substituted_in_later_ops() -> None:
    fx = scenario([
        {"op": "append", "ref": "r1", "report": {}, "expect": {"report_id": "$any"}},
        {"op": "query", "name": "q", "query": {"belief_as_of": "$r1.lsn"}, "expect": {"kernel_status": "unknown"}},
        {"op": "append", "ref": "r2", "report": {"target": "$r1"}, "expect": {"duplicate": False}},
    ])
    impl = Fake([{"report_id": "ID1", "lsn": 5, "generation": 2}, {"kernel_status": "unknown"}, {"report_id": "ID2", "lsn": 6, "duplicate": False}])
    o = run_scenario(fx, impl)
    assert o.status == PASS, o.failures
    assert impl.sent[1]["query"]["belief_as_of"] == 5 and impl.sent[2]["report"]["target"] == "ID1"
    assert impl.closed


def test_failures_are_reported_with_the_path() -> None:
    fx = scenario([{"op": "query", "name": "q", "query": {}, "expect": {"kernel_status": "established", "x": {"y": 1}}}])
    o = run_scenario(fx, Fake([{"kernel_status": "unknown", "x": {"y": 2}}]))
    assert o.status == FAIL and any("kernel_status" in f for f in o.failures) and any("x.y" in f for f in o.failures)


def test_not_implemented_op_is_a_skip_not_a_failure() -> None:
    o = run_scenario(scenario([{"op": "query", "name": "q", "query": {}}]), Fake([NotImplemented]))
    assert o.status == SKIP and "does not implement op 'query'" in o.reason


def test_missing_capability_is_a_skip() -> None:
    o = run_scenario(scenario([{"op": "query", "name": "q", "query": {}}], requires=["outbox"]), Fake([]))
    assert o.status == SKIP and "outbox" in o.reason


def test_expect_load_error() -> None:
    fx = scenario([], setup={"profile": "open-world", "schema": {}, "expect_load_error": {"reason": "x"}})
    assert run_scenario(fx, Fake([], start={"error": {"code": "refused"}})).status == PASS
    assert run_scenario(fx, Fake([], start=None)).status == FAIL


def test_repeat_expands_the_template() -> None:
    fx = scenario([{"op": "append", "ref": "a{i}", "repeat": 3, "report": {"v": "v{i}"}}])
    impl = Fake([{"report_id": f"I{i}", "lsn": i} for i in (1, 2, 3)])
    assert run_scenario(fx, impl).status == PASS
    assert [s["report"]["v"] for s in impl.sent] == ["v1", "v2", "v3"] and [s["ref"] for s in impl.sent] == ["a1", "a2", "a3"]


def test_equal_across_named_results() -> None:
    fx = scenario([{"op": "query", "name": "a", "query": {}}, {"op": "query", "name": "b", "query": {}}],
                  expect={"equal": [{"a": "a", "b": "b", "fields": ["x.y"]}]})
    assert run_scenario(fx, Fake([{"x": {"y": 1}}, {"x": {"y": 1}}])).status == PASS
    assert run_scenario(fx, Fake([{"x": {"y": 1}}, {"x": {"y": 2}}])).status == FAIL


def test_reference_subscriber_is_idempotent_on_event_id() -> None:
    ev = {"event_id": "E1", "plan_id": "p1"}
    fx = scenario([{"op": "deliver", "name": "d1"}, {"op": "deliver", "name": "d2"},
                   {"op": "subscriber_effects", "name": "fx", "expect": {"p1": {"events_seen": 2, "effects_applied": 1}}}])
    assert run_scenario(fx, Fake([{"events": [ev]}, {"events": [ev]}])).status == PASS


def test_summary_table_counts_by_gate_and_area() -> None:
    text = summary([Outcome("a", "G1", "retraction", "scenario", PASS), Outcome("b", "G1", "temporal", "scenario", SKIP),
                    Outcome("c", "G2", "outbox", "scenario", runner.PENDING)])
    assert "G1" in text and "G2" in text and "retraction" in text and "outbox" in text
    assert text.splitlines()[0].startswith("gate")


def test_cli_returns_zero_against_the_stub(capsys: pytest.CaptureFixture[str]) -> None:
    assert runner.main([]) == 0
    out = capsys.readouterr().out
    assert "implementation: ReferenceStub" in out and "pending-decision" in out


def test_compat_authority_check_runs_or_skips() -> None:
    ok_, msg = compat_authority_coincide()
    assert ok_ in (True, None), msg
    if os.environ.get("HARNESS_REQUIRED") == "1":  # the CI harness job has the frozen data: it must really run
        assert ok_ is True, msg
    if ok_:
        assert "0 crossings" in msg
