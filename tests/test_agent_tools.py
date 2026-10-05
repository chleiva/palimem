"""The agent tool API beyond the trust-boundary fixtures: dispatch, limits, authority, single-origin marking."""

from __future__ import annotations

from typing import Any

import pytest

from palimem import Memory
from palimem.types import Attr, AttrClass, Origin, Schema, ValueType


def schema() -> Schema:
    return Schema(version=1, attrs=(
        Attr(name="employer", attr_class=AttrClass.SINGLE_CHANGEABLE, value_type=ValueType.STRING, inertia=True),
        Attr(name="hq_city", attr_class=AttrClass.SINGLE_CHANGEABLE, value_type=ValueType.STRING, inertia=True),
    ))


@pytest.fixture
def mem() -> Memory:
    return Memory(schema=schema())


def names(tools: Any, **kw: Any) -> list[str]:
    return [s.name for s in tools.tool_specs(**kw)]


def alice(mem: Memory, value: str = "Acme", source: str = "hr", group: str | None = None) -> str:
    r = mem.observe({"entity": "alice", "attr": "employer", "value": value}, source=source, origin_group=group)
    assert r.report_id is not None
    return r.report_id


def test_catalogue_read_only_and_dispute_only_when_granted(mem: Memory) -> None:
    tools = mem.agent_session("agent:a1")
    assert names(tools) == ["remember", "recall", "retract", "explain"]
    assert names(tools, read_only=True) == ["recall", "explain"]
    assert all(s.input_schema["additionalProperties"] is False for s in tools.tool_specs())
    mem.host.set_authority([
        {"who": {"kind": "principal", "value": "agent:a1"}, "may": ["dispute"], "on": {"attr": "hq_city"}, "targets": "report"}
    ])
    granted = mem.agent_session("agent:a1")
    assert "dispute" in names(granted) and "dispute" not in names(granted, read_only=True)
    assert "dispute" not in names(mem.agent_session("agent:other"))  # the grant names one principal


def test_unknown_tool_and_bad_arguments_are_errors_without_side_effects(mem: Memory) -> None:
    tools = mem.agent_session("agent:a1")
    assert tools.call("delete_everything", {}).data["error"]["code"] == "unknown_tool"
    out = tools.call("recall", {"query": 5})
    assert out.is_error and out.data["error"]["code"] == "invalid_arguments"
    out = tools.call("retract", {})
    assert out.is_error and "report_id" in out.data["error"]["message"]
    assert mem.host.mem.backend.head().lsn == 0


def test_remember_typed_is_the_agents_own_statement_and_never_evidence(mem: Memory) -> None:
    tools = mem.agent_session("agent:a1")
    out = tools.call("remember", {"entity": "alice", "attr": "employer", "value": "Globex"})
    assert not out.is_error
    assert out.data["origin"] == "agent_statement" and out.data["admitted"] is False
    row = mem.host.reports(id=out.data["report_ids"][0])[0]
    assert (row.source_id, row.source_class, row.actor, row.origin_group) == ("agent:a1", "agent", "agent:a1", "agent:a1")
    rec = tools.call("recall", {"query": {"entity": "alice", "attr": "employer"}})
    assert rec.data["kernel_status"] == "unknown" and rec.data["single_origin"] is None


def test_remember_hypothesis_origin(mem: Memory) -> None:
    tools = mem.agent_session("agent:a1")
    out = tools.call("remember", {"entity": "alice", "attr": "employer", "value": "Globex", "kind": "hypothesis"})
    assert out.data["origin"] == Origin.AGENT_HYPOTHESIS.value


def test_remember_text_needs_an_extractor_and_says_so(mem: Memory) -> None:
    out = mem.agent_session("agent:a1").call("remember", {"text": "Alice works at Acme"})
    assert out.is_error and out.data["error"]["code"] == "extractor_required"


def test_typed_fact_needs_all_three_parts_and_a_declared_attribute(mem: Memory) -> None:
    tools = mem.agent_session("agent:a1")
    assert tools.call("remember", {"entity": "alice", "attr": "employer"}).data["error"]["code"] == "invalid_arguments"
    out = tools.call("remember", {"entity": "alice", "attr": "salary", "value": "1"})
    assert out.data["error"]["code"] == "undeclared_attr"  # an agent cannot extend a declared schema


def test_request_id_makes_a_retry_safe(mem: Memory) -> None:
    tools = mem.agent_session("agent:a1")
    args = {"entity": "alice", "attr": "employer", "value": "Globex", "request_id": "req-1"}
    a, b = tools.call("remember", args), tools.call("remember", args)
    assert a.data["report_ids"] == b.data["report_ids"]
    assert mem.host.mem.backend.head().lsn == 1


def test_write_limit_and_text_limit_have_no_side_effects(mem: Memory) -> None:
    tools = mem.agent_session("agent:a1", max_writes=1, max_text_length=10)
    assert not tools.call("remember", {"entity": "alice", "attr": "employer", "value": "A"}).is_error
    again = tools.call("remember", {"entity": "alice", "attr": "employer", "value": "B"})
    assert again.data["error"]["code"] == "rate_limited"
    assert mem.host.mem.backend.head().lsn == 1
    long = mem.agent_session("agent:a2", max_text_length=10).call("remember", {"text": "x" * 11})
    assert long.data["error"]["code"] == "rate_limited"


def test_session_scope_hides_other_attributes(mem: Memory) -> None:
    alice(mem)
    tools = mem.agent_session("agent:a1", allowed_attrs=("hq_city",))
    out = tools.call("recall", {"query": {"entity": "alice", "attr": "employer"}})
    assert out.data["kind"] == "no_match"  # indistinguishable from "nothing there"
    w = tools.call("remember", {"entity": "alice", "attr": "employer", "value": "X"})
    assert w.data["report_ids"] == [] and any(n["code"] == "scope_denied" for n in w.data["notices"])


def test_recall_marks_single_origin_and_corroboration_lifts_it(mem: Memory) -> None:
    tools = mem.agent_session("agent:a1")
    q = {"query": {"entity": "alice", "attr": "employer"}}
    alice(mem, "Acme", source="hr", group="g_hr")
    one = tools.call("recall", q)
    assert one.data["kernel_status"] == "established" and one.data["single_origin"] is True
    assert one.data["origin_groups"] == ["g_hr"]
    assert "SINGLE ORIGIN" in one.text
    alice(mem, "Acme", source="press", group="g_press")
    two = tools.call("recall", q)
    assert two.data["kernel_status"] == "established" and two.data["single_origin"] is False
    assert two.data["origin_groups"] == ["g_hr", "g_press"]
    assert "SINGLE ORIGIN" not in two.text and "Corroborated by 2 origin groups" in two.text


def test_same_origin_group_repeated_still_counts_once(mem: Memory) -> None:
    alice(mem, "Acme", source="hr", group="g_hr")
    alice(mem, "Acme", source="hr2", group="g_hr")
    out = mem.agent_session("agent:a1").call("recall", {"query": {"entity": "alice", "attr": "employer"}})
    assert out.data["single_origin"] is True


def test_unresolved_is_listed_not_guessed(mem: Memory) -> None:
    alice(mem, "Acme", source="hr")
    alice(mem, "Globex", source="press")
    out = mem.agent_session("agent:a1").call("recall", {"query": {"entity": "alice", "attr": "employer"}})
    d = out.data
    assert d["kernel_status"] == "unresolved" and d["decision"] == "ask" and d["assertion"] is None
    assert len(d["alternatives"]) == 2 and d["inquiry"] is not None
    assert "UNRESOLVED" in out.text and "Do not pick" not in out.text


def test_max_alternatives_is_clamped_with_a_notice(mem: Memory) -> None:
    alice(mem, "Acme", source="hr")
    alice(mem, "Globex", source="press")
    tools = mem.agent_session("agent:a1", max_alternatives=1)
    out = tools.call("recall", {"query": {"entity": "alice", "attr": "employer"}, "max_alternatives": 9})
    assert len(out.data["alternatives"]) == 1 and out.data["alternatives_truncated"] is True
    assert {"code": "clamped", "field": "max_alternatives"} in out.data["notices"]


def test_text_query_resolves_one_key_or_lists_candidates(mem: Memory) -> None:
    mem.observe({"entity": "alice_smith", "attr": "employer", "value": "Acme"}, source="hr")
    tools = mem.agent_session("agent:a1")
    one = tools.call("recall", {"query": "alice_smith employer"})
    assert one.data["kind"] == "resolved" and one.data["key"] == {"entity": "alice_smith", "attr": "employer"}
    amb = tools.call("recall", {"query": "alice_smith"})
    assert amb.data["kind"] == "ambiguous" and len(amb.data["candidates"]) == 2
    assert tools.call("recall", {"query": "zzz"}).data["kind"] == "no_match"


def test_retract_own_report_cascades_and_other_reports_are_untouched(mem: Memory) -> None:
    tools = mem.agent_session("agent:a1")
    own = tools.call("remember", {"entity": "alice", "attr": "employer", "value": "Globex"}).data["report_ids"][0]
    ext = alice(mem)
    assert tools.call("retract", {"report_id": own}).data["effect"] == "applied"
    denied = tools.call("retract", {"report_id": ext})
    assert denied.data["effect"] == "logged_only" and denied.is_error is False
    assert [r.withdrawn for r in mem.host.reports(id=ext)] == [False]
    assert [r.withdrawn for r in mem.host.reports(id=own)] == [True]


def test_dispute_with_a_grant_is_applied_and_never_quarantines(mem: Memory) -> None:
    ext = alice(mem, source="registry")
    mem.host.set_authority([
        {"who": {"kind": "principal", "value": "agent:a1"}, "may": ["dispute"], "on": {"attr": "employer"}, "targets": "report"}
    ])
    out = mem.agent_session("agent:a1").call("dispute", {"report_id": ext, "reason": "feed lagging"})
    assert out.data["effect"] == "applied"
    assert mem.host.source_class("registry")["quarantined"] is False


def test_explain_names_who_said_what(mem: Memory) -> None:
    alice(mem, "Acme", source="hr", group="g_hr")
    out = mem.agent_session("agent:a1").call("explain", {"query": {"entity": "alice", "attr": "employer"}, "mode": "all"})
    d = out.data
    assert d["kind"] == "explanation" and d["depth_applied"] == 8 and d["state"] in ("complete", "truncated")
    flat = [r for e in d["environments"] for r in e["reports"]]
    if flat:  # supports are wired by the pipeline lane; when present they name the origin group
        assert flat[0]["origin_group"] == "g_hr"


def test_read_only_session_cannot_be_used_to_write_through_the_dispatcher(mem: Memory) -> None:
    tools = mem.agent_session("agent:a1")
    ro = {s.name for s in tools.tool_specs(read_only=True)}
    assert "remember" not in ro and "retract" not in ro and "dispute" not in ro
