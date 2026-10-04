"""Attr (schema entry) and Schema (design v0.3 §Data and API: Schema entry).

Authority (S-02, S-07, decided): ``Attr.authority`` is a flat tuple of typed
:class:`~palimem.types.authority.AuthorityRule` grants (the per-cue lists of design v0.3 are
replaced: each rule names the powers it grants). Rules here are scoped to this attribute. Empty means
"the profile default" (:func:`~palimem.types.authority.default_authority_rules`). ``origin_group``
is for corroboration only, so origin-group ``who`` kinds are rejected under the open-world profile
(:meth:`Attr.validate_for_profile`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from fnmatch import fnmatchcase
from typing import Any, Self

from palimem.types._codec import (
    Codec,
    ValidationError,
    as_bool,
    as_enum,
    as_int,
    as_obj,
    as_str,
    check_nat,
    check_nonempty,
    check_order,
    norm_ts,
    opt,
    opt_ts_str,
    set_field,
    ts_from_str,
    tuple_of,
)
from palimem.types.authority import AuthorityRule, validate_rules_for_profile
from palimem.types.enums import AttrClass, CompletenessMode, Profile, ValueType
from palimem.types.values import (
    BeliefOfProp,
    EnumerationProp,
    MemberProp,
    NotMemberProp,
    NotValueProp,
    Proposition,
    ValueProp,
)


@dataclass(frozen=True, kw_only=True)
class Interval(Codec):
    """Valid-time interval; ``None`` ends are open."""

    start: datetime | None = None
    end: datetime | None = None

    def __post_init__(self) -> None:
        set_field(self, "start", norm_ts(self.start, "interval.start"))
        set_field(self, "end", norm_ts(self.end, "interval.end"))
        check_order(self.start, self.end, "interval")

    def to_dict(self) -> dict[str, Any]:
        return {"start": opt_ts_str(self.start), "end": opt_ts_str(self.end)}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "interval", [], ["start", "end"])
        return cls(
            start=opt(o.get("start"), lambda x: ts_from_str(x, "interval.start")),
            end=opt(o.get("end"), lambda x: ts_from_str(x, "interval.end")),
        )


@dataclass(frozen=True, kw_only=True)
class CompletenessScope(Codec):
    """Scope of a ``declared`` closed world. ``None`` fields mean 'all' (the compat profile's
    ``declared(all)`` is ``CompletenessScope()``)."""

    source_classes: tuple[str, ...] | None = None
    interval: Interval | None = None

    def __post_init__(self) -> None:
        if self.source_classes is not None:
            for i, s in enumerate(self.source_classes):
                check_nonempty(s, f"scope.source_classes[{i}]")
            set_field(self, "source_classes", tuple(sorted(set(self.source_classes))))
        if self.interval is not None and not isinstance(self.interval, Interval):
            raise ValidationError("scope.interval: not an Interval")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_classes": None if self.source_classes is None else list(self.source_classes),
            "interval": None if self.interval is None else self.interval.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "scope", [], ["source_classes", "interval"])
        sc = o.get("source_classes")
        return cls(
            source_classes=None if sc is None else tuple_of(sc, "scope.source_classes", as_str),
            interval=opt(o.get("interval"), Interval.from_dict),
        )


@dataclass(frozen=True, kw_only=True)
class Completeness(Codec):
    """``open`` (default: absence is unknown) | ``declared(scope)`` | ``by_enumeration``."""

    mode: CompletenessMode = CompletenessMode.OPEN
    scope: CompletenessScope | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, CompletenessMode):
            raise ValidationError("completeness.mode: not a CompletenessMode")
        if self.mode is CompletenessMode.DECLARED and self.scope is None:
            raise ValidationError("completeness 'declared' requires a scope (use CompletenessScope() for all)")
        if self.mode is not CompletenessMode.DECLARED and self.scope is not None:
            raise ValidationError(f"completeness '{self.mode.value}' must not carry a scope")

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode.value, "scope": None if self.scope is None else self.scope.to_dict()}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "completeness", ["mode"], ["scope"])
        return cls(mode=as_enum(CompletenessMode, o["mode"], "completeness.mode"), scope=opt(o.get("scope"), CompletenessScope.from_dict))


@dataclass(frozen=True, kw_only=True)
class Rule(Codec):
    """Derivation rule of a ``derived`` attribute: reads other attributes through ``fn``."""

    reads: tuple[str, ...]
    fn: str

    def __post_init__(self) -> None:
        if not self.reads:
            raise ValidationError("rule.reads must name at least one attribute")
        for i, r in enumerate(self.reads):
            check_nonempty(r, f"rule.reads[{i}]")
        if len(set(self.reads)) != len(self.reads):
            raise ValidationError("rule.reads has duplicates")
        check_nonempty(self.fn, "rule.fn")

    def to_dict(self) -> dict[str, Any]:
        return {"reads": list(self.reads), "fn": self.fn}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "rule", ["reads", "fn"])
        return cls(reads=tuple_of(o["reads"], "rule.reads", as_str), fn=as_str(o["fn"], "rule.fn"))


@dataclass(frozen=True, kw_only=True)
class Attr(Codec):
    name: str
    attr_class: AttrClass  # JSON name "class"
    value_type: ValueType
    inertia: bool = False  # S-08: stays a per-attribute boolean
    rule: Rule | None = None
    completeness: Completeness = field(default_factory=Completeness)
    authority: tuple[AuthorityRule, ...] = ()

    def __post_init__(self) -> None:
        check_nonempty(self.name, "attr.name")
        if not isinstance(self.attr_class, AttrClass):
            raise ValidationError("attr.class: not an AttrClass")
        if not isinstance(self.value_type, ValueType):
            raise ValidationError("attr.value_type: not a ValueType")
        if not isinstance(self.inertia, bool):
            raise ValidationError("attr.inertia: must be a boolean")
        if self.attr_class is AttrClass.DERIVED:
            if self.rule is None:
                raise ValidationError(f"attr '{self.name}': a derived attribute needs a rule")
            if self.name in self.rule.reads:
                raise ValidationError(f"attr '{self.name}': a rule cannot read its own attribute")
        elif self.rule is not None:
            raise ValidationError(f"attr '{self.name}': only derived attributes carry a rule")
        for r in self.authority:
            if not isinstance(r, AuthorityRule):
                raise ValidationError(f"attr '{self.name}': authority entries must be AuthorityRule objects")
            if not fnmatchcase(self.name, r.on.attr):
                raise ValidationError(
                    f"attr '{self.name}': authority rule scoped to attribute pattern '{r.on.attr}' "
                    "does not match this attribute"
                )

    def validate_for_profile(self, profile: Profile) -> None:
        """Profile-specific checks: origin-group authority is compat-profile only (S-02)."""
        validate_rules_for_profile(self.authority, profile, f"attr '{self.name}'")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "class": self.attr_class.value,
            "value_type": self.value_type.value,
            "inertia": self.inertia,
            "rule": None if self.rule is None else self.rule.to_dict(),
            "completeness": self.completeness.to_dict(),
            "authority": [r.to_dict() for r in self.authority],
        }

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "attr", ["name", "class", "value_type"], ["inertia", "rule", "completeness", "authority"])
        return cls(
            name=as_str(o["name"], "attr.name"),
            attr_class=as_enum(AttrClass, o["class"], "attr.class"),
            value_type=as_enum(ValueType, o["value_type"], "attr.value_type"),
            inertia=as_bool(o.get("inertia", False), "attr.inertia"),
            rule=opt(o.get("rule"), Rule.from_dict),
            completeness=Completeness.from_dict(o["completeness"]) if "completeness" in o else Completeness(),
            authority=tuple_of(o.get("authority", []), "attr.authority", lambda x, c: AuthorityRule.from_dict(x)),
        )


def check_proposition_for_attr(attr: Attr, proposition: Proposition) -> None:
    """Design v0.3 §Write API: validate the proposition *form* against the key's class.

    Pure type-level check; it does not touch any store.
    """
    if attr.attr_class is AttrClass.DERIVED:
        raise ValidationError(f"attr '{attr.name}' is derived: it is computed by its rule and never asserted directly")
    if isinstance(proposition, BeliefOfProp):
        return  # attributed claims are admissible for any non-derived class
    single = attr.attr_class in (AttrClass.SINGLE_STABLE, AttrClass.SINGLE_CHANGEABLE)
    if single and not isinstance(proposition, ValueProp | NotValueProp):
        raise ValidationError(f"attr '{attr.name}' is single-valued: form '{proposition.form}' is not allowed")
    if not single and not isinstance(proposition, MemberProp | NotMemberProp | EnumerationProp):
        raise ValidationError(f"attr '{attr.name}' is multi-valued: form '{proposition.form}' is not allowed")


@dataclass(frozen=True, kw_only=True)
class Schema(Codec):
    """A versioned set of declared attributes. Referential checks only; the static exactness
    check (same-key relations, disjoint rule closures) is kernel work (T-B5), not done here."""

    version: int
    attrs: tuple[Attr, ...]

    def __post_init__(self) -> None:
        check_nat(self.version, "schema.version", minimum=1)
        names = [a.name for a in self.attrs]
        if len(set(names)) != len(names):
            raise ValidationError("schema: duplicate attribute names")
        known = set(names)
        for a in self.attrs:
            if a.rule is not None:
                missing = [r for r in a.rule.reads if r not in known]
                if missing:
                    raise ValidationError(f"schema: attr '{a.name}' reads undeclared attribute(s) {missing}")

    def attr(self, name: str) -> Attr:
        for a in self.attrs:
            if a.name == name:
                return a
        raise KeyError(name)

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "attrs": [a.to_dict() for a in self.attrs]}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "schema", ["version", "attrs"])
        return cls(
            version=as_int(o["version"], "schema.version"),
            attrs=tuple_of(o["attrs"], "schema.attrs", lambda x, c: Attr.from_dict(x)),
        )
