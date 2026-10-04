"""Derived keys: Horn rules with defeasible exceptions over base candidates (SEMANTICS §1, §7).

A derived belief is computed by forward-chaining the declared rules over the *base keys'* candidate
families at one valid time. Keys are independent, so the union over the product of interpretations equals
the rule engine's branching over per-key unions **provided a base key is reached at most once per derivation
path**; :func:`palimem.kernel.exactness.check_schema` enforces that statically.

Compat profile (``revise-stream-v1``) evaluates exceptions closed-world, as the paper does: an exception
whose attribute has no evidence is false, so it does not block the rule. The open-world behaviour of S-10
(an exception follows the completeness of its attribute, so an *unknown* exception may block) is documented
but not implemented: only what the compat profile needs is.

The store supplies base evidence through a :class:`Provider`; pinning (which version of each base key a
derived belief consumed) is the store's business, expressed by which justifications it passes in.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from palimem.kernel.justify import Family, Justification, build_segments, segment_at
from palimem.kernel.spec import KernelSchema, KernelUnsupported, RuleSpec
from palimem.types import Key, Profile, SemanticConfig
from palimem.types import Segment as PSegment
from palimem.types._codec import Value

MAX_DEPTH = 8


class Provider(Protocol):
    """Source of base-key candidates for the rule engine."""

    def candidates(self, key: Key, t: int) -> Family: ...

    def breakpoints(self, key: Key) -> frozenset[int]: ...


class JustificationProvider:
    """A provider over per-key :class:`Justification` objects. A key with none has no evidence."""

    def __init__(self, justifications: Mapping[Key, Justification]) -> None:
        self._j = justifications

    def candidates(self, key: Key, t: int) -> Family:
        j = self._j.get(key)
        return frozenset({frozenset()}) if j is None else j.candidates_at(t)

    def breakpoints(self, key: Key) -> frozenset[int]:
        j = self._j.get(key)
        return frozenset() if j is None else j.breakpoints()


def is_var(x: object) -> bool:
    return isinstance(x, str) and x.startswith("?")


@dataclass
class _Engine:
    schema: KernelSchema
    provider: Provider
    memo: dict[tuple[str, str, int], Family] = field(default_factory=dict)

    def candidates_at(self, entity: str, attr: str, t: int, depth: int = 0) -> Family:
        spec = self.schema.spec(attr)
        if not spec.derived:
            return self.provider.candidates(Key(entity=entity, attr=attr), t)
        ck = (entity, attr, t)
        hit = self.memo.get(ck)
        if hit is not None:
            return hit
        out = self._derive(entity, attr, t, depth)
        self.memo[ck] = out
        return out

    def _derive(self, e: str, a: str, t: int, depth: int) -> Family:
        if depth > MAX_DEPTH:
            raise RecursionError("rule recursion too deep")
        worlds: set[frozenset[Value]] = {frozenset()}
        rules = self.schema.rules_for(a)
        if not rules:
            return frozenset(worlds)
        for r in rules:
            rw = self._eval_rule(r, e, t, depth)
            worlds = {w1 | w2 for w1 in worlds for w2 in rw}
        return frozenset(worlds)

    def _eval_rule(self, r: RuleSpec, e: str, t: int, depth: int) -> set[frozenset[Value]]:
        _head_attr, head_e, head_v_raw = r.head
        head_v = str(head_v_raw)
        entities = self.schema.entities

        def rec(lits: tuple[tuple[str, str, Value], ...], b: dict[str, Value]) -> set[frozenset[Value]]:
            if not lits:
                v = b.get(head_v)
                if v is None:
                    return {frozenset()}
                # exceptions: evaluated per world; an exception that holds blocks the rule
                outs: set[frozenset[Value]] = {frozenset([v])}
                for xa, xe, xv in r.exceptions:
                    ent = str(b.get(xe, xe))
                    exc = self.candidates_at(ent, xa, t, depth + 1)
                    new: set[frozenset[Value]] = set()
                    for w in outs:
                        for S in exc:
                            new.add(frozenset() if xv in S else w)
                    outs = new
                return outs
            (la, lx, ly), rest = lits[0], lits[1:]
            xs: list[str] = [str(b[lx])] if lx in b else (list(entities) if is_var(lx) else [lx])
            worlds_out: set[frozenset[Value]] = set()
            for x in xs:
                b1: dict[str, Value] = {**b, lx: x}
                cands = self.candidates_at(x, la, t, depth + 1)
                for S in cands:  # each candidate set is one world
                    if is_var(ly):
                        lyv = str(ly)
                        ys = [y for y in S if (lyv not in b1 or b1[lyv] == y)]
                    else:
                        ys = [ly] if ly in S else []
                    if not ys:
                        worlds_out.add(frozenset())
                        continue
                    combo: set[frozenset[Value]] = {frozenset()}  # several bindings in one world: union
                    for y in ys:
                        sub = rec(rest, {**b1, str(ly): y})
                        combo = {c | s for c in combo for s in sub}
                    worlds_out |= combo
            return worlds_out

        return rec(r.body, {head_e: e})


def base_attrs_closure(schema: KernelSchema, attr: str, _seen: frozenset[str] = frozenset()) -> set[str]:
    """Base (observed) attributes a derived attribute can read, transitively."""
    spec = schema.spec(attr)
    if not spec.derived:
        return {attr}
    if attr in _seen:
        raise KernelUnsupported(f"rule cycle through {attr!r}")
    out: set[str] = set()
    for r in schema.rules_for(attr):
        for la, _x, _y in r.body + r.exceptions:
            out |= base_attrs_closure(schema, la, _seen | {attr})
    return out


@dataclass(frozen=True, eq=False)
class DerivedJustification:
    """The justification of one derived key (same view methods as :class:`Justification`)."""

    key: Key
    schema: KernelSchema
    profile: Profile
    _engine: _Engine
    _bps: frozenset[int]
    _segs: list[tuple[PSegment, ...]] = field(default_factory=list, repr=False, compare=False)

    def candidates_at(self, t: int) -> Family:
        return self._engine.candidates_at(self.key.entity, self.key.attr, t)

    def breakpoints(self) -> frozenset[int]:
        return self._bps

    def segments(self) -> tuple[PSegment, ...]:
        if not self._segs:
            spec = self.schema.spec(self.key.attr)
            self._segs.append(build_segments(self.key, spec, self.profile, self._bps, self.candidates_at))
        return self._segs[0]

    def segment_at(self, day: int) -> PSegment:
        return segment_at(self.segments(), day)

    def holds_truths(self, value: Value, t: int) -> list[bool]:
        return [value in c for c in self.candidates_at(t)]


def justify_derived(
    schema: KernelSchema,
    key: Key,
    provider: Provider,
    semantic: SemanticConfig,
    *,
    profile: Profile | None = None,
) -> DerivedJustification:
    """Justify a derived key over base candidates supplied by ``provider``."""
    spec = schema.spec(key.attr)
    if not spec.derived:
        raise ValueError(f"{key.attr!r} is a base attribute: use justify_key")
    keys = {Key(entity=e, attr=a) for e in schema.entities for a in base_attrs_closure(schema, key.attr)}
    bps: set[int] = set()
    for k in keys:
        bps |= provider.breakpoints(k)
    return DerivedJustification(
        key=key,
        schema=schema,
        profile=profile if profile is not None else semantic.profile,
        _engine=_Engine(schema, provider),
        _bps=frozenset(bps),
    )


CandidatesFn = Callable[[int], Family]
