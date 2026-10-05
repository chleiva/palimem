"""Entity merges as typed contract records (author ruling 2026-10-05, item 4, additive).

A merge is an admission-stage decision with its own id. It is *stored* as a marker report on the reserved attribute
``__entity_merge__`` (the storage mechanism of ``palimem.entities`` is unchanged), and two typed shapes describe it:

* :class:`MergeMarker`: the payload carried by the marker report (what the host decided). Payload version 2 is the
  typed form (``{"v":2,"op":...,"reason":...,"resolver":{...}}``); version 1, the earlier ad-hoc form with flat ``method``
  and ``score`` members, is still **read** and is mapped onto the same type, so logs written before this change keep
  loading. Writers emit version 2.
* :class:`MergeRecord`: what a reader gets back: the merge id, the entities it joined, the surviving representative,
  the reason, the resolver that proposed it (method and score), the admission version it was admitted under, and the id
  of the decision that reversed it, if any.

Authority to merge is the ``merge`` power (:attr:`palimem.types.enums.Power.MERGE`). It is a privileged host operation:
a rule may never grant it to an ``agent`` principal (S-07 invariant), and the agent tool API has no merge tool.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Self

from palimem.types._codec import (
    Codec,
    ValidationError,
    as_enum,
    as_float,
    as_int,
    as_list,
    as_obj,
    as_str,
    canonical_json,
    check_nat,
    check_nonempty,
    check_ulid,
    opt,
    set_field,
)

MARKER_VERSION = 2
LEGACY_MARKER_VERSION = 1
"""Payload version 1: flat ``method`` / ``score`` members (before the typed form). Read-only."""


class MergeOp(str, Enum):
    MERGE = "merge"
    UNMERGE = "unmerge"


@dataclass(frozen=True, kw_only=True)
class ResolverInfo(Codec):
    """Who proposed the decision: a method name, an optional similarity score and an optional resolver version."""

    method: str = "manual"
    score: float | None = None
    version: str | None = None

    def __post_init__(self) -> None:
        check_nonempty(self.method, "resolver.method")
        if self.score is not None:
            if isinstance(self.score, bool) or not isinstance(self.score, int | float):
                raise ValidationError("resolver.score: expected a number")
            if self.score != self.score or self.score in (float("inf"), float("-inf")):
                raise ValidationError("resolver.score: non-finite")
            set_field(self, "score", round(float(self.score), 6))
        if self.version is not None:
            check_nonempty(self.version, "resolver.version")

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"method": self.method}
        if self.score is not None:
            d["score"] = self.score
        if self.version is not None:
            d["version"] = self.version
        return d

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "resolver", ["method"], ["score", "version"])
        return cls(
            method=as_str(o["method"], "resolver.method"),
            score=opt(o.get("score"), lambda x: as_float(x, "resolver.score")),
            version=opt(o.get("version"), lambda x: as_str(x, "resolver.version")),
        )


@dataclass(frozen=True, kw_only=True)
class MergeMarker(Codec):
    """The typed payload of a merge marker report: one decision, ``merge`` or ``unmerge``.

    ``merge``: ``into`` names the entity the marker's own entity is merged into. ``unmerge``: ``target`` names the
    merge id (the marker report id of an earlier merge) being undone."""

    op: MergeOp
    reason: str
    into: str | None = None
    target: str | None = None
    resolver: ResolverInfo = ResolverInfo()

    def __post_init__(self) -> None:
        if not isinstance(self.op, MergeOp):
            raise ValidationError("merge_marker.op: not a MergeOp")
        check_nonempty(self.reason, "merge_marker.reason")
        if not isinstance(self.resolver, ResolverInfo):
            raise ValidationError("merge_marker.resolver: not a ResolverInfo")
        if self.op is MergeOp.MERGE:
            check_nonempty(self.into, "merge_marker.into")
            if self.target is not None:
                raise ValidationError("merge_marker: a merge does not carry a target")
        else:
            check_ulid(self.target, "merge_marker.target")
            if self.into is not None:
                raise ValidationError("merge_marker: an unmerge does not carry 'into'")

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"v": MARKER_VERSION, "op": self.op.value, "reason": self.reason, "resolver": self.resolver.to_dict()}
        if self.into is not None:
            d["into"] = self.into
        if self.target is not None:
            d["target"] = self.target
        return d

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        if not isinstance(d, dict):
            raise ValidationError("merge_marker: expected an object")
        v = d.get("v")
        if v == LEGACY_MARKER_VERSION:  # compat reader: flat method/score members (pre-typed payload)
            o = as_obj(d, "merge_marker", ["v", "op", "reason", "method"], ["into", "target", "score"])
            return cls(
                op=as_enum(MergeOp, o["op"], "merge_marker.op"), reason=as_str(o["reason"], "merge_marker.reason"),
                into=opt(o.get("into"), lambda x: as_str(x, "merge_marker.into")),
                target=opt(o.get("target"), lambda x: as_str(x, "merge_marker.target")),
                resolver=ResolverInfo(
                    method=as_str(o["method"], "merge_marker.method"),
                    score=opt(o.get("score"), lambda x: as_float(x, "merge_marker.score")),
                ),
            )
        if v != MARKER_VERSION:
            raise ValidationError(f"merge_marker.v: unsupported payload version {v!r}")
        o = as_obj(d, "merge_marker", ["v", "op", "reason", "resolver"], ["into", "target"])
        return cls(
            op=as_enum(MergeOp, o["op"], "merge_marker.op"), reason=as_str(o["reason"], "merge_marker.reason"),
            into=opt(o.get("into"), lambda x: as_str(x, "merge_marker.into")),
            target=opt(o.get("target"), lambda x: as_str(x, "merge_marker.target")),
            resolver=ResolverInfo.from_dict(o["resolver"]),
        )

    def to_text(self) -> str:
        """The canonical JSON text carried as the marker's ``member`` value."""
        return canonical_json(self.to_dict())

    @classmethod
    def from_text(cls, text: str) -> Self:
        try:
            body = json.loads(text)
        except ValueError as e:
            raise ValidationError(f"merge_marker: not JSON ({e})") from e
        return cls.from_dict(body)


@dataclass(frozen=True, kw_only=True)
class MergeRecord(Codec):
    """A merge as a reader sees it (contract type).

    ``members`` are the entities of the class this merge produced, as of the merge's own log position (sorted);
    ``representative`` is the entity whose key the class's beliefs are held under; ``admission_version`` is the version the
    marker was admitted under; ``reversed_by`` is the id of the unmerge decision that undid it, or ``None`` while active."""

    id: str
    members: tuple[str, ...]
    representative: str
    reason: str
    resolver: ResolverInfo
    admission_version: int
    reversed_by: str | None = None

    def __post_init__(self) -> None:
        check_ulid(self.id, "merge_record.id")
        if not isinstance(self.members, tuple) or len(self.members) < 2:
            raise ValidationError("merge_record.members: a merge joins at least two entities")
        for m in self.members:
            check_nonempty(m, "merge_record.members[]")
        if tuple(sorted(set(self.members))) != self.members:
            raise ValidationError("merge_record.members: must be sorted and without duplicates")
        check_nonempty(self.representative, "merge_record.representative")
        if self.representative not in self.members:
            raise ValidationError("merge_record.representative: must be one of the members")
        check_nonempty(self.reason, "merge_record.reason")
        if not isinstance(self.resolver, ResolverInfo):
            raise ValidationError("merge_record.resolver: not a ResolverInfo")
        check_nat(self.admission_version, "merge_record.admission_version", minimum=1)
        if self.reversed_by is not None:
            check_ulid(self.reversed_by, "merge_record.reversed_by")
            if self.reversed_by == self.id:
                raise ValidationError("merge_record: a merge cannot reverse itself")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "members": list(self.members), "representative": self.representative, "reason": self.reason,
            "resolver": self.resolver.to_dict(), "admission_version": self.admission_version, "reversed_by": self.reversed_by,
        }

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "merge_record", ["id", "members", "representative", "reason", "resolver", "admission_version"], ["reversed_by"])
        return cls(
            id=as_str(o["id"], "merge_record.id"),
            members=tuple(as_str(x, "merge_record.members[]") for x in as_list(o["members"], "merge_record.members")),
            representative=as_str(o["representative"], "merge_record.representative"),
            reason=as_str(o["reason"], "merge_record.reason"),
            resolver=ResolverInfo.from_dict(o["resolver"]),
            admission_version=as_int(o["admission_version"], "merge_record.admission_version"),
            reversed_by=opt(o.get("reversed_by"), lambda x: as_str(x, "merge_record.reversed_by")),
        )


__all__ = ["LEGACY_MARKER_VERSION", "MARKER_VERSION", "MergeMarker", "MergeOp", "MergeRecord", "ResolverInfo"]
