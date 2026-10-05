"""A minimal MCP server over stdio for the agent tool API (T-F3).

MCP is JSON-RPC 2.0, one message per line on stdin/stdout. This module implements the part a tool server needs
(``initialize``, ``ping``, ``tools/list``, ``tools/call``) with the standard library only, so it works offline and with
no extra installed. There is **no network listener**: the process serves the one client that launched it, and the
principal, source and session are fixed by whoever launched the server, never by the client's arguments.

What it exposes is the **agent tool API only** (``remember``, ``recall``, ``retract``, ``explain``, and ``dispute``
when granted). There is no tool to delete, merge, withdraw a source wholesale or change authority: those are host
operations (docs/THREAT_MODEL.md T-34..T-36). Read tools and write tools are registered separately: ``read_only``
removes every write tool from ``tools/list`` and refuses to run one.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, TextIO

from palimem import __version__
from palimem.agent import AgentTools, ToolSpec

SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = (
    "palimem is a justified memory. Use `recall` before relying on a fact. Read kernel_status and decision together: "
    "`established` means the evidence settles it; `unresolved` lists alternatives and you must not pick one yourself; "
    "`unknown` means no admissible evidence, so do not guess. If `single_origin` is true the answer rests on one origin "
    "group and is uncorroborated. `remember` records what YOU say; it is never evidence and never confirms anyone. "
    "`retract` only works on your own statements."
)

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32600, -32601, -32602, -32603


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


class McpServer:
    """One server, one bound agent session. ``handle`` is pure message-in, message-out (testable without a pipe)."""

    def __init__(self, tools: AgentTools, *, read_only: bool = False, name: str = "palimem") -> None:
        self.tools = tools
        self.read_only = read_only
        self.name = name
        self.initialised = False

    def _listed(self) -> dict[str, ToolSpec]:
        return {s.name: s for s in self.tools.tool_specs(read_only=self.read_only)}

    # ------------------------------------------------------------------ one message

    def handle(self, msg: Any) -> dict[str, Any] | None:
        """The response to one decoded message, or ``None`` for a notification."""
        if not isinstance(msg, Mapping) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            return _error(msg.get("id") if isinstance(msg, Mapping) else None, INVALID_REQUEST, "invalid request")
        method: str = msg["method"]
        is_notification = "id" not in msg
        msg_id = msg.get("id")
        params = msg.get("params") or {}
        if not isinstance(params, Mapping):
            return None if is_notification else _error(msg_id, INVALID_PARAMS, "params must be an object")
        try:
            result = self._dispatch(method, params)
        except _RpcError as e:
            return None if is_notification else _error(msg_id, e.code, str(e))
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _dispatch(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            wanted = params.get("protocolVersion")
            version = wanted if wanted in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
            self.initialised = True
            return {
                "protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.name, "version": __version__}, "instructions": INSTRUCTIONS,
            }
        if method in ("notifications/initialized", "notifications/cancelled"):
            return {}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [s.to_dict() for s in self._listed().values()]}
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str):
                raise _RpcError(INVALID_PARAMS, "tools/call needs a tool name")
            args = params.get("arguments", {})
            if not isinstance(args, Mapping):
                raise _RpcError(INVALID_PARAMS, "arguments must be an object")
            if name not in self._listed():
                raise _RpcError(INVALID_PARAMS, f"unknown tool: {name}")
            out = self.tools.call(name, args)
            return {
                "content": [{"type": "text", "text": out.text}], "structuredContent": out.data, "isError": out.is_error,
            }
        raise _RpcError(METHOD_NOT_FOUND, f"method not found: {method}")

    # ------------------------------------------------------------------ the stdio loop

    def serve(self, stdin: TextIO, stdout: TextIO) -> None:
        """Read one JSON message per line until EOF; write one response line per request."""
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                resp: dict[str, Any] | None = _error(None, PARSE_ERROR, "parse error")
            else:
                if isinstance(msg, list):
                    resp = _error(None, INVALID_REQUEST, "batches are not supported")
                else:
                    try:
                        resp = self.handle(msg)
                    except Exception as e:  # noqa: BLE001  (a server must not die on one bad call)
                        resp = _error(msg.get("id") if isinstance(msg, Mapping) else None, INTERNAL_ERROR, type(e).__name__)
            if resp is not None:
                stdout.write(json.dumps(resp, ensure_ascii=False, separators=(",", ":")) + "\n")
                stdout.flush()


class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
