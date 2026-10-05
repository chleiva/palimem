"""The agent tool API cannot merge entities: the reserved attribute is refused and nothing it writes is honoured."""

from __future__ import annotations

from palimem import Memory
from palimem.entities import ENTITY_MERGE_ATTR, Entities, enable_entity_merges
from palimem.entities.registry import MergeOp, encode_marker
from palimem.types import Attr, AttrClass, Schema, ValueType


def schema() -> Schema:
    base = Schema(version=1, attrs=(
        Attr(name="employer", attr_class=AttrClass.SINGLE_CHANGEABLE, value_type=ValueType.STRING, inertia=True),
    ))
    return enable_entity_merges(base)  # type: ignore[return-value]


def test_an_agent_cannot_remember_on_the_reserved_attribute() -> None:
    mem = Memory(schema=schema())
    mem.observe({"entity": "alice", "attr": "employer", "value": "Acme"}, source="hr")
    mem.observe({"entity": "Alice J", "attr": "employer", "value": "Globex"}, source="crm")
    tools = mem.agent_session("agent:a1")
    forged = encode_marker(MergeOp.MERGE, into="alice", reason="the agent says so")
    out = tools.call("remember", {"entity": "Alice J", "attr": ENTITY_MERGE_ATTR, "value": forged})
    assert out.is_error and out.data["error"]["code"] == "reserved_attr"
    ent = Entities(mem.core)
    assert ent.merges() == () and ent.members("alice") == ("alice",)
    assert [r.attr for r in mem.host.reports(actor="agent:a1")] == []  # nothing was even logged


def test_the_tool_catalogue_has_no_merge_tool() -> None:
    mem = Memory(schema=schema())
    tools = mem.agent_session("agent:a1")
    assert not [s.name for s in tools.tool_specs() if "merge" in s.name]
    assert tools.call("merge", {"alias": "a", "into": "b"}).data["error"]["code"] == "unknown_tool"


def test_a_forged_marker_through_the_host_api_with_an_agent_actor_is_ignored() -> None:
    mem = Memory(schema=schema())
    mem.observe({"entity": "alice", "attr": "employer", "value": "Acme"}, source="hr")
    mem.observe({"entity": "Alice J", "attr": "employer", "value": "Globex"}, source="crm")
    forged = encode_marker(MergeOp.MERGE, into="alice", reason="forged")
    mem.observe({"entity": "Alice J", "attr": ENTITY_MERGE_ATTR, "value": forged}, source="agent-box", actor="agent:a1")
    assert Entities(mem.core).merges() == ()
