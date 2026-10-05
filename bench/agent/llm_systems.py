"""Memory systems for the LLM-in-the-loop RETRACT-ACT run (no network, no model, no cost in this module).

Every system implements the same adapter protocol (section 6 of docs/eval/AGENT_BENCHMARK.md) and nothing else differs
between them: what the agent model sees is the text returned by ``context``.

    class MemorySystem:
        name: str            # llm+lww, llm+palimem, llm+raw_log
        note: str            # the one paragraph of the system prompt that says how to read THIS memory
        def ingest(self, report: dict) -> str | None            # None = stored; a string = the memory's error text
        def context(self, dp: dict, as_of_report: str | None = None) -> str
        def close(self) -> None

``llm+lww``     the scripted last-write-wins answer of ``policies.lww`` for the used key.
``llm+palimem`` a real ``palimem.memory.Memory`` fed typed reports with identity bound by the host from the scenario's
                source registry (``palimem_system.bind_report``; ``text``, ``actor`` and ``origin`` fields are never
                read) and read through the **agent tool API** (``palimem.agent``: ``Host.bind_session`` then the
                ``recall`` tool), the text a tool-calling agent would receive.
``llm+raw_log`` every visible report's ``text`` with its day and source name, in order.

A report that palimem refuses to ingest (negative evidence has no kernel semantics yet) is not hidden: ``ingest``
returns the error text, and ``context`` shows it as a memory error notice, as an agent would see a tool error.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Protocol

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:  # direct use: python bench/agent/llm_agent.py
    sys.path.insert(0, str(HERE))

import palimem_system as _ps
import policies as _pol

from palimem.agent import Host
from palimem.agent.host import SessionContext
from palimem.compat import schema_from_kernel
from palimem.memory import Memory
from palimem.policy import PRESETS
from palimem.store import InMemoryBackend
from palimem.types import Profile, SemanticConfig

# ------------------------------------------------------------------------------------------- usage notes (frozen text)

NOTE_LWW = (
    "The memory below is a key-value store. It returns the most recent value stored for the key you need, "
    "or says that no value is stored."
)
NOTE_RAW_LOG = (
    "The memory below is the complete log of reports the memory has received so far, oldest first. Each line gives "
    "the day, the source that reported it and the text of the report. Reports can withdraw or correct earlier ones; "
    "you must work out what is currently justified."
)
# The product's own prompt fragment (docs/AGENT_GUIDE.md section 4), restated for this protocol, in which the harness
# has already called the `recall` tool for the key you need and shows you its result.
NOTE_PALIMEM = (
    "The memory below is the result of your justified memory's `recall` tool for the key you need.\n"
    "- If kernel_status is ESTABLISHED, you may act on it; if it says SINGLE ORIGIN, attribute it and remember that it "
    "is uncorroborated.\n"
    "- If it is UNRESOLVED, do not pick one of the alternatives yourself; if decision is ask, ask who can settle it.\n"
    "- If it is UNKNOWN, or the result is resource_limited, you do not know. Do not guess and do not use older values."
)


class MemorySystem(Protocol):
    name: str
    note: str

    def ingest(self, report: dict) -> str | None: ...

    def context(self, dp: dict, as_of_report: str | None = None) -> str: ...

    def close(self) -> None: ...


# --------------------------------------------------------------------------------------------------- llm+raw_log


class RawLog:
    name = "llm+raw_log"
    note = NOTE_RAW_LOG

    def __init__(self, scn: dict) -> None:
        self.scn = scn
        self.reports: list[dict] = []

    def ingest(self, report: dict) -> str | None:
        self.reports.append(report)
        return None

    def context(self, dp: dict, as_of_report: str | None = None) -> str:
        upto = self.reports
        if as_of_report is not None:
            ids = [r["id"] for r in self.reports]
            upto = self.reports[: ids.index(as_of_report) + 1] if as_of_report in ids else self.reports
        lines = ["[memory: full log of received reports]"]
        lines += [f"day {r['recorded_at']} | {r['source']['id']} | {r['text']}" for r in upto]
        if not upto:
            lines.append("(the log is empty)")
        return "\n".join(lines)

    def close(self) -> None:
        return None


# ----------------------------------------------------------------------------------------------------- llm+lww


class LwwStore:
    name = "llm+lww"
    note = NOTE_LWW

    def __init__(self, scn: dict) -> None:
        self.scn = scn
        self.reports: list[dict] = []

    def ingest(self, report: dict) -> str | None:
        self.reports.append(report)
        return None

    def context(self, dp: dict, as_of_report: str | None = None) -> str:
        upto = self.reports
        if as_of_report is not None:
            ids = [r["id"] for r in self.reports]
            upto = self.reports[: ids.index(as_of_report) + 1] if as_of_report in ids else self.reports
        k = dp["tool"]["use_key"]
        v = _pol.resolve(self.scn, k["attr"], k["entity"], upto)
        head = f"[memory: get {k['entity']}/{k['attr']}]"
        return f"{head}\n{v!r}" if v is not None else f"{head}\nno value stored"

    def close(self) -> None:
        return None


# -------------------------------------------------------------------------------------------------- llm+palimem


def iso(n: int) -> str:
    return _ps.day(n).isoformat().replace("+00:00", "Z")


class PalimemTools:
    name = "llm+palimem"
    note = NOTE_PALIMEM
    AGENT = "agent:assistant"

    def __init__(self, scn: dict, preset: str = "justified", self_update: bool = True) -> None:
        self.scn = scn
        self.clock = _ps._Clock()
        be = InMemoryBackend(clock=self.clock, store_secret=_ps.STORE_SECRET)
        ks = _ps.kernel_schema(scn)
        self.mem = Memory(
            be, schema_from_kernel(ks), kernel_schema=ks, entities=ks.entities,
            semantic=SemanticConfig(self_update=self_update, profile=Profile.OPEN_WORLD),
            admission=_ps.AdmissionConfig(profile=Profile.OPEN_WORLD), policy=PRESETS[preset],
        )
        self.host = Host(self.mem)
        self.tools = self.host.bind_session(SessionContext(session_id=f"retract-act:{scn['id']}", agent_principal=self.AGENT))
        self.ids: dict[str, str] = {}
        self.head_after: dict[str, int] = {}
        self.errors: dict[str, str] = {}
        self.warnings: list[str] = []

    def ingest(self, report: dict) -> str | None:
        self.clock.day = report["recorded_at"]
        err: str | None = None
        try:
            res = self.mem.append(_ps.bind_report(self.scn, report, self.ids, self.warnings),
                                  idempotency_key=f"{self.scn['id']}:{report['id']}")
            assert res.entry is not None and res.entry.report.id is not None
            self.ids[report["id"]] = res.entry.report.id
        except Exception as e:  # noqa: BLE001 - a contract gap (e.g. negative evidence): shown to the agent, never hidden
            err = f"{type(e).__name__}: {e}"
            self.errors[report["id"]] = err
        self.head_after[report["id"]] = self.mem.backend.head().lsn
        return err

    def context(self, dp: dict, as_of_report: str | None = None) -> str:
        k = dp["tool"]["use_key"]
        args: dict = {"query": {"entity": k["entity"], "attr": k["attr"]}}
        if dp.get("valid_at") is not None:
            args["valid_at"] = iso(dp["valid_at"])
        if as_of_report is not None:
            args["belief_as_of"] = self.head_after[as_of_report]
        out = self.tools.call("recall", args)
        lines = [f"[memory: recall {k['entity']}/{k['attr']}]"]
        seen = set(self.head_after) if as_of_report is None else self._upto(as_of_report)
        for rid, err in self.errors.items():
            if rid in seen:
                lines.append(f"memory error: report {rid} could not be stored ({err})")
        lines.append(out.text)
        return "\n".join(lines)

    def _upto(self, rid: str) -> set[str]:
        order = list(self.head_after)
        return set(order[: order.index(rid) + 1]) if rid in order else set(order)

    def close(self) -> None:
        self.mem.close()


def make_system(name: str, scn: dict) -> MemorySystem:
    if name == "llm+lww":
        return LwwStore(scn)
    if name == "llm+raw_log":
        return RawLog(scn)
    if name == "llm+palimem":
        return PalimemTools(scn)
    raise ValueError(f"unknown system {name!r}")


SYSTEM_NAMES = ("llm+lww", "llm+palimem", "llm+raw_log")
__all__ = ["SYSTEM_NAMES", "LwwStore", "MemorySystem", "PalimemTools", "RawLog", "iso", "make_system"]

