"""Ruling 17 (2026-10-05): an agent may NEVER declare an attribute.

An unknown attribute named by an agent call is queued as a proposal for the host (``Memory.proposals()``), unless the host
pre-approved it (``allowed_attrs`` lists it) or opted the session into ``auto_declare``. Accepting or rejecting a proposal is a
host-only, recorded act; no tool the LLM can call reads, accepts or rejects one, and the LLM is never shown a proposal id.

The adversarial cases are the point: an LLM that tries to declare an attribute, to switch on ``auto_declare``, to widen its own
``allowed_attrs`` or to accept its own proposal gets nothing, and every attempt is audited.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from palimem import Memory
from palimem.agent import HostError, ProposalError, ProposalQueue
from palimem.cli import main as cli_main
from palimem.mcp import McpServer
from palimem.types import Attr, AttrClass, Schema, ValueType


def declared() -> Schema:
    return Schema(version=1, attrs=(
        Attr(name="employer", attr_class=AttrClass.SINGLE_CHANGEABLE, value_type=ValueType.STRING, inertia=True),
    ))


@pytest.fixture(params=["zero-config", "declared"])
def mem(request: pytest.FixtureRequest) -> Memory:
    return Memory() if request.param == "zero-config" else Memory(schema=declared())


def args(attr: str = "salary", value: str = "1", **extra: Any) -> dict[str, Any]:
    return {"entity": "alice", "attr": attr, "value": value, **extra}


def audit_events(m: Memory) -> list[str]:
    return [r.event for r in m.host.audit.rows()]


def declares(m: Memory, attr: str) -> bool:
    return any(a.name == attr for a in m.schema.attrs)


# ------------------------------------------------------------------ queueing


def test_an_unknown_attribute_is_queued_not_declared_and_nothing_is_recorded(mem: Memory) -> None:
    tools = mem.agent_session("agent:a1")
    out = tools.call("remember", args())
    assert not out.is_error
    assert out.data["queued"] is True and out.data["report_ids"] == [] and out.data["admitted"] is False
    assert [n["code"] for n in out.data["notices"]] == ["attr_queued"]
    assert "proposed to the host" in out.text and "Do not retry" in out.text
    assert not declares(mem, "salary")  # the agent did not declare it
    assert mem.core.backend.head().lsn == 0  # and nothing was recorded
    assert [(p.attr, p.entity, p.value, p.agent_principal) for p in mem.proposals("pending")] == [
        ("salary", "alice", "1", "agent:a1")
    ]
    assert "attr_proposed" in audit_events(mem)


def test_the_llm_is_never_shown_a_proposal_id(mem: Memory) -> None:
    out = mem.agent_session("agent:a1").call("remember", args())
    pid = mem.proposals("pending")[0].id
    assert pid not in json.dumps(out.data) and pid not in out.text


def test_an_identical_retry_does_not_multiply_the_queue(mem: Memory) -> None:
    tools = mem.agent_session("agent:a1")
    for _ in range(3):
        tools.call("remember", args(request_id="r1"))
    assert len(mem.proposals("pending")) == 1
    tools.call("remember", args(value="2"))
    assert len(mem.proposals("pending")) == 2


def test_a_session_can_only_hold_so_many_pending_proposals(mem: Memory) -> None:
    tools = mem.agent_session("agent:a1", max_proposals=2)
    assert not tools.call("remember", args("a1")).is_error
    assert not tools.call("remember", args("a2")).is_error
    third = tools.call("remember", args("a3"))
    assert third.is_error and third.data["error"]["code"] == "rate_limited"
    assert len(mem.proposals("pending")) == 2


def test_a_reserved_attribute_is_refused_not_queued(mem: Memory) -> None:
    out = mem.agent_session("agent:a1").call("remember", args("__entity_merge__"))
    assert out.is_error and out.data["error"]["code"] == "reserved_attr"
    assert mem.proposals() == []


def test_a_declared_attribute_is_recorded_as_before() -> None:
    m = Memory(schema=declared())
    out = m.agent_session("agent:a1").call("remember", args("employer", "Acme"))
    assert len(out.data["report_ids"]) == 1 and "queued" not in out.data
    assert m.proposals() == []


# ------------------------------------------------------------------ the host decides


def test_accept_declares_the_attribute_and_records_the_fact_as_the_agents_own_statement(mem: Memory) -> None:
    mem.agent_session("agent:a1").call("remember", args())
    pid = mem.proposals("pending")[0].id
    done = mem.accept_proposal(pid, attr_class="single_changeable", actor="user:alice")
    assert declares(mem, "salary") and done.status == "accepted" and done.decided_by == "user:alice"
    assert done.value is None  # the queued value is redacted once decided
    assert len(done.applied_report_ids) == 1
    rows = mem.host.reports(id=done.applied_report_ids[0])
    assert len(rows) == 1 and rows[0].actor == "agent:a1" and rows[0].origin == "agent_statement"
    assert rows[0].source_class == "agent" and rows[0].origin_group == "agent:a1"
    events = audit_events(mem)
    assert "attr_declared" in events and "attr_accepted" in events
    with pytest.raises(ProposalError) as e:
        mem.accept_proposal(pid, actor="user:alice")
    assert e.value.code == "proposal_decided"


def test_accept_without_apply_declares_but_records_nothing(mem: Memory) -> None:
    mem.agent_session("agent:a1").call("remember", args())
    done = mem.accept_proposal(mem.proposals("pending")[0].id, apply=False)
    assert declares(mem, "salary") and done.applied_report_ids == () and mem.core.backend.head().lsn == 0


def test_reject_keeps_the_attribute_undeclared_and_redacts_the_value(mem: Memory) -> None:
    mem.agent_session("agent:a1").call("remember", args(value="SECRET-VALUE-42"))
    pid = mem.proposals("pending")[0].id
    done = mem.reject_proposal(pid, reason="not an attribute we track", actor="user:alice")
    assert done.status == "rejected" and done.reason == "not an attribute we track" and done.value is None
    assert not declares(mem, "salary") and mem.core.backend.head().lsn == 0
    assert "attr_rejected" in audit_events(mem)


def test_only_a_user_or_system_principal_may_decide(mem: Memory) -> None:
    mem.agent_session("agent:a1").call("remember", args())
    pid = mem.proposals("pending")[0].id
    for bad in ("agent:a1", "connector:feed"):
        with pytest.raises(HostError) as e:
            mem.accept_proposal(pid, actor=bad)
        assert e.value.code == "not_authorised"
        with pytest.raises(HostError):
            mem.reject_proposal(pid, actor=bad)
    assert not declares(mem, "salary") and mem.proposals("pending")[0].status == "pending"


def test_an_unknown_proposal_id_is_an_error(mem: Memory) -> None:
    with pytest.raises(ProposalError) as e:
        mem.accept_proposal("prop-nope")
    assert e.value.code == "proposal_unknown"


# ------------------------------------------------------------------ host pre-approval and opt-in


def test_allowed_attrs_is_the_hosts_pre_approval(mem: Memory) -> None:
    tools = mem.agent_session("agent:a1", allowed_attrs=("salary",))
    out = tools.call("remember", args("salary"))
    assert declares(mem, "salary") and len(out.data["report_ids"]) == 1 and mem.proposals() == []
    ev = [r for r in mem.host.audit.rows() if r.event == "attr_declared"]
    assert len(ev) == 1 and ev[0].detail["reason"] == "allowed_attrs"
    outside = tools.call("remember", args("bonus"))  # not listed: out of scope, neither declared nor queued
    assert [n["code"] for n in outside.data["notices"]] == ["scope_denied"]
    assert not declares(mem, "bonus") and mem.proposals() == []


def test_auto_declare_is_a_host_opt_in_for_that_session_only(mem: Memory) -> None:
    out = mem.agent_session("agent:a1", auto_declare=True).call("remember", args("salary"))
    assert declares(mem, "salary") and len(out.data["report_ids"]) == 1 and mem.proposals() == []
    ev = [r for r in mem.host.audit.rows() if r.event == "attr_declared"]
    assert ev[0].detail["reason"] == "auto_declare"
    other = mem.agent_session("agent:a2").call("remember", args("bonus"))  # another session: still queued
    assert other.data["queued"] is True and not declares(mem, "bonus")


def test_an_auto_declared_attribute_is_a_multi_valued_open_set(mem: Memory) -> None:
    mem.agent_session("agent:a1", auto_declare=True).call("remember", args("salary"))
    a = mem.schema.attr("salary")
    assert a.attr_class is AttrClass.MULTI_SET


# ------------------------------------------------------------------ adversarial: an LLM tries to get around it


def test_an_llm_cannot_declare_switch_on_auto_declare_or_widen_its_own_scope(mem: Memory) -> None:
    tools = mem.agent_session("agent:a1")
    out = tools.call(
        "remember",
        args(auto_declare=True, allowed_attrs=["salary"], declare=True, declare_attr="salary", accept=True,
             proposal_id="prop-x", attr_class="single_changeable", actor="user:alice"),
    )
    ignored = {n["field"] for n in out.data["notices"] if n["code"] == "field_ignored"}
    assert {"auto_declare", "allowed_attrs", "declare", "declare_attr", "accept", "proposal_id", "attr_class", "actor"} <= ignored
    assert out.data["queued"] is True and out.data["report_ids"] == []
    assert not declares(mem, "salary") and mem.core.backend.head().lsn == 0
    rows = [r for r in mem.host.audit.rows() if r.event == "trust_downgrade"]
    assert rows and {"auto_declare", "allowed_attrs", "accept"} <= set(rows[-1].fields)
    assert tools.ctx.auto_declare is False and tools.ctx.allowed_attrs is None  # the host's binding is unchanged


@pytest.mark.parametrize(
    "tool", ["accept_proposal", "reject_proposal", "declare_attribute", "declare", "proposals", "list_proposals", "set_policy"]
)
def test_there_is_no_tool_to_declare_or_to_decide_a_proposal(mem: Memory, tool: str) -> None:
    tools = mem.agent_session("agent:a1")
    tools.call("remember", args())
    out = tools.call(tool, {"proposal_id": mem.proposals("pending")[0].id, "attr": "salary", "class": "single_changeable"})
    assert out.is_error and out.data["error"]["code"] == "unknown_tool"
    assert not declares(mem, "salary") and mem.proposals("pending")[0].status == "pending"


def test_no_tool_schema_has_a_declaration_or_proposal_field(mem: Memory) -> None:
    banned = {"declare", "auto_declare", "allowed_attrs", "accept", "reject", "proposal", "proposal_id", "attr_class"}
    for spec in mem.agent_session("agent:a1").tool_specs():
        assert not (set(spec.input_schema["properties"]) & banned), spec.name
        assert "propos" not in spec.description.lower() or spec.name == "remember"


def test_recall_does_not_leak_the_queue(mem: Memory) -> None:
    tools = mem.agent_session("agent:a1")
    tools.call("remember", args(value="SECRET-VALUE-42"))
    out = tools.call("recall", {"query": {"entity": "alice", "attr": "salary"}})
    assert "SECRET-VALUE-42" not in json.dumps(out.data) and "SECRET-VALUE-42" not in out.text
    assert "prop-" not in json.dumps(out.data)


def test_the_mcp_server_exposes_no_declare_or_proposal_tool_and_queues_through_the_wire(mem: Memory) -> None:
    server = McpServer(mem.agent_session("agent:a1"))
    listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert listed is not None
    names = {t["name"] for t in listed["result"]["tools"]}
    assert names <= {"remember", "recall", "retract", "explain", "dispute"} and not {"declare", "accept_proposal"} & names
    resp = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                          "params": {"name": "remember", "arguments": args(auto_declare=True)}})
    assert resp is not None
    text = resp["result"]["content"][0]["text"]
    assert "proposed to the host" in text and not declares(mem, "salary") and len(mem.proposals("pending")) == 1


# ------------------------------------------------------------------ persistence and the queue itself


def test_the_queue_survives_a_restart_and_a_decision_redacts_the_file(tmp_path: Path) -> None:
    db = tmp_path / "pm.db"
    first = Memory(db)
    first.agent_session("agent:a1").call("remember", args(value="SECRET-VALUE-42"))
    first.agent_session("agent:a1").call("remember", args("bonus", "KEEP-ME"))
    first.close()
    again = Memory(db)
    pending = {p.attr: p for p in again.proposals("pending")}
    assert set(pending) == {"salary", "bonus"} and pending["salary"].value == "SECRET-VALUE-42"
    again.reject_proposal(pending["salary"].id, actor="user:alice")
    again.close()
    text = Path(f"{db}.proposals.jsonl").read_text(encoding="utf-8")
    assert "SECRET-VALUE-42" not in text  # a decided proposal's value is not kept
    assert "KEEP-ME" in text  # a pending one is
    third = Memory(db)
    assert [p.attr for p in third.proposals("pending")] == ["bonus"]
    assert [p.status for p in third.proposals("rejected")] == ["rejected"]
    third.close()


def test_the_queue_in_isolation(tmp_path: Path) -> None:
    q = ProposalQueue(tmp_path / "q.jsonl", max_pending_per_session=1)
    p, created = q.propose(session_id="s", agent_principal="agent:a", entity="e", attr="x", value="v", kind="statement")
    assert created
    same, again = q.propose(session_id="s", agent_principal="agent:a", entity="e", attr="x", value="v", kind="statement")
    assert same.id == p.id and not again
    with pytest.raises(ProposalError):
        q.propose(session_id="s", agent_principal="agent:a", entity="e", attr="y", value="v", kind="statement")
    q.propose(session_id="other", agent_principal="agent:a", entity="e", attr="y", value="v", kind="hypothesis")
    q.decide(p.id, accepted=False, actor="user:x")
    assert [i.status for i in q.list()] == ["rejected", "pending"]


# ------------------------------------------------------------------ the command line


def run(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    return cli_main(argv, out=buf), buf.getvalue()


def test_the_cli_lists_accepts_and_rejects_proposals(tmp_path: Path) -> None:
    db = str(tmp_path / "pm.db")
    m = Memory(db)
    m.agent_session("agent:a1").call("remember", args("salary", "100"))
    m.agent_session("agent:a1").call("remember", args("bonus", "5"))
    m.close()
    code, listing = run(["proposals", "list", db])
    assert code == 0 and "salary" in listing and "bonus" in listing and "pending" in listing
    ids = {line.split()[3]: line.split()[0] for line in listing.splitlines()}
    code, msg = run(["proposals", "accept", db, ids["salary"], "--class", "single_changeable", "--actor", "user:alice"])
    assert code == 0 and "declared salary" in msg and "recorded 1 report" in msg
    code, msg = run(["proposals", "reject", db, ids["bonus"], "--reason", "no", "--actor", "user:alice"])
    assert code == 0 and "stays undeclared" in msg
    code, after = run(["proposals", "list", db, "--status", "all", "--json"])
    rows = {r["attr"]: r for r in json.loads(after)}
    assert rows["salary"]["status"] == "accepted" and rows["bonus"]["status"] == "rejected"
    assert rows["bonus"]["value"] is None and rows["salary"]["attr_class"] == "single_changeable"
    reopened = Memory(db)
    assert declares(reopened, "salary") and not declares(reopened, "bonus")
    reopened.close()


def test_the_cli_refuses_an_agent_actor_and_a_missing_id(tmp_path: Path) -> None:
    db = str(tmp_path / "pm.db")
    m = Memory(db)
    m.agent_session("agent:a1").call("remember", args())
    pid = m.proposals("pending")[0].id
    m.close()
    assert run(["proposals", "accept", db, pid, "--actor", "agent:a1"])[0] == 2
    assert run(["proposals", "accept", db])[0] == 2
    again = Memory(db)
    assert not declares(again, "salary") and again.proposals("pending")[0].status == "pending"
    again.close()


def test_the_mcp_command_has_a_host_side_auto_declare_flag() -> None:
    from palimem.cli import build_parser

    ns = build_parser().parse_args(["mcp", "x.db", "--principal", "agent:a", "--auto-declare"])
    assert ns.auto_declare is True
    assert build_parser().parse_args(["mcp", "x.db", "--principal", "agent:a"]).auto_declare is False
