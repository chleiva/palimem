"""Principals and the authority grant table (S-07, decided; S-02).

* A principal id is ``<kind>:<name>`` with kind in ``agent | user | connector | system``. The kind is
  carried in the id prefix and fixed by the host at registration; the LLM cannot choose it.
* Authority is a declarative, ordered list of :class:`AuthorityRule`; first match wins; the default
  grant is *own source only*. The table is a versioned **admission input**: any change to it is a
  new :class:`AuthorityTable` with a higher ``admission_version`` (:meth:`AuthorityTable.successor`),
  and old reports are not silently re-evaluated.
* ``origin_group`` is for corroboration counting only. The two origin-group ``Who`` kinds exist only
  for the compat profile ``revise-stream-v1`` (paper A-SELF) and are rejected under ``open-world``
  (:func:`validate_rules_for_profile`).

Hard invariant (checked in ``AuthorityRule.__post_init__``, so it applies wherever a rule is
loaded or changed, and no admission-version bump can override it): **no rule may grant an explicit
agent principal ``withdraw`` or ``correct`` over an ``external_observation``**, nor a
``targets: source|any`` withdraw/correct. Wildcard ``Who`` kinds (``any``, ``origin_group``,
``target_*``) cannot be checked against principals at load time, so by contract they never match a
principal of kind ``agent`` for withdraw/correct over non-agent-class origins; that is enforced at
evaluation time by admission (T-D2) and covered by fixture tb-19 and its siblings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Any, Self

from palimem.types._codec import (
    Codec,
    ValidationError,
    as_enum,
    as_int,
    as_obj,
    as_str,
    check_nat,
    check_nonempty,
    opt,
    set_field,
    tuple_of,
)
from palimem.types.enums import (
    AGENT_ORIGINS,
    Origin,
    Power,
    PrincipalKind,
    Profile,
    Targets,
    WhoKind,
)
from palimem.types.values import Key

_PRINCIPAL_RE = re.compile(r"^(agent|user|connector|system):([A-Za-z0-9_.@/\-]+)$")

AGENT_CLASS_ORIGINS = AGENT_ORIGINS | {Origin.SIMULATION, Origin.COUNTERFACTUAL}
"""Origins an agent principal may ever be granted withdraw/correct over (never admissible evidence)."""


def parse_principal(s: Any, ctx: str = "principal") -> tuple[PrincipalKind, str]:
    if not isinstance(s, str):
        raise ValidationError(f"{ctx}: expected a typed principal id like 'agent:planner'")
    m = _PRINCIPAL_RE.match(s)
    if not m:
        raise ValidationError(f"{ctx}: '{s}' is not '<kind>:<name>' with kind in agent|user|connector|system")
    return PrincipalKind(m.group(1)), m.group(2)


def check_principal(s: Any, ctx: str = "principal") -> str:
    parse_principal(s, ctx)
    assert isinstance(s, str)
    return s


def principal_kind(s: str) -> PrincipalKind:
    return parse_principal(s)[0]


@dataclass(frozen=True, kw_only=True)
class KeyScope(Codec):
    """Key scope of a rule: glob patterns on the attribute name and on the canonical entity id."""

    attr: str = "*"
    entity: str = "*"

    def __post_init__(self) -> None:
        check_nonempty(self.attr, "scope.attr")
        check_nonempty(self.entity, "scope.entity")

    def matches(self, key: Key) -> bool:
        return fnmatchcase(key.attr, self.attr) and fnmatchcase(key.entity, self.entity)

    def to_dict(self) -> dict[str, Any]:
        return {"attr": self.attr, "entity": self.entity}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "key scope", [], ["attr", "entity"])
        return cls(attr=as_str(o.get("attr", "*"), "scope.attr"), entity=as_str(o.get("entity", "*"), "scope.entity"))


@dataclass(frozen=True, kw_only=True)
class Who(Codec):
    kind: WhoKind
    value: str | None = None  # a principal id (kind=principal) or an origin_group name (kind=origin_group)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, WhoKind):
            raise ValidationError("who.kind: not a WhoKind")
        if self.kind is WhoKind.PRINCIPAL:
            if self.value is None:
                raise ValidationError("who 'principal' requires a typed principal id")
            check_principal(self.value, "who.value")
        elif self.kind is WhoKind.ORIGIN_GROUP:
            if self.value is None:
                raise ValidationError("who 'origin_group' requires a group name")
            check_nonempty(self.value, "who.value")
        elif self.value is not None:
            raise ValidationError(f"who '{self.kind.value}' must not carry a value")

    @property
    def uses_origin_group(self) -> bool:
        return self.kind in (WhoKind.ORIGIN_GROUP, WhoKind.TARGET_ORIGIN_GROUP)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "value": self.value}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "who", ["kind"], ["value"])
        return cls(kind=as_enum(WhoKind, o["kind"], "who.kind"), value=opt(o.get("value"), lambda x: as_str(x, "who.value")))


@dataclass(frozen=True, kw_only=True)
class AuthorityRule(Codec):
    """``who`` may ``may`` on keys matching ``on``, over ``targets``, restricted to target reports
    whose origin is in ``over_origins`` (``None`` = any origin)."""

    who: Who
    may: tuple[Power, ...]
    on: KeyScope = field(default_factory=KeyScope)
    targets: Targets = Targets.REPORT
    over_origins: tuple[Origin, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.who, Who):
            raise ValidationError("authority.who: not a Who")
        if not self.may:
            raise ValidationError("authority.may must grant at least one power")
        for p in self.may:
            if not isinstance(p, Power):
                raise ValidationError("authority.may: not a Power")
        set_field(self, "may", tuple(sorted(set(self.may), key=lambda p: p.value)))
        if not isinstance(self.targets, Targets):
            raise ValidationError("authority.targets: not a Targets")
        if self.over_origins is not None:
            if not self.over_origins:
                raise ValidationError("authority.over_origins, if given, must be non-empty")
            for o in self.over_origins:
                if not isinstance(o, Origin):
                    raise ValidationError("authority.over_origins: not an Origin")
            set_field(self, "over_origins", tuple(sorted(set(self.over_origins), key=lambda o: o.value)))
        self._check_merge_shape()
        self._check_agent_invariant()

    def _check_merge_shape(self) -> None:
        """``merge`` is about entities, not about a target report: it is granted by identity (a named principal, or any
        non-agent principal), on its own, and never over report origins or a wider extent."""
        if Power.MERGE not in self.may:
            return
        if self.may != (Power.MERGE,):
            raise ValidationError("authority: 'merge' must be granted on its own, not together with other powers")
        if self.who.kind not in (WhoKind.PRINCIPAL, WhoKind.ANY):
            raise ValidationError("authority: 'merge' is granted to a named principal or to any non-agent principal")
        if self.over_origins is not None or self.targets is not Targets.REPORT:
            raise ValidationError("authority: 'merge' has no target reports: over_origins and targets do not apply")

    def _check_agent_invariant(self) -> None:
        if self.who.kind is not WhoKind.PRINCIPAL or self.who.value is None:
            return
        if principal_kind(self.who.value) is PrincipalKind.AGENT and Power.MERGE in self.may:
            raise ValidationError(f"authority: agent principal '{self.who.value}' can never be granted 'merge' (S-07 invariant)")
        if principal_kind(self.who.value) is not PrincipalKind.AGENT:
            return
        acting = {p for p in self.may if p in (Power.WITHDRAW, Power.CORRECT)}
        if not acting:
            return
        if self.targets is not Targets.REPORT:
            raise ValidationError(
                f"authority: agent principal '{self.who.value}' cannot be granted {sorted(p.value for p in acting)} "
                f"with targets '{self.targets.value}' (S-07 invariant)"
            )
        if self.over_origins is None or any(o not in AGENT_CLASS_ORIGINS for o in self.over_origins):
            raise ValidationError(
                f"authority: agent principal '{self.who.value}' cannot be granted withdraw/correct over "
                "external evidence; restrict over_origins to agent-class origins (S-07 invariant)"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "who": self.who.to_dict(),
            "may": [p.value for p in self.may],
            "on": self.on.to_dict(),
            "targets": self.targets.value,
            "over_origins": None if self.over_origins is None else [o.value for o in self.over_origins],
        }

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "authority rule", ["who", "may"], ["on", "targets", "over_origins"])
        oo = o.get("over_origins")
        return cls(
            who=Who.from_dict(o["who"]),
            may=tuple_of(o["may"], "authority.may", lambda x, c: as_enum(Power, x, c)),
            on=KeyScope.from_dict(o["on"]) if "on" in o else KeyScope(),
            targets=as_enum(Targets, o.get("targets", "report"), "authority.targets"),
            over_origins=None if oo is None else tuple_of(oo, "authority.over_origins", lambda x, c: as_enum(Origin, x, c)),
        )


ALL_POWERS = (Power.CORRECT, Power.WITHDRAW, Power.DISPUTE)  # the report-targeting powers; ``merge`` is granted explicitly

HOST_MERGE_KINDS = (PrincipalKind.SYSTEM, PrincipalKind.USER)
"""Principal kinds that may merge by default (the host's own decision). Anyone else needs an explicit ``merge`` grant."""


def may_merge(actor: str, rules: tuple[AuthorityRule, ...] = (), *, key: Key | None = None) -> bool:
    """May ``actor`` record an entity merge (author ruling 2026-10-05)?

    Never for an ``agent`` principal, whatever the rules say (the invariant is also enforced when a rule is built).
    ``system`` and ``user`` principals may by default; any other principal needs an explicit ``merge`` grant naming it
    (or ``any``, which is every non-agent principal), whose key scope matches ``key`` when one is given."""
    kind = principal_kind(actor)
    if kind is PrincipalKind.AGENT:
        return False
    if kind in HOST_MERGE_KINDS:
        return True
    for rule in rules:
        if Power.MERGE not in rule.may:
            continue
        if rule.who.kind is WhoKind.PRINCIPAL and rule.who.value != actor:
            continue
        if key is not None and not rule.on.matches(key):
            continue
        return True
    return False

DEFAULT_RULES: tuple[AuthorityRule, ...] = (
    # default grant: the target's own source only (S-02), for every power
    AuthorityRule(who=Who(kind=WhoKind.TARGET_SOURCE), may=ALL_POWERS),
    # built-in agent rule (S-07 amendment): an agent may withdraw/correct what it authored itself,
    # across sessions, and only over agent-class origins
    AuthorityRule(
        who=Who(kind=WhoKind.TARGET_ACTOR),
        may=(Power.WITHDRAW, Power.CORRECT),
        over_origins=tuple(sorted(AGENT_CLASS_ORIGINS, key=lambda o: o.value)),
    ),
)

REVISE_STREAM_V1_RULES: tuple[AuthorityRule, ...] = (
    # S-07 "Effect on revise-stream-v1": source-level retraction by anyone, same-origin correction
    AuthorityRule(who=Who(kind=WhoKind.ANY), may=(Power.WITHDRAW,), targets=Targets.ANY),
    AuthorityRule(who=Who(kind=WhoKind.TARGET_ORIGIN_GROUP), may=(Power.CORRECT,)),
)


def default_authority_rules(profile: Profile) -> tuple[AuthorityRule, ...]:
    """Rules in force when a schema/table declares none (S-02: source-based product default,
    origin-based compat profile)."""
    return REVISE_STREAM_V1_RULES if profile is Profile.REVISE_STREAM_V1 else DEFAULT_RULES


def validate_rules_for_profile(rules: tuple[AuthorityRule, ...], profile: Profile, ctx: str = "authority") -> None:
    """``origin_group`` is for corroboration, never authority: refuse origin-group ``who`` kinds
    outside the compat profile (S-02)."""
    if profile is Profile.OPEN_WORLD:
        for r in rules:
            if r.who.uses_origin_group:
                raise ValidationError(
                    f"{ctx}: who '{r.who.kind.value}' is only valid under profile 'revise-stream-v1'; "
                    "origin_group is for corroboration, never authority"
                )


@dataclass(frozen=True, kw_only=True)
class AuthorityTable(Codec):
    """The grant table in force at one admission version (a versioned admission input)."""

    admission_version: int
    rules: tuple[AuthorityRule, ...]

    def __post_init__(self) -> None:
        check_nat(self.admission_version, "authority_table.admission_version", minimum=1)
        for r in self.rules:
            if not isinstance(r, AuthorityRule):
                raise ValidationError("authority_table.rules: not AuthorityRule objects")

    def successor(self, rules: tuple[AuthorityRule, ...]) -> AuthorityTable:
        """A changed grant table is a new admission version; the old table stays answerable."""
        return AuthorityTable(admission_version=self.admission_version + 1, rules=tuple(rules))

    def to_dict(self) -> dict[str, Any]:
        return {"admission_version": self.admission_version, "rules": [r.to_dict() for r in self.rules]}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "authority_table", ["admission_version", "rules"])
        return cls(
            admission_version=as_int(o["admission_version"], "authority_table.admission_version"),
            rules=tuple_of(o["rules"], "authority_table.rules", lambda x, c: AuthorityRule.from_dict(x)),
        )
