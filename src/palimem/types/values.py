"""Key, Proposition and Candidate (design v0.3 §Data and API: Report.proposition, Candidate).

``Proposition`` is what a report *claims*; ``CandidateForm`` is what a belief *holds* as a
candidate answer. Negative claims keep their content (``not Acme`` and ``not Globex`` are
distinct candidates) and attributed claims keep holder and proposition.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, ClassVar, Self

from palimem.types._codec import (
    Codec,
    ValidationError,
    Value,
    as_obj,
    as_str,
    canonical_json,
    check_nonempty,
    check_value,
    set_field,
    tuple_of,
)
from palimem.types.limits import MAX_BELIEF_NESTING


def canonical_values(values: tuple[Value, ...], ctx: str) -> tuple[Value, ...]:
    """Set semantics: deduplicate and order by canonical JSON, so equal sets compare equal."""
    seen: dict[str, Value] = {}
    for i, v in enumerate(values):
        check_value(v, f"{ctx}[{i}]")
        seen[canonical_json(v)] = v
    return tuple(seen[k] for k in sorted(seen))


@dataclass(frozen=True, kw_only=True)
class Key(Codec):
    entity: str
    attr: str

    def __post_init__(self) -> None:
        check_nonempty(self.entity, "key.entity")
        check_nonempty(self.attr, "key.attr")

    def to_dict(self) -> dict[str, Any]:
        return {"entity": self.entity, "attr": self.attr}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "key", ["entity", "attr"])
        return cls(entity=as_str(o["entity"], "key.entity"), attr=as_str(o["attr"], "key.attr"))


# ---------------------------------------------------------------- propositions

@dataclass(frozen=True, kw_only=True)
class ValueProp(Codec):
    """single-valued key: attr = v."""

    form: ClassVar[str] = "value"
    value: Value

    def __post_init__(self) -> None:
        check_value(self.value, "proposition.value")

    def to_dict(self) -> dict[str, Any]:
        return {"form": "value", "v": self.value}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "proposition(value)", ["form", "v"])
        return cls(value=check_value(o["v"], "proposition.v"))


@dataclass(frozen=True, kw_only=True)
class MemberProp(Codec):
    """set-valued key: v is a member."""

    form: ClassVar[str] = "member"
    value: Value

    def __post_init__(self) -> None:
        check_value(self.value, "proposition.value")

    def to_dict(self) -> dict[str, Any]:
        return {"form": "member", "v": self.value}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "proposition(member)", ["form", "v"])
        return cls(value=check_value(o["v"], "proposition.v"))


@dataclass(frozen=True, kw_only=True)
class NotMemberProp(Codec):
    """set-valued key: v is not a member."""

    form: ClassVar[str] = "not_member"
    value: Value

    def __post_init__(self) -> None:
        check_value(self.value, "proposition.value")

    def to_dict(self) -> dict[str, Any]:
        return {"form": "not_member", "v": self.value}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "proposition(not_member)", ["form", "v"])
        return cls(value=check_value(o["v"], "proposition.v"))


@dataclass(frozen=True, kw_only=True)
class EnumerationProp(Codec):
    """set-valued key: these are *all* the members ([] = explicitly empty)."""

    form: ClassVar[str] = "enumeration"
    values: tuple[Value, ...]

    def __post_init__(self) -> None:
        set_field(self, "values", canonical_values(tuple(self.values), "proposition.values"))

    def to_dict(self) -> dict[str, Any]:
        return {"form": "enumeration", "values": list(self.values)}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "proposition(enumeration)", ["form", "values"])
        return cls(values=tuple_of(o["values"], "proposition.values", check_value))


@dataclass(frozen=True, kw_only=True)
class NotValueProp(Codec):
    """single-valued key: attr is not v."""

    form: ClassVar[str] = "not_value"
    value: Value

    def __post_init__(self) -> None:
        check_value(self.value, "proposition.value")

    def to_dict(self) -> dict[str, Any]:
        return {"form": "not_value", "v": self.value}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "proposition(not_value)", ["form", "v"])
        return cls(value=check_value(o["v"], "proposition.v"))


@dataclass(frozen=True, kw_only=True)
class BeliefOfProp(Codec):
    """An attributed claim: establishes only the attribution, never the inner proposition."""

    form: ClassVar[str] = "belief_of"
    holder: str
    proposition: Proposition

    def __post_init__(self) -> None:
        check_nonempty(self.holder, "proposition.holder")
        if not isinstance(self.proposition, PROPOSITION_TYPES):
            raise ValidationError("proposition.proposition: not a Proposition")
        if belief_depth(self) > MAX_BELIEF_NESTING:
            raise ValidationError(
                f"belief_of nesting exceeds {MAX_BELIEF_NESTING}: deeper nesting is reserved (S-11)"
            )

    def to_dict(self) -> dict[str, Any]:
        return {"form": "belief_of", "holder": self.holder, "proposition": self.proposition.to_dict()}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "proposition(belief_of)", ["form", "holder", "proposition"])
        return cls(holder=as_str(o["holder"], "proposition.holder"), proposition=proposition_from_dict(o["proposition"]))


Proposition = ValueProp | MemberProp | NotMemberProp | EnumerationProp | NotValueProp | BeliefOfProp
PROPOSITION_TYPES = (ValueProp, MemberProp, NotMemberProp, EnumerationProp, NotValueProp, BeliefOfProp)

_PROP_BY_FORM: dict[str, type[Any]] = {t.form: t for t in PROPOSITION_TYPES}


def belief_depth(p: Proposition) -> int:
    """Number of nested ``belief_of`` wrappers (0 for a plain proposition)."""
    return 1 + belief_depth(p.proposition) if isinstance(p, BeliefOfProp) else 0


def proposition_from_dict(d: Any) -> Proposition:
    if not isinstance(d, dict) or "form" not in d:
        raise ValidationError("proposition: expected an object with a 'form'")
    cls = _PROP_BY_FORM.get(d["form"]) if isinstance(d["form"], str) else None
    if cls is None:
        raise ValidationError(f"proposition: unknown form {d['form']!r}; expected one of {sorted(_PROP_BY_FORM)}")
    out: Proposition = cls.from_dict(d)
    return out


def proposition_from_json(text: str) -> Proposition:
    from palimem.types._codec import parse_json

    return proposition_from_dict(parse_json(text))


# ---------------------------------------------------------------- candidate forms

@dataclass(frozen=True, kw_only=True)
class ValueForm(Codec):
    form: ClassVar[str] = "value"
    value: Value

    def __post_init__(self) -> None:
        check_value(self.value, "candidate.value")

    def to_dict(self) -> dict[str, Any]:
        return {"form": "value", "v": self.value}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "candidate form(value)", ["form", "v"])
        return cls(value=check_value(o["v"], "candidate.v"))


@dataclass(frozen=True, kw_only=True)
class SetForm(Codec):
    """A determined member set (non-empty; the empty set is ``EmptyForm``)."""

    form: ClassVar[str] = "set"
    values: tuple[Value, ...]

    def __post_init__(self) -> None:
        vals = canonical_values(tuple(self.values), "candidate.values")
        if not vals:
            raise ValidationError("candidate form 'set' must be non-empty; use 'empty' for the empty set")
        set_field(self, "values", vals)

    def to_dict(self) -> dict[str, Any]:
        return {"form": "set", "values": list(self.values)}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "candidate form(set)", ["form", "values"])
        return cls(values=tuple_of(o["values"], "candidate.values", check_value))


@dataclass(frozen=True, kw_only=True)
class EmptyForm(Codec):
    form: ClassVar[str] = "empty"

    def to_dict(self) -> dict[str, Any]:
        return {"form": "empty"}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        as_obj(d, "candidate form(empty)", ["form"])
        return cls()


@dataclass(frozen=True, kw_only=True)
class NotValueForm(Codec):
    form: ClassVar[str] = "not_value"
    value: Value

    def __post_init__(self) -> None:
        check_value(self.value, "candidate.value")

    def to_dict(self) -> dict[str, Any]:
        return {"form": "not_value", "v": self.value}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "candidate form(not_value)", ["form", "v"])
        return cls(value=check_value(o["v"], "candidate.v"))


@dataclass(frozen=True, kw_only=True)
class NotMemberForm(Codec):
    form: ClassVar[str] = "not_member"
    value: Value

    def __post_init__(self) -> None:
        check_value(self.value, "candidate.value")

    def to_dict(self) -> dict[str, Any]:
        return {"form": "not_member", "v": self.value}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "candidate form(not_member)", ["form", "v"])
        return cls(value=check_value(o["v"], "candidate.v"))


@dataclass(frozen=True, kw_only=True)
class BeliefOfForm(Codec):
    form: ClassVar[str] = "belief_of"
    holder: str
    proposition: Proposition

    def __post_init__(self) -> None:
        check_nonempty(self.holder, "candidate.holder")
        if not isinstance(self.proposition, PROPOSITION_TYPES):
            raise ValidationError("candidate.proposition: not a Proposition")
        if belief_depth(self.proposition) + 1 > MAX_BELIEF_NESTING:
            raise ValidationError(f"candidate belief_of nesting exceeds {MAX_BELIEF_NESTING}: reserved (S-11)")

    def to_dict(self) -> dict[str, Any]:
        return {"form": "belief_of", "holder": self.holder, "proposition": self.proposition.to_dict()}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "candidate form(belief_of)", ["form", "holder", "proposition"])
        return cls(holder=as_str(o["holder"], "candidate.holder"), proposition=proposition_from_dict(o["proposition"]))


CandidateForm = ValueForm | SetForm | EmptyForm | NotValueForm | NotMemberForm | BeliefOfForm
CANDIDATE_FORM_TYPES = (ValueForm, SetForm, EmptyForm, NotValueForm, NotMemberForm, BeliefOfForm)
NEGATIVE_FORM_TYPES = (NotValueForm, NotMemberForm)

_FORM_BY_NAME: dict[str, type[Any]] = {t.form: t for t in CANDIDATE_FORM_TYPES}


def candidate_form_from_dict(d: Any) -> CandidateForm:
    if not isinstance(d, dict) or "form" not in d:
        raise ValidationError("candidate form: expected an object with a 'form'")
    cls = _FORM_BY_NAME.get(d["form"]) if isinstance(d["form"], str) else None
    if cls is None:
        raise ValidationError(f"candidate form: unknown form {d['form']!r}; expected one of {sorted(_FORM_BY_NAME)}")
    out: CandidateForm = cls.from_dict(d)
    return out


@dataclass(frozen=True, kw_only=True)
class Candidate(Codec):
    """A candidate answer for a key. ``id`` = SHA-256 of the canonical JSON of (key, form), so it
    is stable across belief versions. It is derived, never supplied: ``from_dict`` checks it.
    """

    key: Key
    form: CandidateForm
    id: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.key, Key):
            raise ValidationError("candidate.key: not a Key")
        if not isinstance(self.form, CANDIDATE_FORM_TYPES):
            raise ValidationError("candidate.form: not a CandidateForm")
        set_field(self, "id", candidate_id(self.key, self.form))

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "key": self.key.to_dict(), "form": self.form.to_dict()}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "candidate", ["key", "form"], ["id"])
        c = cls(key=Key.from_dict(o["key"]), form=candidate_form_from_dict(o["form"]))
        if "id" in o and o["id"] != c.id:
            raise ValidationError(f"candidate.id mismatch: given {o['id']!r}, computed {c.id!r}")
        return c


def candidate_id(key: Key, form: CandidateForm) -> str:
    return hashlib.sha256(canonical_json({"key": key.to_dict(), "form": form.to_dict()}).encode("utf-8")).hexdigest()
