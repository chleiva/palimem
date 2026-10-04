"""Kernel-level schema: the flags the justification semantics actually reads.

The paper's attribute table (``revise_stream.model.AttributeSpec``) has six flags: ``cardinality``,
``changeable``, ``error_allowed``, ``derived``, ``competing_values`` and (implicitly, law of inertia A4)
inertia. The contract's :class:`palimem.types.Attr` carries a *class* (``single_stable``,
``single_changeable``, ``multi_set``, ``derived``) and an ``inertia`` boolean. Three paper attribute
kinds cannot be expressed by the contract classes (see ``KernelSchema.from_schema``):

* multi-valued **changeable** keys with competing values (the Alex ``residence`` slot; Setting 1 has
  500 of them),
* the **cardinality** of a derived attribute (single vs multi),
* ``error_allowed`` / ``competing_values`` as explicit flags.

So the kernel works on its own :class:`AttrSpec`; the contract ``Schema`` maps onto it where it can, and
the compatibility harness builds the full kernel schema directly. The gap is reported to the author.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from palimem.types import Attr, AttrClass, Profile, Schema
from palimem.types._codec import Value

Lit = tuple[str, str, Value]  # (attr, entity-or-variable, value-or-variable); exceptions may name a constant value
Cardinality = Literal["single", "multi"]


class KernelUnsupported(NotImplementedError):
    """The input is outside what the kernel's validated semantics cover (no oracle exists for it)."""


@dataclass(frozen=True, eq=False)
class AttrSpec:
    name: str
    cardinality: Cardinality
    changeable: bool
    error_allowed: bool = True
    competing_values: bool = True
    derived: bool = False
    inertia: bool = True  # the paper applies the law of inertia (A4) to every attribute

    def __post_init__(self) -> None:
        if self.cardinality not in ("single", "multi"):
            raise ValueError(f"attr {self.name!r}: cardinality must be 'single' or 'multi'")


@dataclass(frozen=True, eq=False)
class RuleSpec:
    """A Horn rule with defeasible exceptions (SEMANTICS §1): ``head <- body`` unless an exception holds."""

    id: str
    head: Lit
    body: tuple[Lit, ...]
    kind: str = "defeasible"
    exceptions: tuple[Lit, ...] = ()

    def to_json_obj(self) -> dict[str, object]:
        return {
            "id": self.id,
            "head": list(self.head),
            "body": [list(b) for b in self.body],
            "kind": self.kind,
            "exceptions": [list(x) for x in self.exceptions],
        }

    @classmethod
    def from_json_obj(cls, o: Mapping[str, object]) -> RuleSpec:
        def lit(x: object) -> Lit:
            if not (
                isinstance(x, list | tuple)
                and len(x) == 3
                and isinstance(x[0], str)
                and isinstance(x[1], str)
                and isinstance(x[2], str | int | float | bool)
            ):
                raise ValueError(f"rule literal must be [attr, entity, value-or-variable], got {x!r}")
            return (x[0], x[1], x[2])

        body = o["body"]
        exc = o.get("exceptions", [])
        assert isinstance(body, list) and isinstance(exc, list)
        return cls(
            id=str(o["id"]),
            head=lit(o["head"]),
            body=tuple(lit(b) for b in body),
            kind=str(o.get("kind", "defeasible")),
            exceptions=tuple(lit(x) for x in exc),
        )


def rule_fn(cardinality: Cardinality, rules: Sequence[RuleSpec]) -> str:
    """Canonical text for ``Attr.rule.fn``: the contract leaves ``fn`` opaque, the kernel reads this JSON."""
    return json.dumps(
        {"cardinality": cardinality, "rules": [r.to_json_obj() for r in rules]},
        sort_keys=True,
        separators=(",", ":"),
    )


def parse_rule_fn(fn: str) -> tuple[Cardinality, tuple[RuleSpec, ...]]:
    try:
        o = json.loads(fn)
        card = o["cardinality"]
        rules = tuple(RuleSpec.from_json_obj(r) for r in o["rules"])
    except (ValueError, KeyError, TypeError) as e:
        raise KernelUnsupported(f"rule.fn is not kernel rule JSON ({e})") from e
    if card not in ("single", "multi"):
        raise KernelUnsupported(f"rule.fn cardinality {card!r} not in single|multi")
    return card, rules


@dataclass(frozen=True, eq=False)
class KernelSchema:
    attrs: Mapping[str, AttrSpec]
    rules: tuple[RuleSpec, ...] = ()
    entities: tuple[str, ...] = field(default=())

    def spec(self, attr: str) -> AttrSpec:
        try:
            return self.attrs[attr]
        except KeyError as e:
            raise KeyError(f"attribute {attr!r} is not declared in the schema") from e

    def rules_for(self, attr: str) -> tuple[RuleSpec, ...]:
        return tuple(r for r in self.rules if r.head[0] == attr)

    @classmethod
    def from_schema(
        cls, schema: Schema, *, entities: Sequence[str] = (), profile: Profile = Profile.OPEN_WORLD
    ) -> KernelSchema:
        """Map a contract :class:`Schema` onto kernel flags where the contract can express them.

        * ``single_stable`` -> single, not changeable; ``single_changeable`` -> single, changeable;
          ``multi_set`` -> multi, not changeable, values do not compete (a member is a member).
        * ``derived`` -> the rule JSON in ``Attr.rule.fn`` (see :func:`rule_fn`) carries cardinality and
          rules.
        * ``Attr.inertia`` must be true: the semantics of ``inertia=False`` are not specified (S-08 kept
          the boolean but did not define the false case), so the kernel refuses rather than invent one.
        """
        del profile  # the profile selects classification, not the schema mapping
        attrs: dict[str, AttrSpec] = {}
        rules: list[RuleSpec] = []
        for a in schema.attrs:
            attrs[a.name], rs = _spec_from_attr(a)
            rules.extend(rs)
        return cls(attrs=attrs, rules=tuple(rules), entities=tuple(entities))


def _spec_from_attr(a: Attr) -> tuple[AttrSpec, tuple[RuleSpec, ...]]:
    if a.attr_class is AttrClass.DERIVED:
        assert a.rule is not None
        card, rules = parse_rule_fn(a.rule.fn)
        for r in rules:
            if r.head[0] != a.name:
                raise KernelUnsupported(f"attr {a.name!r}: rule head {r.head[0]!r} is another attribute")
        return AttrSpec(name=a.name, cardinality=card, changeable=True, error_allowed=False, derived=True), rules
    if not a.inertia:
        raise KernelUnsupported(
            f"attr {a.name!r}: inertia=False has no specified semantics yet (S-08 kept the boolean but did "
            "not define the false case); set inertia=True"
        )
    if a.attr_class is AttrClass.SINGLE_STABLE:
        return AttrSpec(name=a.name, cardinality="single", changeable=False), ()
    if a.attr_class is AttrClass.SINGLE_CHANGEABLE:
        return AttrSpec(name=a.name, cardinality="single", changeable=True), ()
    return AttrSpec(name=a.name, cardinality="multi", changeable=False, competing_values=False), ()
