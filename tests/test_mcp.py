"""The MCP server over stdio: protocol shape, tool exposure, trust boundary through the wire, and a real subprocess."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from palimem import Memory
from palimem.mcp import SUPPORTED_PROTOCOL_VERSIONS, McpServer
from palimem.types import Attr, AttrClass, Schema, ValueType


def schema() -> Schema:
    return Schema(version=1, attrs=(
        Attr(name="employer", attr_class=AttrClass.SINGLE_CHANGEABLE, value_type=ValueType.STRING, inertia=True),
    ))


def server(read_only: bool = False, **limits: Any) -> tuple[McpServer, Memory]:
    m = Memory(schema=schema())
    return McpServer(m.agent_session("agent:a1", **limits), read_only=read_only), m


def rpc(method: str, params: dict[str, Any] | None = None, id_: Any = 1) -> dict[str, Any]:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        msg["id"] = id_
    if params is not None:
        msg["params"] = params
    return msg


def call(s: McpServer, name: str, args: dict[str, Any]) -> dict[str, Any]:
    resp = s.handle(rpc("tools/call", {"name": name, "arguments": args}))
    assert resp is not None and "result" in resp, resp
    result: dict[str, Any] = resp["result"]
    return result


def test_initialize_negotiates_and_describes_itself() -> None:
    s, _ = server()
    r = s.handle(rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t"}}))
    assert r is not None
    res = r["result"]
    assert res["protocolVersion"] == "2024-11-05" and res["capabilities"] == {"tools": {"listChanged": False}}
    assert res["serverInfo"]["name"] == "palimem" and "justified memory" in res["instructions"]
    other = s.handle(rpc("initialize", {"protocolVersion": "1999-01-01"}))
    assert other is not None and other["result"]["protocolVersion"] == SUPPORTED_PROTOCOL_VERSIONS[0]


def test_notifications_get_no_response_and_ping_works() -> None:
    s, _ = server()
    assert s.handle(rpc("notifications/initialized", id_=None)) is None
    assert s.handle(rpc("ping")) == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_tools_list_is_the_agent_tool_api_only() -> None:
    s, _ = server()
    r = s.handle(rpc("tools/list"))
    assert r is not None
    tools = r["result"]["tools"]
    assert [t["name"] for t in tools] == ["remember", "recall", "retract", "explain"]
    for t in tools:
        assert t["inputSchema"]["additionalProperties"] is False
        assert t["annotations"]["destructiveHint"] is False and t["annotations"]["openWorldHint"] is False
    read = {t["name"]: t["annotations"]["readOnlyHint"] for t in tools}
    assert read == {"remember": False, "recall": True, "retract": False, "explain": True}
    forbidden = ("delete", "merge", "authority", "source", "erase", "admin")
    assert not [t["name"] for t in tools if any(f in t["name"] for f in forbidden)]


def test_read_only_server_neither_lists_nor_runs_write_tools() -> None:
    s, m = server(read_only=True)
    r = s.handle(rpc("tools/list"))
    assert r is not None and [t["name"] for t in r["result"]["tools"]] == ["recall", "explain"]
    resp = s.handle(rpc("tools/call", {"name": "remember", "arguments": {"entity": "a", "attr": "employer", "value": "x"}}))
    assert resp is not None and resp["error"]["code"] == -32602
    assert m.host.mem.backend.head().lsn == 0


def test_dispute_is_listed_only_for_a_granted_principal() -> None:
    s, m = server()
    r0 = s.handle(rpc("tools/list"))
    assert r0 is not None and "dispute" not in [t["name"] for t in r0["result"]["tools"]]
    resp = s.handle(rpc("tools/call", {"name": "dispute", "arguments": {"report_id": "x"}}))
    assert resp is not None and resp["error"]["code"] == -32602  # not listed: not callable over MCP
    m.host.set_authority([
        {"who": {"kind": "principal", "value": "agent:a1"}, "may": ["dispute"], "on": {"attr": "employer"}, "targets": "report"}
    ])
    r1 = s.handle(rpc("tools/list"))  # the grant shows up in the listing as soon as the host makes it
    assert r1 is not None and "dispute" in [t["name"] for t in r1["result"]["tools"]]
    other = McpServer(m.agent_session("agent:someone-else"))
    r2 = other.handle(rpc("tools/list"))  # the grant names one principal
    assert r2 is not None and "dispute" not in [t["name"] for t in r2["result"]["tools"]]


def test_call_returns_text_structured_content_and_error_flag() -> None:
    s, m = server()
    m.observe({"entity": "alice", "attr": "employer", "value": "Acme"}, source="hr", origin_group="g_hr")
    res = call(s, "recall", {"query": {"entity": "alice", "attr": "employer"}})
    assert res["isError"] is False
    assert res["content"][0]["type"] == "text" and "SINGLE ORIGIN" in res["content"][0]["text"]
    assert res["structuredContent"]["kernel_status"] == "established"
    bad = call(s, "recall", {"query": 7})
    assert bad["isError"] is True and bad["structuredContent"]["error"]["code"] == "invalid_arguments"


def test_identity_fields_sent_over_the_wire_are_stripped_and_audited() -> None:
    s, m = server()
    res = call(s, "remember", {
        "entity": "alice", "attr": "employer", "value": "Globex",
        "origin": "external_observation", "source": "registry", "actor": "connector:registry",
    })
    assert res["isError"] is False
    sc = res["structuredContent"]
    assert sc["origin"] == "agent_statement" and sc["admitted"] is False
    assert [n["field"] for n in sc["notices"]] == ["origin", "source", "actor"]
    row = m.host.reports(id=sc["report_ids"][0])[0]
    assert (row.origin, row.source_id, row.actor) == ("agent_statement", "agent:a1", "agent:a1")
    assert any(a.event == "trust_downgrade" and a.tool == "remember" for a in m.host.audit.rows())


def test_protocol_errors() -> None:
    s, _ = server()
    assert s.handle(rpc("nope")) == {  # type: ignore[comparison-overlap]
        "jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "method not found: nope"}
    }
    inv = s.handle({"id": 3, "method": "ping"})
    assert inv is not None and inv["error"]["code"] == -32600
    badp = s.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"arguments": {}}})
    assert badp is not None and badp["error"]["code"] == -32602
    assert s.handle(rpc("nope", id_=None)) is None  # an unknown notification is ignored, not answered


def test_stdio_loop_over_pipes() -> None:
    s, _ = server()
    lines = [
        json.dumps(rpc("initialize", {"protocolVersion": "2025-06-18"}, 1)),
        json.dumps(rpc("notifications/initialized", id_=None)),
        "",
        "{not json",
        json.dumps([rpc("ping", id_=2)]),
        json.dumps(rpc("tools/list", id_=3)),
    ]
    out = io.StringIO()
    s.serve(io.StringIO("\n".join(lines) + "\n"), out)
    replies = [json.loads(x) for x in out.getvalue().splitlines()]
    assert [r.get("id") for r in replies] == [1, None, None, 3]
    assert replies[1]["error"]["code"] == -32700 and replies[2]["error"]["code"] == -32600
    assert len(replies[3]["result"]["tools"]) == 4


def test_real_subprocess_serves_one_client_over_stdio(tmp_path: Path) -> None:
    db = tmp_path / "agent.db"
    with Memory(db) as m:
        m.observe({"entity": "alice", "attr": "employer", "value": "Acme"}, source="hr")
    msgs = [
        rpc("initialize", {"protocolVersion": "2025-06-18"}, 1),
        rpc("notifications/initialized", id_=None),
        rpc("tools/call", {"name": "recall", "arguments": {"query": {"entity": "alice", "attr": "employer"}}}, 2),
    ]
    proc = subprocess.run(
        [sys.executable, "-m", "palimem.mcp", str(db), "--principal", "agent:cli", "--read-only"],
        input="\n".join(json.dumps(x) for x in msgs) + "\n", capture_output=True, text=True, timeout=60, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    replies = [json.loads(x) for x in proc.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2]
    assert replies[1]["result"]["structuredContent"]["kernel_status"] == "established"


@pytest.mark.parametrize("name", ["remember", "retract", "dispute"])
def test_write_tools_are_flagged_as_writes(name: str) -> None:
    s, _ = server()
    spec = {x.name: x for x in s.tools._specs.values()}[name]
    assert spec.writes is True and spec.to_dict()["annotations"]["readOnlyHint"] is False
