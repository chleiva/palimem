"""Support, Segment, Belief and BeliefView (design v0.3 §Data and API: Belief record).

A belief is a timeline of :class:`Segment` objects, each with its own kernel status; there is no
key-level status. Every justification (:class:`Support`) carries its own valid interval.

Additions and placements that differ from the design's field list, all from author decisions:

* ``Belief.lsn`` (S-05): the log sequence number that produced this version; ``belief_as_of``
  resolves to the version with the greatest ``lsn`` at or before the requested one. The design
  only had ``recorded_at``, which is now metadata.
* ``Candidate`` carries its ``key`` (needed to derive and verify its stable id).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from itertools import pairwise
from types import MappingProxyType
from typing import Any, Self

from palimem.types._codec import (
    Codec,
    ValidationError,
    as_bool,
    as_enum,
    as_int,
    as_obj,
    as_str,
    canonical_json,
    check_nat,
    check_nonempty,
    check_order,
    check_ulid,
    norm_ts,
    opt,
    opt_ts_str,
    req_ts,
    set_field,
    ts_from_str,
    ts_to_str,
    tuple_of,
)
from palimem.types.enums import InvalidatedKind, KernelStatus, Precision, Profile
from palimem.types.values import (
    NEGATIVE_FORM_TYPES,
    BeliefOfForm,
    Candidate,
    EmptyForm,
    Key,
    SetForm,
    ValueForm,
)


@dataclass(frozen=True, kw_only=True)
class Support(Codec):
    """One justification and when it applies: a subset-minimal environment of **base** report ids
    (derivation pins are explanatory, not evidence; S-12, decided)."""

    environment: tuple[str, ...]
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    precision: Precision = Precision.DAY

    def __post_init__(self) -> None:
        if not self.environment:
            raise ValidationError("support.environment must name at least one report")
        for i, r in enumerate(self.environment):
            check_ulid(r, f"support.environment[{i}]")
        set_field(self, "environment", tuple(sorted(set(self.environment))))
        set_field(self, "valid_from", norm_ts(self.valid_from, "support.valid_from"))
        set_field(self, "valid_to", norm_ts(self.valid_to, "support.valid_to"))
        check_order(self.valid_from, self.valid_to, "support")
        if not isinstance(self.precision, Precision):
            raise ValidationError("support.precision: not a Precision")

    def to_dict(self) -> dict[str, Any]:
        return {
            "environment": list(self.environment),
            "valid_from": opt_ts_str(self.valid_from),
            "valid_to": opt_ts_str(self.valid_to),
            "precision": self.precision.value,
        }

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "support", ["environment"], ["valid_from", "valid_to", "precision"])
        return cls(
            environment=tuple_of(o["environment"], "support.environment", as_str),
            valid_from=opt(o.get("valid_from"), lambda x: ts_from_str(x, "support.valid_from")),
            valid_to=opt(o.get("valid_to"), lambda x: ts_from_str(x, "support.valid_to")),
            precision=as_enum(Precision, o.get("precision", "day"), "support.precision"),
        )


_POSITIVE_ESTABLISHED = (ValueForm, SetForm, BeliefOfForm)


def _empty_support() -> Mapping[str, tuple[Support, ...]]:
    return MappingProxyType({})


@dataclass(frozen=True, kw_only=True)
class Segment(Codec):
    """A maximal valid-time interval ``[valid_from, valid_to)`` with one answer."""

    valid_from: datetime | None
    valid_to: datetime | None
    kernel_status: KernelStatus
    established: Candidate | None = None
    alternatives: tuple[Candidate, ...] = ()
    support: Mapping[str, tuple[Support, ...]] = field(default_factory=_empty_support)

    def __post_init__(self) -> None:
        set_field(self, "valid_from", norm_ts(self.valid_from, "segment.valid_from"))
        set_field(self, "valid_to", norm_ts(self.valid_to, "segment.valid_to"))
        if self.valid_from is not None and self.valid_to is not None and not self.valid_from < self.valid_to:
            raise ValidationError("segment: valid_from must be strictly before valid_to")
        if not isinstance(self.kernel_status, KernelStatus):
            raise ValidationError("segment.kernel_status: not a KernelStatus")
        st = self.kernel_status
        est, alts = self.established, self.alternatives
        if st in (KernelStatus.ESTABLISHED, KernelStatus.ESTABLISHED_EMPTY, KernelStatus.ESTABLISHED_FALSE):
            if est is None:
                raise ValidationError(f"segment: status '{st.value}' requires an established candidate")
            if alts:
                raise ValidationError(f"segment: status '{st.value}' must not list alternatives")
            form = est.form
            if st is KernelStatus.ESTABLISHED and not isinstance(form, _POSITIVE_ESTABLISHED):
                raise ValidationError("segment: 'established' needs a value, set or belief_of candidate")
            if st is KernelStatus.ESTABLISHED_EMPTY and not isinstance(form, EmptyForm):
                raise ValidationError("segment: 'established_empty' needs the 'empty' candidate")
            if st is KernelStatus.ESTABLISHED_FALSE and not isinstance(form, NEGATIVE_FORM_TYPES):
                raise ValidationError("segment: 'established_false' needs a not_value or not_member candidate")
        elif st is KernelStatus.UNRESOLVED:
            if est is not None:
                raise ValidationError("segment: 'unresolved' must not name an established candidate")
            if len(alts) < 2:
                raise ValidationError("segment: 'unresolved' lists at least two alternatives")
        else:  # UNKNOWN
            # No established candidate. Alternatives are allowed only as *constraints* (author ruling of 2026-10-05,
            # negative evidence): denials such as ``not_value(Acme)`` and ``not_value(Globex)`` narrow what the value can
            # be without determining it, so the status is `unknown` and the compatible negatives are listed.
            if est is not None:
                raise ValidationError("segment: 'unknown' names no established candidate")
            if not all(isinstance(c, Candidate) and isinstance(c.form, NEGATIVE_FORM_TYPES) for c in alts):
                raise ValidationError("segment: an 'unknown' segment may list only negative candidates (constraints)")
        cands = ([est] if est is not None else []) + list(alts)
        for c in cands:
            if not isinstance(c, Candidate):
                raise ValidationError("segment: candidates must be Candidate objects")
        ids = [c.id for c in cands]
        if len(set(ids)) != len(ids):
            raise ValidationError("segment: duplicate candidates")
        if len({c.key for c in cands}) > 1:
            raise ValidationError("segment: all candidates must belong to one key")
        sup: dict[str, tuple[Support, ...]] = {}
        for cid in sorted(self.support):
            sl = tuple(self.support[cid])
            if cid not in ids:
                raise ValidationError(f"segment.support: '{cid}' is not a candidate of this segment")
            if not sl:
                raise ValidationError(f"segment.support: candidate '{cid}' has an empty support list")
            if not all(isinstance(s, Support) for s in sl):
                raise ValidationError("segment.support: values must be Support objects")
            sup[cid] = sl
        set_field(self, "support", MappingProxyType(sup))

    def __hash__(self) -> int:
        return hash(canonical_json(self.to_dict()))

    @property
    def key(self) -> Key | None:
        cands = ([self.established] if self.established is not None else []) + list(self.alternatives)
        return cands[0].key if cands else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid_from": opt_ts_str(self.valid_from),
            "valid_to": opt_ts_str(self.valid_to),
            "kernel_status": self.kernel_status.value,
            "established": None if self.established is None else self.established.to_dict(),
            "alternatives": [c.to_dict() for c in self.alternatives],
            "support": {cid: [s.to_dict() for s in self.support[cid]] for cid in sorted(self.support)},
        }

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "segment", ["kernel_status"], ["valid_from", "valid_to", "established", "alternatives", "support"])
        raw_sup = o.get("support", {})
        if not isinstance(raw_sup, dict):
            raise ValidationError("segment.support: expected an object")
        return cls(
            valid_from=opt(o.get("valid_from"), lambda x: ts_from_str(x, "segment.valid_from")),
            valid_to=opt(o.get("valid_to"), lambda x: ts_from_str(x, "segment.valid_to")),
            kernel_status=as_enum(KernelStatus, o["kernel_status"], "segment.kernel_status"),
            established=opt(o.get("established"), Candidate.from_dict),
            alternatives=tuple_of(o.get("alternatives", []), "segment.alternatives", lambda x, c: Candidate.from_dict(x)),
            support={
                as_str(k, "segment.support key"): tuple_of(v, f"segment.support[{k}]", lambda x, c: Support.from_dict(x))
                for k, v in raw_sup.items()
            },
        )


# ---------------------------------------------------------------- belief

@dataclass(frozen=True, kw_only=True)
class Pin(Codec):
    """A report version a belief consumed, with the admission version that admitted it."""

    report_id: str
    admission_version: int

    def __post_init__(self) -> None:
        check_ulid(self.report_id, "pin.report_id")
        check_nat(self.admission_version, "pin.admission_version", minimum=1)

    def to_dict(self) -> dict[str, Any]:
        return {"report_id": self.report_id, "admission_version": self.admission_version}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "pin", ["report_id", "admission_version"])
        return cls(report_id=as_str(o["report_id"], "pin.report_id"), admission_version=as_int(o["admission_version"], "pin.admission_version"))


@dataclass(frozen=True, kw_only=True)
class Dependency(Codec):
    key: Key
    version: int

    def __post_init__(self) -> None:
        check_nat(self.version, "depends_on.version", minimum=1)

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key.to_dict(), "version": self.version}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "dependency", ["key", "version"])
        return cls(key=Key.from_dict(o["key"]), version=as_int(o["version"], "depends_on.version"))


@dataclass(frozen=True, kw_only=True)
class InvalidatedBy(Codec):
    """What invalidated a belief version: a report, an admission decision, or an entity merge."""

    kind: InvalidatedKind
    id: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, InvalidatedKind):
            raise ValidationError("invalidated_by.kind: not an InvalidatedKind")
        check_nonempty(self.id, "invalidated_by.id")
        if self.kind in (InvalidatedKind.REPORT, InvalidatedKind.ADMISSION):
            check_ulid(self.id, "invalidated_by.id")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "id": self.id}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "invalidated_by", ["kind", "id"])
        return cls(kind=as_enum(InvalidatedKind, o["kind"], "invalidated_by.kind"), id=as_str(o["id"], "invalidated_by.id"))


@dataclass(frozen=True, kw_only=True)
class Versions(Codec):
    """The schema, semantic and admission versions a belief was computed under."""

    schema: int
    semantic: int
    admission: int

    def __post_init__(self) -> None:
        for n in ("schema", "semantic", "admission"):
            check_nat(getattr(self, n), f"versions.{n}", minimum=1)

    def to_dict(self) -> dict[str, Any]:
        return {"schema": self.schema, "semantic": self.semantic, "admission": self.admission}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "versions", ["schema", "semantic", "admission"])
        return cls(**{n: as_int(o[n], f"versions.{n}") for n in ("schema", "semantic", "admission")})


@dataclass(frozen=True, kw_only=True)
class SemanticConfig(Codec):
    """A semantic configuration (design v0.3 §Semantic configuration): the paper's P0c and
    P0cSU are ``self_update`` off and on. A numbered version of one of these is what
    :class:`Versions` refers to."""

    semantics: str = "v0.3"
    self_update: bool
    profile: Profile = Profile.OPEN_WORLD

    def __post_init__(self) -> None:
        check_nonempty(self.semantics, "semantic.semantics")
        if not isinstance(self.self_update, bool):
            raise ValidationError("semantic.self_update: must be a boolean")
        if not isinstance(self.profile, Profile):
            raise ValidationError("semantic.profile: not a Profile")

    def to_dict(self) -> dict[str, Any]:
        return {"semantics": self.semantics, "self_update": self.self_update, "profile": self.profile.value}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "semantic config", ["self_update"], ["semantics", "profile"])
        return cls(
            semantics=as_str(o.get("semantics", "v0.3"), "semantic.semantics"),
            self_update=as_bool(o["self_update"], "semantic.self_update"),
            profile=as_enum(Profile, o.get("profile", "open-world"), "semantic.profile"),
        )


@dataclass(frozen=True, kw_only=True)
class Inference(Codec):
    """``complete`` or ``incomplete(reason)``; complete iff completed_generation = required_generation."""

    complete: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.complete, bool):
            raise ValidationError("inference.complete: must be a boolean")
        if self.complete and self.reason is not None:
            raise ValidationError("inference: a complete inference has no reason")
        if not self.complete:
            check_nonempty(self.reason, "inference.reason")

    def to_dict(self) -> dict[str, Any]:
        return {"complete": self.complete, "reason": self.reason}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "inference", ["complete"], ["reason"])
        return cls(complete=as_bool(o["complete"], "inference.complete"), reason=opt(o.get("reason"), lambda x: as_str(x, "inference.reason")))


def _check_generations(required: int, completed: int, inference: Inference, ctx: str) -> None:
    check_nat(required, f"{ctx}.required_generation")
    check_nat(completed, f"{ctx}.completed_generation")
    if completed > required:
        raise ValidationError(f"{ctx}: completed_generation ({completed}) exceeds required_generation ({required})")
    if inference.complete != (completed == required):
        raise ValidationError(f"{ctx}: inference is complete iff completed_generation = required_generation")


def _check_segment_order(segments: tuple[Segment, ...]) -> None:
    for a, b in pairwise(segments):
        if a.valid_to is None or b.valid_from is None or a.valid_to > b.valid_from:
            raise ValidationError("belief.segments must be ordered and non-overlapping in valid time")


@dataclass(frozen=True, kw_only=True)
class Belief(Codec):
    """One belief version for one key."""

    key: Key
    version: int
    lsn: int  # S-05: the log position that produced this version
    required_generation: int
    completed_generation: int
    segments: tuple[Segment, ...]
    pinned: tuple[Pin, ...]
    depends_on: tuple[Dependency, ...]
    invalidated_by: InvalidatedBy | None
    versions: Versions
    inference: Inference
    recorded_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.key, Key):
            raise ValidationError("belief.key: not a Key")
        check_nat(self.version, "belief.version", minimum=1)
        check_nat(self.lsn, "belief.lsn", minimum=1)
        _check_generations(self.required_generation, self.completed_generation, self.inference, "belief")
        set_field(self, "recorded_at", req_ts(self.recorded_at, "belief.recorded_at"))
        for s in self.segments:
            if not isinstance(s, Segment):
                raise ValidationError("belief.segments: not Segment objects")
            if s.key is not None and s.key != self.key:
                raise ValidationError("belief.segments: a segment names candidates of a different key")
        _check_segment_order(self.segments)
        pins = [(p.report_id, p.admission_version) for p in self.pinned]
        if len(set(pins)) != len(pins):
            raise ValidationError("belief.pinned: duplicate pins")
        if any(dep.key == self.key for dep in self.depends_on):
            raise ValidationError("belief.depends_on: a belief cannot depend on itself")

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key.to_dict(),
            "version": self.version,
            "lsn": self.lsn,
            "required_generation": self.required_generation,
            "completed_generation": self.completed_generation,
            "segments": [s.to_dict() for s in self.segments],
            "pinned": [p.to_dict() for p in self.pinned],
            "depends_on": [d.to_dict() for d in self.depends_on],
            "invalidated_by": None if self.invalidated_by is None else self.invalidated_by.to_dict(),
            "versions": self.versions.to_dict(),
            "inference": self.inference.to_dict(),
            "recorded_at": ts_to_str(self.recorded_at),
        }

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(
            d, "belief",
            ["key", "version", "lsn", "required_generation", "completed_generation", "segments", "versions", "inference", "recorded_at"],
            ["pinned", "depends_on", "invalidated_by"],
        )
        return cls(
            key=Key.from_dict(o["key"]),
            version=as_int(o["version"], "belief.version"),
            lsn=as_int(o["lsn"], "belief.lsn"),
            required_generation=as_int(o["required_generation"], "belief.required_generation"),
            completed_generation=as_int(o["completed_generation"], "belief.completed_generation"),
            segments=tuple_of(o["segments"], "belief.segments", lambda x, c: Segment.from_dict(x)),
            pinned=tuple_of(o.get("pinned", []), "belief.pinned", lambda x, c: Pin.from_dict(x)),
            depends_on=tuple_of(o.get("depends_on", []), "belief.depends_on", lambda x, c: Dependency.from_dict(x)),
            invalidated_by=opt(o.get("invalidated_by"), InvalidatedBy.from_dict),
            versions=Versions.from_dict(o["versions"]),
            inference=Inference.from_dict(o["inference"]),
            recorded_at=ts_from_str(o["recorded_at"], "belief.recorded_at"),
        )


@dataclass(frozen=True, kw_only=True)
class BeliefView(Codec):
    """What an Answer embeds: bounded, never the whole record. ``ref`` is a handle for
    ``get_belief(key, version)``."""

    key: Key
    version: int
    required_generation: int
    completed_generation: int
    inference: Inference
    segment: Segment
    ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.key, Key):
            raise ValidationError("belief_view.key: not a Key")
        check_nat(self.version, "belief_view.version", minimum=1)
        _check_generations(self.required_generation, self.completed_generation, self.inference, "belief_view")
        if not isinstance(self.segment, Segment):
            raise ValidationError("belief_view.segment: not a Segment")
        if self.segment.key is not None and self.segment.key != self.key:
            raise ValidationError("belief_view: segment candidates belong to a different key")
        check_nonempty(self.ref, "belief_view.ref")

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key.to_dict(),
            "version": self.version,
            "required_generation": self.required_generation,
            "completed_generation": self.completed_generation,
            "inference": self.inference.to_dict(),
            "segment": self.segment.to_dict(),
            "ref": self.ref,
        }

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "belief_view", ["key", "version", "required_generation", "completed_generation", "inference", "segment", "ref"])
        return cls(
            key=Key.from_dict(o["key"]),
            version=as_int(o["version"], "belief_view.version"),
            required_generation=as_int(o["required_generation"], "belief_view.required_generation"),
            completed_generation=as_int(o["completed_generation"], "belief_view.completed_generation"),
            inference=Inference.from_dict(o["inference"]),
            segment=Segment.from_dict(o["segment"]),
            ref=as_str(o["ref"], "belief_view.ref"),
        )
