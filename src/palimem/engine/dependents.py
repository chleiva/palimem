"""Which derived keys can an appended report change? (Lane O, docs/PERFORMANCE.md "After optimisation")

The revision of a derived key ``(e, a)`` reads base keys chosen by the rules of ``a``: the head entity's own keys, and
keys of entities that are *values* of earlier literals (``work_city(?e, ?c) <- employer(?e, ?x), hq_city(?x, ?c)``
reads ``(e, employer)`` and then ``(x, hq_city)`` for every ``x`` the employer key can name). Re-justifying every
derived key of every entity on each append (what the first version did) makes an append cost grow with the
**entity count**. This module answers the reverse question exactly enough to avoid that:

    given the keys that changed in this revision, which derived keys of attribute ``a`` may read one of them?

Two pieces:

* :class:`DependentsPlans`, a static analysis of the rules: for a derived attribute ``a`` and a changed attribute
  ``b``, how to get from a changed key ``(c, b)`` back to the head entities whose derivation reads it. Each literal
  ``b(t, ...)`` of a rule gives one *plan* by looking at its entity term ``t``: the head variable (the head entity *is*
  ``c``), a constant, a variable bound as the value of an earlier literal ``a_j(t_j, t)`` (the heads are the entities
  whose ``a_j`` key names ``c``, followed back through ``t_j``), or an unbound variable (every entity: the engine
  iterates the whole universe there).
* :class:`ValueIndex`, the reverse map ``(attr, value) -> entities whose key holds that value`` for the attributes
  that bind entity variables ("binders"). It is a cache of the stored beliefs, never a source of truth: the pipeline
  drops it whenever a belief could have changed outside a revision (completion, erasure repair, a rolled-back
  append) and rebuilds it lazily from the store.

Every answer is a **superset** of the true dependents (values are taken over all times and all worlds), and a plan
that cannot be resolved falls back to "every entity", so the targeted revision recomputes at least what the
exhaustive one would have changed. ``Pipeline(exhaustive=True)`` keeps the exhaustive form for audits, and the tests
compare the two.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from palimem.kernel import KernelSchema, RuleSpec
from palimem.types import (
    Belief,
    BeliefOfForm,
    Candidate,
    KernelStatus,
    Key,
    SetForm,
    ValueForm,
)
from palimem.types import Segment as PSegment

# a plan: ("direct", ()) | ("chain", (a_j, a_k, ...)) | ("const", <entity>) | ("all", ())
Plan = tuple[str, tuple[str, ...]]


def _is_var(x: object) -> bool:
    return isinstance(x, str) and x.startswith("?")


def _plan_for(rule: RuleSpec, term: str, limit: int) -> Plan | None:
    """The plan for a literal whose entity term is ``term``, binders searched among ``rule.body[:limit]``.
    ``None`` means "give up: every entity"."""
    head_var = str(rule.head[1])
    if term == head_var:
        return ("direct", ())
    if not _is_var(term):
        return ("const", (term,))
    for j, (aj, tj, vj) in enumerate(rule.body[:limit]):
        if _is_var(vj) and str(vj) == term:
            sub = _plan_for(rule, str(tj), j)
            if sub is None or sub[0] == "const":
                return None  # a chain through a constant entity: not worth a special case
            return ("chain", (aj, *sub[1]))
    return None  # a variable nothing binds: the engine iterates every entity


@dataclass(frozen=True)
class DependentsPlans:
    """Static reverse plans of one kernel schema: ``plans[(derived attr a, changed attr b)]``."""

    plans: Mapping[tuple[str, str], tuple[Plan, ...]]
    binders: frozenset[str]  # attributes whose values bind entity variables (the value index covers exactly these)

    @classmethod
    def of(cls, ks: KernelSchema) -> DependentsPlans:
        plans: dict[tuple[str, str], list[Plan]] = {}
        binders: set[str] = set()
        derived = [a for a, s in ks.attrs.items() if s.derived]
        for a in derived:
            for r in ks.rules_for(a):
                lits = [(attr, ent, True) for (attr, ent, _v) in r.body] + [(attr, ent, False) for (attr, ent, _v) in r.exceptions]
                for i, (b, ent, in_body) in enumerate(lits):
                    plan = _plan_for(r, str(ent), i if in_body else len(r.body))
                    plans.setdefault((a, b), []).append(plan if plan is not None else ("all", ()))
                    if plan is not None and plan[0] == "chain":
                        binders.update(plan[1])
        return cls(plans={k: tuple(dict.fromkeys(v)) for k, v in plans.items()}, binders=frozenset(binders))

    def reads(self, attr: str, changed_attr: str) -> bool:
        return (attr, changed_attr) in self.plans


def belief_values(b: Belief | None) -> frozenset[str]:
    """Every value any candidate world of the belief contains, at any time (the entity names it can bind)."""
    if b is None:
        return frozenset()
    out: set[str] = set()
    for seg in b.segments:
        out.update(_segment_values(seg))
    return frozenset(out)


def _segment_values(seg: PSegment) -> Iterable[str]:
    cands: tuple[Candidate, ...]
    if seg.kernel_status is KernelStatus.ESTABLISHED and seg.established is not None:
        cands = (seg.established,)
    elif seg.kernel_status is KernelStatus.UNRESOLVED:
        cands = tuple(seg.alternatives)
    else:
        return ()
    vals: list[str] = []
    for c in cands:
        f = c.form
        if isinstance(f, ValueForm):
            vals.append(str(f.value))
        elif isinstance(f, SetForm):
            vals.extend(str(v) for v in f.values)
        elif isinstance(f, BeliefOfForm):
            continue
    return vals


class ValueIndex:
    """``(attr, value) -> entities`` for the binder attributes, kept in step with the stored beliefs."""

    def __init__(self, binders: Iterable[str]) -> None:
        self.binders = frozenset(binders)
        self._by: dict[str, dict[str, set[str]]] = {a: {} for a in self.binders}
        self._of: dict[Key, frozenset[str]] = {}

    def update(self, key: Key, belief: Belief | None) -> None:
        if key.attr not in self.binders:
            return
        new = belief_values(belief)
        old = self._of.get(key, frozenset())
        if new == old:
            return
        table = self._by[key.attr]
        for v in old - new:
            ents = table.get(v)
            if ents is not None:
                ents.discard(key.entity)
                if not ents:
                    del table[v]
        for v in new - old:
            table.setdefault(v, set()).add(key.entity)
        if new:
            self._of[key] = new
        else:
            self._of.pop(key, None)

    def entities(self, attr: str, value: str) -> frozenset[str]:
        return frozenset(self._by[attr].get(value, ()))

    def size(self) -> int:
        return sum(len(v) for t in self._by.values() for v in t.values())


def resolve(
    plans: DependentsPlans,
    attr: str,
    changed: Iterable[Key],
    index: ValueIndex | None,
    entities: Sequence[str],
) -> set[str] | None:
    """The head entities whose derived key of ``attr`` may read one of the ``changed`` keys; ``None`` = every entity
    (a plan that cannot be resolved, or no index to resolve a chain with)."""
    heads: set[str] = set()
    for key in changed:
        for kind, arg in plans.plans.get((attr, key.attr), ()):
            if kind == "direct":
                heads.add(key.entity)
            elif kind == "const":
                if key.entity == arg[0]:
                    return None
            elif kind == "chain":
                if index is None:
                    return None
                cur: set[str] = {key.entity}
                for aj in arg:
                    nxt: set[str] = set()
                    for v in cur:
                        nxt |= index.entities(aj, v)
                    cur = nxt
                    if not cur:
                        break
                heads |= cur
            else:
                return None
    return heads
