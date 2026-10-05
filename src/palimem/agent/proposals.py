"""Attribute proposals (ruling 17 of 2026-10-05): an agent may never declare an attribute.

An unknown attribute named by an agent call (``remember(entity, attr, value)``) is **queued as a proposal** for the host.
The host sees the queue (``Memory.proposals()``, ``palimem proposals list``) and decides: ``accept`` declares the
attribute and records the queued fact as the agent's own report; ``reject`` drops it. Both decisions are host-only,
recorded acts: no tool the LLM can call reads, accepts or rejects a proposal, and the LLM is never shown a proposal id.

The queue is an append-only JSONL file next to the store (``<db>.proposals.jsonl``; in memory for ``:memory:``). State is
the replay of its events (``proposed``, ``accepted``, ``rejected``), so it survives a restart. The queued fact's *value* is
kept only while the proposal is pending: a decision redacts it from the file (``compact``), because a rejected value is
data the host chose not to record.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

ProposalStatus = Literal["pending", "accepted", "rejected"]
DEFAULT_MAX_PENDING_PER_SESSION = 10


class ProposalError(ValueError):
    """A proposal could not be queued or decided (unknown id, already decided, queue full)."""

    def __init__(self, message: str, *, code: str = "proposal_error") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Proposal:
    id: str
    attr: str
    entity: str
    value: Any
    kind: Literal["statement", "hypothesis"]
    agent_principal: str
    session_id: str
    request_id: str | None
    created_at: str
    status: ProposalStatus = "pending"
    decided_by: str | None = None
    decided_at: str | None = None
    reason: str | None = None
    attr_class: str | None = None
    applied_report_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "attr": self.attr, "entity": self.entity, "value": self.value, "kind": self.kind,
            "agent_principal": self.agent_principal, "session_id": self.session_id, "request_id": self.request_id,
            "created_at": self.created_at, "status": self.status, "decided_by": self.decided_by,
            "decided_at": self.decided_at, "reason": self.reason, "attr_class": self.attr_class,
            "applied_report_ids": list(self.applied_report_ids),
        }


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


class ProposalQueue:
    """The host-held queue of attribute proposals. Thread-safe; replays its file on open."""

    def __init__(self, path: str | Path | None = None, *, max_pending_per_session: int = DEFAULT_MAX_PENDING_PER_SESSION) -> None:
        self.path = None if path is None else Path(path)
        self.max_pending_per_session = max_pending_per_session
        self._lock = threading.RLock()
        self._items: dict[str, Proposal] = {}
        self._events: list[dict[str, Any]] = []
        if self.path is not None and self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._apply(json.loads(line))

    # ------------------------------------------------------------------ replay and persistence

    def _apply(self, ev: dict[str, Any]) -> None:
        self._events.append(ev)
        kind = ev["event"]
        if kind == "proposed":
            p = ev["proposal"]
            self._items[p["id"]] = Proposal(
                id=p["id"], attr=p["attr"], entity=p["entity"], value=p.get("value"), kind=p["kind"],
                agent_principal=p["agent_principal"], session_id=p["session_id"], request_id=p.get("request_id"),
                created_at=p["created_at"],
            )
        elif kind in ("accepted", "rejected"):
            cur = self._items[ev["id"]]
            self._items[cur.id] = replace(
                cur, status="accepted" if kind == "accepted" else "rejected", decided_by=ev["actor"], decided_at=ev["at"],
                reason=ev.get("reason"), attr_class=ev.get("attr_class"),
                applied_report_ids=tuple(ev.get("applied_report_ids", ())),
                value=None,  # a decision redacts the queued value from memory; `compact` redacts it from the file
            )

    def _append(self, ev: dict[str, Any]) -> None:
        self._apply(ev)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(ev, sort_keys=True) + "\n")

    def compact(self) -> None:
        """Rewrite the file without the queued values of decided proposals (atomic replace)."""
        with self._lock:
            decided = {i for i, p in self._items.items() if p.status != "pending"}
            kept: list[dict[str, Any]] = []
            for ev in self._events:
                if ev["event"] == "proposed" and ev["proposal"]["id"] in decided:
                    ev = {**ev, "proposal": {**ev["proposal"], "value": None, "redacted": True}}
                kept.append(ev)
            self._events = kept
            if self.path is None:
                return
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in kept), encoding="utf-8")
            os.replace(tmp, self.path)

    # ------------------------------------------------------------------ queue operations

    def propose(
        self, *, session_id: str, agent_principal: str, entity: str, attr: str, value: Any,
        kind: Literal["statement", "hypothesis"], request_id: str | None = None, max_pending: int | None = None,
    ) -> tuple[Proposal, bool]:
        """Queue a proposal. Returns ``(proposal, created)``: an identical pending proposal from the same session is
        returned as is (a retry must not multiply the queue). A session may hold only ``max_pending_per_session``."""
        value = _jsonable(value)
        with self._lock:
            for p in self._items.values():
                if (
                    p.status == "pending" and p.session_id == session_id and p.agent_principal == agent_principal
                    and (p.attr, p.entity, p.value, p.kind) == (attr, entity, value, kind)
                ):
                    return p, False
            pending = sum(1 for p in self._items.values() if p.status == "pending" and p.session_id == session_id)
            limit = self.max_pending_per_session if max_pending is None else max_pending
            if pending >= limit:
                raise ProposalError(
                    f"this session already has {pending} pending attribute proposals", code="rate_limited"
                )
            pid = "prop-" + uuid.uuid4().hex[:12]
            prop = {
                "id": pid, "attr": attr, "entity": entity, "value": value, "kind": kind,
                "agent_principal": agent_principal, "session_id": session_id, "request_id": request_id,
                "created_at": _now(),
            }
            self._append({"event": "proposed", "proposal": prop})
            return self._items[pid], True

    def get(self, proposal_id: str) -> Proposal:
        with self._lock:
            try:
                return self._items[proposal_id]
            except KeyError:
                raise ProposalError(f"unknown proposal {proposal_id!r}", code="proposal_unknown") from None

    def list(self, status: ProposalStatus | None = None) -> list[Proposal]:
        with self._lock:
            items = sorted(self._items.values(), key=lambda p: (p.created_at, p.id))
            return [p for p in items if status is None or p.status == status]

    def __iter__(self) -> Iterator[Proposal]:
        return iter(self.list())

    def decide(
        self, proposal_id: str, *, accepted: bool, actor: str, reason: str | None = None, attr_class: str | None = None,
        applied_report_ids: tuple[str, ...] = (),
    ) -> Proposal:
        with self._lock:
            cur = self.get(proposal_id)
            if cur.status != "pending":
                raise ProposalError(f"proposal {proposal_id!r} is already {cur.status}", code="proposal_decided")
            self._append({
                "event": "accepted" if accepted else "rejected", "id": proposal_id, "actor": actor, "at": _now(),
                "reason": reason, "attr_class": attr_class, "applied_report_ids": list(applied_report_ids),
            })
            self.compact()
            return self._items[proposal_id]


__all__ = ["DEFAULT_MAX_PENDING_PER_SESSION", "Proposal", "ProposalError", "ProposalQueue", "ProposalStatus"]
