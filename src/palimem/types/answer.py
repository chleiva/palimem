"""Query and Answer: output contract v2 (design v0.3 §Query and answer).

``Answer = Resolved | ResourceLimited``. A ``Resolved`` answer carries ``kernel_status`` (what the
admitted evidence warrants) and ``decision`` (what the policy does with it) as separate top-level
fields. ``ResourceLimited`` carries neither a segment nor a kernel status, because neither could
truthfully describe a snapshot whose inference has not completed.

``belief_as_of`` accepts an LSN (``int``) or a timestamp (S-05); the log maps a timestamp to the
last LSN at or before it. In JSON an integer is an LSN and a string is a timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Self

from palimem.types._codec import (
    Codec,
    ValidationError,
    as_bool,
    as_enum,
    as_float,
    as_int,
    as_obj,
    as_str,
    check_nat,
    check_nonempty,
    norm_ts,
    opt,
    opt_ts_str,
    parse_json,
    set_field,
    ts_from_str,
    ts_to_str,
    tuple_of,
)
from palimem.types.belief import BeliefView, Support
from palimem.types.enums import (
    Decision,
    ExplainMode,
    ExplanationState,
    KernelStatus,
    NotReconstructableReason,
    Profile,
    ResourceLimitedReason,
    RuleFired,
)
from palimem.types.values import Candidate, Key

BeliefAsOf = int | datetime


def check_belief_as_of(v: Any, ctx: str) -> BeliefAsOf | None:
    if v is None:
        return None
    if isinstance(v, bool):
        raise ValidationError(f"{ctx}: expected an LSN (integer) or a timestamp, got bool")
    if isinstance(v, int):
        return check_nat(v, ctx)
    out = norm_ts(v, ctx)
    return out


def belief_as_of_to_json(v: BeliefAsOf | None) -> int | str | None:
    if v is None:
        return None
    return v if isinstance(v, int) else ts_to_str(v)


def belief_as_of_from_json(x: Any, ctx: str = "belief_as_of") -> BeliefAsOf | None:
    if x is None:
        return None
    if isinstance(x, bool):
        raise ValidationError(f"{ctx}: expected an LSN (integer) or a timestamp string, got bool")
    if isinstance(x, int):
        return x
    return ts_from_str(x, ctx)


@dataclass(frozen=True, kw_only=True)
class Query(Codec):
    key: Key
    valid_at: datetime | None = None  # defaults to now
    belief_as_of: BeliefAsOf | None = None  # LSN or timestamp; defaults to now
    profile: Profile = Profile.OPEN_WORLD
    explanation_budget: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, Key):
            raise ValidationError("query.key: not a Key")
        set_field(self, "valid_at", norm_ts(self.valid_at, "query.valid_at"))
        set_field(self, "belief_as_of", check_belief_as_of(self.belief_as_of, "query.belief_as_of"))
        if not isinstance(self.profile, Profile):
            raise ValidationError("query.profile: not a Profile")
        if self.explanation_budget is not None:
            check_nat(self.explanation_budget, "query.explanation_budget")

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key.to_dict(),
            "valid_at": opt_ts_str(self.valid_at),
            "belief_as_of": belief_as_of_to_json(self.belief_as_of),
            "profile": self.profile.value,
            "explanation_budget": self.explanation_budget,
        }

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "query", ["key"], ["valid_at", "belief_as_of", "profile", "explanation_budget"])
        return cls(
            key=Key.from_dict(o["key"]),
            valid_at=opt(o.get("valid_at"), lambda x: ts_from_str(x, "query.valid_at")),
            belief_as_of=belief_as_of_from_json(o.get("belief_as_of"), "query.belief_as_of"),
            profile=as_enum(Profile, o.get("profile", "open-world"), "query.profile"),
            explanation_budget=opt(o.get("explanation_budget"), lambda x: as_int(x, "query.explanation_budget")),
        )


@dataclass(frozen=True, kw_only=True)
class SegmentBounds(Codec):
    """The slice of valid time an answer describes."""

    valid_from: datetime | None = None
    valid_to: datetime | None = None

    def __post_init__(self) -> None:
        set_field(self, "valid_from", norm_ts(self.valid_from, "segment.valid_from"))
        set_field(self, "valid_to", norm_ts(self.valid_to, "segment.valid_to"))
        if self.valid_from is not None and self.valid_to is not None and not self.valid_from < self.valid_to:
            raise ValidationError("answer.segment: valid_from must be strictly before valid_to")

    def to_dict(self) -> dict[str, Any]:
        return {"valid_from": opt_ts_str(self.valid_from), "valid_to": opt_ts_str(self.valid_to)}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "answer.segment", [], ["valid_from", "valid_to"])
        return cls(
            valid_from=opt(o.get("valid_from"), lambda x: ts_from_str(x, "answer.segment.valid_from")),
            valid_to=opt(o.get("valid_to"), lambda x: ts_from_str(x, "answer.segment.valid_to")),
        )


@dataclass(frozen=True, kw_only=True)
class PolicyInfo(Codec):
    version: int
    rule_fired: RuleFired = RuleFired.NONE

    def __post_init__(self) -> None:
        check_nat(self.version, "policy.version", minimum=1)
        if not isinstance(self.rule_fired, RuleFired):
            raise ValidationError("policy.rule_fired: not a RuleFired")

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "rule_fired": self.rule_fired.value}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "policy", ["version"], ["rule_fired"])
        return cls(version=as_int(o["version"], "policy.version"), rule_fired=as_enum(RuleFired, o.get("rule_fired", "none"), "policy.rule_fired"))


@dataclass(frozen=True, kw_only=True)
class Inquiry(Codec):
    """What an agent can do about an ``ask``: the competing candidates, the keys whose evidence
    would decide between them, and the source classes that could supply it."""

    competing: tuple[Candidate, ...]
    missing: tuple[Key, ...] = ()
    resolvers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Ruling 16 (2026-10-05): an inquiry also accompanies an `abstain`, and a key with no evidence at all has no
        # competing candidate. An inquiry must still say what could be done: a candidate to settle or a key to supply.
        if len(self.competing) < 1 and len(self.missing) < 1:
            raise ValidationError("inquiry must list at least one competing candidate or one missing key")
        for r in self.resolvers:
            check_nonempty(r, "inquiry.resolvers[]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "competing": [c.to_dict() for c in self.competing],
            "missing": [k.to_dict() for k in self.missing],
            "resolvers": list(self.resolvers),
        }

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "inquiry", ["competing"], ["missing", "resolvers"])
        return cls(
            competing=tuple_of(o["competing"], "inquiry.competing", lambda x, c: Candidate.from_dict(x)),
            missing=tuple_of(o.get("missing", []), "inquiry.missing", lambda x, c: Key.from_dict(x)),
            resolvers=tuple_of(o.get("resolvers", []), "inquiry.resolvers", as_str),
        )


@dataclass(frozen=True, kw_only=True)
class Resolved(Codec):
    """An answer for one segment of one belief version. ``provenance`` is all subset-minimal
    environments over **base** reports applicable in the answered segment (S-12), cut at the
    query's ``explanation_budget``."""

    segment: SegmentBounds
    kernel_status: KernelStatus
    decision: Decision
    justified: BeliefView
    assertion: Candidate | None = None  # exactly when decision = commit
    alternatives: tuple[Candidate, ...] = ()
    provenance: tuple[Support, ...] = ()
    explanation: ExplanationState = ExplanationState.COMPLETE
    policy: PolicyInfo
    confidence: float | None = None
    inquiry: Inquiry | None = None  # required when decision = ask; present on abstain (ruling 16); never on commit

    def __post_init__(self) -> None:
        if not isinstance(self.decision, Decision):
            raise ValidationError("answer.decision: not a Decision")
        if not isinstance(self.kernel_status, KernelStatus):
            raise ValidationError("answer.kernel_status: not a KernelStatus")
        if (self.decision is Decision.COMMIT) != (self.assertion is not None):
            raise ValidationError("answer: an assertion is present exactly when decision = commit")
        if self.decision is Decision.ASK and self.inquiry is None:
            raise ValidationError("answer: an inquiry is required when decision = ask")
        if self.decision is Decision.COMMIT and self.inquiry is not None:
            raise ValidationError("answer: an inquiry is never present when decision = commit")
        if self.confidence is not None:
            conf = as_float(self.confidence, "answer.confidence")
            if not 0.0 <= conf <= 1.0:
                raise ValidationError("answer.confidence must be within [0, 1]")
        seg = self.justified.segment
        if seg.kernel_status is not self.kernel_status:
            raise ValidationError("answer: kernel_status must equal the justified segment's status")
        if (seg.valid_from, seg.valid_to) != (self.segment.valid_from, self.segment.valid_to):
            raise ValidationError("answer: segment bounds must equal the justified segment's bounds")
        seg_ids = {c.id for c in (([seg.established] if seg.established is not None else []) + list(seg.alternatives))}
        if self.assertion is not None and self.assertion.id not in seg_ids:
            raise ValidationError("answer: the committed assertion must be a candidate of the justified segment")
        for c in self.alternatives:
            if c.key != self.justified.key:
                raise ValidationError("answer.alternatives: candidate belongs to a different key")
        if not isinstance(self.explanation, ExplanationState):
            raise ValidationError("answer.explanation: not an ExplanationState")

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment": self.segment.to_dict(),
            "kernel_status": self.kernel_status.value,
            "decision": self.decision.value,
            "assertion": None if self.assertion is None else self.assertion.to_dict(),
            "alternatives": [c.to_dict() for c in self.alternatives],
            "provenance": [s.to_dict() for s in self.provenance],
            "explanation": self.explanation.value,
            "policy": self.policy.to_dict(),
            "confidence": self.confidence,
            "inquiry": None if self.inquiry is None else self.inquiry.to_dict(),
            "justified": self.justified.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(
            d, "answer",
            ["segment", "kernel_status", "decision", "policy", "justified"],
            ["assertion", "alternatives", "provenance", "explanation", "confidence", "inquiry"],
        )
        return cls(
            segment=SegmentBounds.from_dict(o["segment"]),
            kernel_status=as_enum(KernelStatus, o["kernel_status"], "answer.kernel_status"),
            decision=as_enum(Decision, o["decision"], "answer.decision"),
            assertion=opt(o.get("assertion"), Candidate.from_dict),
            alternatives=tuple_of(o.get("alternatives", []), "answer.alternatives", lambda x, c: Candidate.from_dict(x)),
            provenance=tuple_of(o.get("provenance", []), "answer.provenance", lambda x, c: Support.from_dict(x)),
            explanation=as_enum(ExplanationState, o.get("explanation", "complete"), "answer.explanation"),
            policy=PolicyInfo.from_dict(o["policy"]),
            confidence=opt(o.get("confidence"), lambda x: as_float(x, "answer.confidence")),
            inquiry=opt(o.get("inquiry"), Inquiry.from_dict),
            justified=BeliefView.from_dict(o["justified"]),
        )


@dataclass(frozen=True, kw_only=True)
class LastComplete(Codec):
    """An older complete snapshot, explicitly labelled with the ``belief_as_of`` it belongs to;
    never presented as current."""

    belief_as_of: BeliefAsOf
    view: BeliefView

    def __post_init__(self) -> None:
        if check_belief_as_of(self.belief_as_of, "last_complete.belief_as_of") is None:
            raise ValidationError("last_complete.belief_as_of is required")
        if not isinstance(self.view, BeliefView):
            raise ValidationError("last_complete.view: not a BeliefView")

    def to_dict(self) -> dict[str, Any]:
        return {"belief_as_of": belief_as_of_to_json(self.belief_as_of), "view": self.view.to_dict()}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "last_complete", ["belief_as_of", "view"])
        v = belief_as_of_from_json(o["belief_as_of"], "last_complete.belief_as_of")
        if v is None:
            raise ValidationError("last_complete.belief_as_of is required")
        return cls(belief_as_of=v, view=BeliefView.from_dict(o["view"]))


@dataclass(frozen=True, kw_only=True)
class ResourceLimited(Codec):
    """No result is valid for the requested snapshot. Carries no segment and no kernel_status.

    ``environment_budget`` (S-06, decided): the key exceeded its environment budget (default
    :data:`~palimem.types.limits.DEFAULT_ENVIRONMENT_BUDGET` = 12); a key above budget never
    degrades silently to a different kernel's answer."""

    reason: ResourceLimitedReason
    required_generation: int
    completed_generation: int
    reason_key: Key | None = None  # the stale dependency / the key over budget; see __post_init__
    last_complete: LastComplete | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.reason, ResourceLimitedReason):
            raise ValidationError("answer.reason: not a ResourceLimitedReason")
        check_nat(self.required_generation, "answer.required_generation")
        check_nat(self.completed_generation, "answer.completed_generation")
        if self.completed_generation > self.required_generation:
            raise ValidationError("answer: completed_generation exceeds required_generation")
        generation_bound = (ResourceLimitedReason.INFERENCE_INCOMPLETE, ResourceLimitedReason.STALE_DEPENDENCY)
        if self.reason in generation_bound and self.completed_generation == self.required_generation:
            raise ValidationError(f"answer: reason '{self.reason.value}' means completed_generation < required_generation")
        keyed = (ResourceLimitedReason.STALE_DEPENDENCY, ResourceLimitedReason.ENVIRONMENT_BUDGET)
        if (self.reason in keyed) != (self.reason_key is not None):
            raise ValidationError(
                "answer: reason_key is present exactly when reason is stale_dependency (the stale key) "
                "or environment_budget (the key over budget)"
            )

    @property
    def decision(self) -> str:
        return "resource_limited"

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": "resource_limited",
            "reason": self.reason.value,
            "reason_key": None if self.reason_key is None else self.reason_key.to_dict(),
            "required_generation": self.required_generation,
            "completed_generation": self.completed_generation,
            "last_complete": None if self.last_complete is None else self.last_complete.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(
            d, "resource_limited answer",
            ["decision", "reason", "required_generation", "completed_generation"],
            ["reason_key", "last_complete"],
        )
        if o["decision"] != "resource_limited":
            raise ValidationError("resource_limited answer: decision must be 'resource_limited'")
        return cls(
            reason=as_enum(ResourceLimitedReason, o["reason"], "answer.reason"),
            reason_key=opt(o.get("reason_key"), Key.from_dict),
            required_generation=as_int(o["required_generation"], "answer.required_generation"),
            completed_generation=as_int(o["completed_generation"], "answer.completed_generation"),
            last_complete=opt(o.get("last_complete"), LastComplete.from_dict),
        )


@dataclass(frozen=True, kw_only=True)
class NotReconstructable(Codec):
    """The belief in force at the requested snapshot was redacted by an erasure, so the historical answer can no
    longer be reconstructed (S-13; author ruling 2026-10-05, an additive third ``Answer`` variant).

    Like :class:`ResourceLimited` it carries no segment and no kernel_status: none could truthfully describe the
    snapshot. It says *what* was redacted without leaking any of it: the key the caller asked about, the snapshot they
    asked for, and the number and log position of the redacted version, never its content. ``current_available`` says
    whether a repaired current belief of the key can be read instead."""

    reason: NotReconstructableReason
    key: Key
    belief_as_of: BeliefAsOf  # the snapshot that was requested
    version: int  # the redacted belief version in force at that snapshot
    lsn: int  # the log position that version was produced at
    current_available: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.reason, NotReconstructableReason):
            raise ValidationError("answer.reason: not a NotReconstructableReason")
        if not isinstance(self.key, Key):
            raise ValidationError("answer.key: not a Key")
        if check_belief_as_of(self.belief_as_of, "answer.belief_as_of") is None:
            raise ValidationError("answer.belief_as_of is required")
        set_field(self, "belief_as_of", check_belief_as_of(self.belief_as_of, "answer.belief_as_of"))
        check_nat(self.version, "answer.version", minimum=1)
        check_nat(self.lsn, "answer.lsn", minimum=1)
        if not isinstance(self.current_available, bool):
            raise ValidationError("answer.current_available: expected a boolean")

    @property
    def decision(self) -> str:
        return "not_reconstructable"

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": "not_reconstructable",
            "reason": self.reason.value,
            "key": self.key.to_dict(),
            "belief_as_of": belief_as_of_to_json(self.belief_as_of),
            "version": self.version,
            "lsn": self.lsn,
            "current_available": self.current_available,
        }

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(
            d, "not_reconstructable answer",
            ["decision", "reason", "key", "belief_as_of", "version", "lsn"], ["current_available"],
        )
        if o["decision"] != "not_reconstructable":
            raise ValidationError("not_reconstructable answer: decision must be 'not_reconstructable'")
        v = belief_as_of_from_json(o["belief_as_of"], "answer.belief_as_of")
        if v is None:
            raise ValidationError("answer.belief_as_of is required")
        return cls(
            reason=as_enum(NotReconstructableReason, o["reason"], "answer.reason"),
            key=Key.from_dict(o["key"]),
            belief_as_of=v,
            version=as_int(o["version"], "answer.version"),
            lsn=as_int(o["lsn"], "answer.lsn"),
            current_available=as_bool(o.get("current_available", True), "answer.current_available"),
        )


Answer = Resolved | ResourceLimited | NotReconstructable


@dataclass(frozen=True, kw_only=True)
class ExplainQuery(Codec):
    """``explain(key, valid_at, mode, depth)`` (S-12, decided). ``depth=None`` is the full
    derivation closure; an integer limits derivation levels (1 = the key's own reports)."""

    key: Key
    valid_at: datetime | None = None
    belief_as_of: BeliefAsOf | None = None
    mode: ExplainMode = ExplainMode.ALL
    depth: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, Key):
            raise ValidationError("explain.key: not a Key")
        set_field(self, "valid_at", norm_ts(self.valid_at, "explain.valid_at"))
        set_field(self, "belief_as_of", check_belief_as_of(self.belief_as_of, "explain.belief_as_of"))
        if not isinstance(self.mode, ExplainMode):
            raise ValidationError("explain.mode: not an ExplainMode")
        if self.depth is not None:
            check_nat(self.depth, "explain.depth", minimum=1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key.to_dict(),
            "valid_at": opt_ts_str(self.valid_at),
            "belief_as_of": belief_as_of_to_json(self.belief_as_of),
            "mode": self.mode.value,
            "depth": self.depth,
        }

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "explain query", ["key"], ["valid_at", "belief_as_of", "mode", "depth"])
        return cls(
            key=Key.from_dict(o["key"]),
            valid_at=opt(o.get("valid_at"), lambda x: ts_from_str(x, "explain.valid_at")),
            belief_as_of=belief_as_of_from_json(o.get("belief_as_of"), "explain.belief_as_of"),
            mode=as_enum(ExplainMode, o.get("mode", "all"), "explain.mode"),
            depth=opt(o.get("depth"), lambda x: as_int(x, "explain.depth")),
        )


@dataclass(frozen=True, kw_only=True)
class Explanation(Codec):
    """Result of ``explain``: subset-minimal environments over **base** reports for one segment
    (``mode=one`` returns a single canonical environment)."""

    key: Key
    segment: SegmentBounds
    mode: ExplainMode
    depth: int | None
    state: ExplanationState = ExplanationState.COMPLETE
    environments: tuple[Support, ...] = ()

    def __post_init__(self) -> None:
        if self.mode is ExplainMode.ONE and len(self.environments) > 1:
            raise ValidationError("explanation: mode 'one' returns at most one environment")
        if self.depth is not None:
            check_nat(self.depth, "explanation.depth", minimum=1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key.to_dict(),
            "segment": self.segment.to_dict(),
            "mode": self.mode.value,
            "depth": self.depth,
            "state": self.state.value,
            "environments": [s.to_dict() for s in self.environments],
        }

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "explanation", ["key", "segment", "mode"], ["depth", "state", "environments"])
        return cls(
            key=Key.from_dict(o["key"]),
            segment=SegmentBounds.from_dict(o["segment"]),
            mode=as_enum(ExplainMode, o["mode"], "explanation.mode"),
            depth=opt(o.get("depth"), lambda x: as_int(x, "explanation.depth")),
            state=as_enum(ExplanationState, o.get("state", "complete"), "explanation.state"),
            environments=tuple_of(o.get("environments", []), "explanation.environments", lambda x, c: Support.from_dict(x)),
        )


def answer_from_dict(d: Any) -> Answer:
    if not isinstance(d, dict) or "decision" not in d:
        raise ValidationError("answer: expected an object with a 'decision'")
    if d["decision"] == "resource_limited":
        return ResourceLimited.from_dict(d)
    if d["decision"] == "not_reconstructable":
        return NotReconstructable.from_dict(d)
    return Resolved.from_dict(d)


def answer_from_json(text: str) -> Answer:
    return answer_from_dict(parse_json(text))
