"""Merge decisions as records in the evidence log, and the entity classes they define at a log position.

A merge is an **admission-stage decision with its own id**. It is recorded as a *marker report* on the reserved
attribute ``__entity_merge__`` (the same representation the compat profile uses for source-level retraction, see
docs/PIPELINE.md section 3), so it gets everything the log gives for free: an LSN, the salted hash chain, an
idempotency key, crash safety, export/import and erasure. The marker's own report id *is* the merge id. Reversing a
merge is another marker (``unmerge``) naming the merge id; nothing in the log is ever edited.

A marker is **honoured** only when it comes from a host principal (actor kind ``system`` or ``user``, and a source id
of the same kinds), was admitted by admission, and decodes strictly. An agent cannot merge: whatever it writes on the
reserved attribute is ignored, and the agent tool API refuses the attribute outright.

The state at a log position is a pure function of the honoured markers up to it, so a ``belief_as_of`` query is
answered under the entity classes that were in force at that position.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from enum import Enum

from palimem.types import (
    AdmissionOutcome,
    Cue,
    LogEntry,
    MemberProp,
    Origin,
    PrincipalKind,
    Report,
    ValidationError,
)
from palimem.types.authority import principal_kind

ENTITY_MERGE_ATTR = "__entity_merge__"
MARKER_VERSION = 1
HOST_KINDS = frozenset({PrincipalKind.SYSTEM, PrincipalKind.USER})


class MergeOp(str, Enum):
    MERGE = "merge"
    UNMERGE = "unmerge"


@dataclass(frozen=True)
class MergeDecision:
    """One honoured decision. ``id`` is the marker's report id: the merge id of a ``merge``, the id of this decision
    for an ``unmerge`` (which names the merge it undoes in ``target``)."""

    id: str
    lsn: int
    op: MergeOp
    alias: str  # the entity merged away: the *representative* of its class at that position (merge); the marker's key entity (unmerge)
    into: str | None  # the representative it was merged into (merge)
    target: str | None  # the merge id undone (unmerge)
    reason: str
    method: str
    score: float | None
    proposer: str  # the marker's actor

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id, "lsn": self.lsn, "op": self.op.value, "alias": self.alias, "into": self.into,
            "target": self.target, "reason": self.reason, "method": self.method, "score": self.score,
            "proposer": self.proposer,
        }


# --------------------------------------------------------------------------- the marker encoding


def encode_marker(
    op: MergeOp, *, into: str | None = None, target: str | None = None, reason: str, method: str = "manual",
    score: float | None = None,
) -> str:
    """The canonical JSON text carried as the marker's ``member`` value."""
    body: dict[str, object] = {"v": MARKER_VERSION, "op": op.value, "reason": reason, "method": method}
    if op is MergeOp.MERGE:
        if not into:
            raise ValueError("a merge names the entity it is merged into")
        body["into"] = into
    else:
        if not target:
            raise ValueError("an unmerge names the merge it undoes")
        body["target"] = target
    if score is not None:
        body["score"] = round(float(score), 6)
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def decode_marker(report: Report) -> dict[str, object] | None:
    """The decoded body of a marker report, or ``None`` when the report is not a well-formed marker (it is then
    ignored by the registry: a malformed or foreign write on the reserved attribute has no effect)."""
    if report.key.attr != ENTITY_MERGE_ATTR or report.cue is not Cue.ASSERT:
        return None
    prop = report.proposition
    if not isinstance(prop, MemberProp) or not isinstance(prop.value, str):
        return None
    try:
        body = json.loads(prop.value)
    except ValueError:
        return None
    if not isinstance(body, dict) or body.get("v") != MARKER_VERSION:
        return None
    op = body.get("op")
    if op == "merge":
        into = body.get("into")
        if not isinstance(into, str) or not into or into == report.key.entity:
            return None
    elif op == "unmerge":
        if not isinstance(body.get("target"), str) or not body["target"]:
            return None
    else:
        return None
    if not isinstance(body.get("reason"), str) or not isinstance(body.get("method"), str):
        return None
    score = body.get("score")
    if score is not None and not isinstance(score, (int, float)):
        return None
    return body


def from_host(report: Report) -> bool:
    """A marker is honoured only from a host principal: the actor and the source id are both ``system:`` or ``user:``
    and the origin is an external observation (the host's own decision, never an agent's)."""
    if report.origin is not Origin.EXTERNAL_OBSERVATION:
        return False
    try:
        if principal_kind(report.actor) not in HOST_KINDS:
            return False
    except ValidationError:
        return False
    return report.source.id.split(":", 1)[0] in {k.value for k in HOST_KINDS}


# --------------------------------------------------------------------------- classes at a log position


@dataclass(frozen=True)
class Edge:
    merge_id: str
    alias: str
    into: str


class ClassState:
    """The entity classes in force at one log position: a forest of active merge edges, each class with one
    representative (the node with no outgoing edge)."""

    def __init__(self, edges: Sequence[Edge]) -> None:
        self.edges: tuple[Edge, ...] = tuple(edges)
        self._out: dict[str, Edge] = {e.alias: e for e in edges}
        self._rep_cache: dict[str, str] = {}
        adj: dict[str, set[str]] = {}
        for e in edges:
            adj.setdefault(e.alias, set()).add(e.into)
            adj.setdefault(e.into, set()).add(e.alias)
        self._adj = adj
        self._members: dict[str, tuple[str, ...]] = {}

    @property
    def empty(self) -> bool:
        return not self.edges

    def canon(self, entity: str) -> str:
        hit = self._rep_cache.get(entity)
        if hit is not None:
            return hit
        cur = entity
        seen = {cur}
        while cur in self._out:
            cur = self._out[cur].into
            if cur in seen:  # cannot happen (merges refuse a cycle); never loop on corrupt input
                break
            seen.add(cur)
        self._rep_cache[entity] = cur
        return cur

    def members(self, entity: str) -> tuple[str, ...]:
        """Every entity in the class of ``entity``, sorted (just ``(entity,)`` when it was never merged)."""
        rep = self.canon(entity)
        hit = self._members.get(rep)
        if hit is None:
            hit = tuple(sorted(self._component(rep)))
            self._members[rep] = hit
        return hit

    def _component(self, start: str, skip: str | None = None) -> set[str]:
        seen = {start}
        stack = [start]
        while stack:
            n = stack.pop()
            for m in self._adj.get(n, ()):
                if skip is not None and {n, m} == set(self._edge_nodes(skip)):
                    continue
                if m not in seen:
                    seen.add(m)
                    stack.append(m)
        return seen

    def _edge_nodes(self, merge_id: str) -> tuple[str, str]:
        for e in self.edges:
            if e.merge_id == merge_id:
                return (e.alias, e.into)
        raise KeyError(merge_id)

    def classes(self) -> dict[str, tuple[str, ...]]:
        """Representative -> members, for every class with more than one member."""
        out: dict[str, tuple[str, ...]] = {}
        for node in self._adj:
            rep = self.canon(node)
            if rep not in out:
                out[rep] = self.members(rep)
        return out

    def merge_pins(self, entity: str, contributors: Iterable[str]) -> tuple[str, ...]:
        """The merge ids the belief of the class *representative's* key consumed: an edge is pinned when some
        contributor (a member holding evidence for the attribute) lies on the alias side of it, i.e. its evidence
        reached the representative across that merge. A class whose evidence all sits on the representative pins
        nothing: no evidence crossed a merge, so reversing it recomputes nothing."""
        contrib = set(contributors)
        out: list[str] = []
        for e in self.edges:
            if e.alias not in self._adj or self.canon(e.alias) != self.canon(entity):
                continue
            if contrib & self._component(e.alias, skip=e.merge_id):
                out.append(e.merge_id)
        return tuple(sorted(out))


EMPTY_STATE = ClassState(())


def apply_ops(ops: Iterable[MergeDecision]) -> ClassState:
    """The class state after applying honoured decisions in log order. A merge between two entities already in one
    class, or an unmerge of an unknown or already-undone merge, is a recorded no-op (the decision stays in the log)."""
    edges: dict[str, Edge] = {}
    for op in ops:
        state = ClassState(list(edges.values()))
        if op.op is MergeOp.MERGE:
            assert op.into is not None
            a, b = state.canon(op.alias), state.canon(op.into)
            if a == b:
                continue
            edges[op.id] = Edge(merge_id=op.id, alias=a, into=b)
        else:
            assert op.target is not None
            edges.pop(op.target, None)
    return ClassState(list(edges.values()))


# --------------------------------------------------------------------------- the registry over the log


@dataclass
class MergeRegistry:
    """The honoured decisions of the committed log, in LSN order, kept incrementally (``sync`` scans only new
    rows). Pure function of the log: a rolled-back append never entered it, and an erasure or restore resets it."""

    ops: list[MergeDecision] = field(default_factory=list)
    scanned: int = 0
    _states: dict[int, ClassState] = field(default_factory=dict)

    def reset(self) -> None:
        self.ops.clear()
        self.scanned = 0
        self._states.clear()

    def decision_of(self, entry: LogEntry, outcome: AdmissionOutcome | None) -> MergeDecision | None:
        """The honoured decision a marker entry stands for, or ``None``."""
        r = entry.report
        if r.key.attr != ENTITY_MERGE_ATTR or r.id is None:
            return None
        if outcome is not AdmissionOutcome.ADMISSIBLE or not from_host(r):
            return None
        body = decode_marker(r)
        if body is None:
            return None
        op = MergeOp(str(body["op"]))
        score = body.get("score")
        return MergeDecision(
            id=r.id, lsn=entry.lsn, op=op, alias=r.key.entity,
            into=str(body["into"]) if op is MergeOp.MERGE else None,
            target=str(body["target"]) if op is MergeOp.UNMERGE else None,
            reason=str(body["reason"]), method=str(body["method"]),
            score=float(score) if isinstance(score, (int, float)) else None, proposer=r.actor,
        )

    def add(self, op: MergeDecision) -> None:
        if self.ops and op.lsn <= self.ops[-1].lsn:
            return
        self.ops.append(op)
        self._states.clear()

    def state_at(self, lsn: int | None = None) -> ClassState:
        """The classes in force after every honoured decision with ``decision.lsn <= lsn`` (``None``: all known)."""
        if not self.ops:
            return EMPTY_STATE
        n = len(self.ops) if lsn is None else sum(1 for o in self.ops if o.lsn <= lsn)
        if n == 0:
            return EMPTY_STATE
        hit = self._states.get(n)
        if hit is None:
            hit = self._states[n] = apply_ops(self.ops[:n])
        return hit

    def state_with(self, lsn_before: int, pending: MergeDecision | None) -> ClassState:
        base = [o for o in self.ops if o.lsn <= lsn_before]
        return apply_ops([*base, pending] if pending is not None else base)

    def active_merges(self, lsn: int | None = None) -> tuple[MergeDecision, ...]:
        state = self.state_at(lsn)
        ids = {e.merge_id for e in state.edges}
        return tuple(o for o in self.ops if o.op is MergeOp.MERGE and o.id in ids and (lsn is None or o.lsn <= lsn))

    def history(self, lsn: int | None = None) -> tuple[MergeDecision, ...]:
        return tuple(o for o in self.ops if lsn is None or o.lsn <= lsn)


def iter_markers(entries: Iterable[LogEntry]) -> Iterator[LogEntry]:
    for e in entries:
        if e.report.key.attr == ENTITY_MERGE_ATTR:
            yield e


__all__ = [
    "EMPTY_STATE",
    "ENTITY_MERGE_ATTR",
    "ClassState",
    "Edge",
    "MergeDecision",
    "MergeOp",
    "MergeRegistry",
    "apply_ops",
    "decode_marker",
    "encode_marker",
    "from_host",
    "iter_markers",
]
