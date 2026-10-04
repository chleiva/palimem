"""Provenance (T-B4, decision S-12): subset-minimal environments per candidate, and the oracle's flat sets.

Two rules, kept apart (S-12: "the profile reproduces the oracle's rule and the product keeps the principled
one"):

**Principled (product).** The *support* of a candidate world ``S`` of a key at valid day ``t`` is the set of
**subset-minimal environments**: for each admissible interpretation whose candidate family at ``t`` contains
``S``, the reports that interpretation labels TRUE and that make up the runs *contributing* ``S`` at ``t``
(the value runs in ``S`` that cover ``t``); keep the subset-minimal ones. Reports that merely license the
interpretation (a competitor that explains another report's ERR label, the predecessor an ``A-CHG`` cue
presupposes) are not evidence for ``S``, and neither are reports that support the same value only in another
stretch of valid time: supports are per interval.
For a derived key the environments are over **base** reports along the derivation path (derivation pins are
explanatory, not evidence): an environment of a derived world is the join (union) of the environments of the
base worlds it consumed. Environments are antichains, bounded by :class:`EnvBudget`; above the cap the
result is *truncated*, which changes nothing about status or value.

**Oracle flat (profile ``revise-stream-v1``).** The study's ``gold.support_ids``: for an observed key, the
admitted assertions whose value is a candidate value at ``t`` (the union over all candidate worlds); for a
derived key, the admitted assertions of every ``(base key, value)`` binding used on any derivation path that
yields a candidate (exception bindings included, sub-traces folded in). This is a flat set with no notion of
environments, and it differs from the principled rule (e.g. it includes the reports of every alternative of
an unresolved key, and ignores which interpretation labelled them TRUE). The compat profile must reproduce
it exactly; :func:`oracle_flat_ids` and :func:`oracle_flat_ids_derived` do.

No import of ``derive`` / ``justify`` at runtime (they import this module); the engines are typed against
the :class:`SupportProvider` protocol.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from palimem.kernel.evidence import Ev
from palimem.kernel.spec import AttrSpec, KernelSchema, RuleSpec
from palimem.kernel.timeline import Interp, Run, Timeline, observed_candidates
from palimem.types import Key
from palimem.types._codec import Value

World = frozenset[Value]
Env = frozenset[str]
Envs = frozenset[Env]
Dist = dict[World, Envs]

DEFAULT_ENV_CAP = 256
"""Maximum number of minimal environments kept per candidate world. A bounded explanation, never a
different answer: above the cap the explanation is ``truncated`` (design v0.3 Resource contract)."""

MAX_LEVEL = 9  # a derived key is level 1; rule chains deeper than 8 are refused, as in the oracle

_E0: Envs = frozenset({frozenset()})


@dataclass
class EnvBudget:
    """Mutable explanation budget: the cap per antichain and whether any antichain was cut."""

    cap: int = DEFAULT_ENV_CAP
    truncated: bool = False
    minimal: bool = True
    """``False`` is a diagnostic mode (``harness.kernel_diff`` uses it to explain differences with the
    oracle's flat rule): keep *every* environment, not only the subset-minimal ones. Never the product."""


def minimize(envs: Iterable[Env], budget: EnvBudget) -> Envs:
    """The subset-minimal elements of ``envs`` (an antichain), at most ``budget.cap`` of them.

    Elements are visited by (size, sorted ids), so every strict subset of an element is visited before it
    and a single pass suffices. The cap keeps the first ``cap`` in that deterministic order."""
    out: list[Env] = []
    for e in sorted(set(envs), key=lambda x: (len(x), sorted(x))):
        if not budget.minimal or not any(m <= e for m in out):
            out.append(e)
    if len(out) > budget.cap:
        budget.truncated = True
        out = out[: budget.cap]
    return frozenset(out)


def join(a: Envs, b: Envs, budget: EnvBudget) -> Envs:
    """Environments of a conclusion that needs one environment from each of two independent conclusions."""
    return minimize((x | y for x in a for y in b), budget)


def _merge(into: dict[World, set[Env]], world: World, envs: Iterable[Env]) -> None:
    into.setdefault(world, set()).update(envs)


def _finish(d: Mapping[World, set[Env]], budget: EnvBudget) -> Dist:
    return {w: minimize(es, budget) for w, es in d.items()}


# --------------------------------------------------------------------------- base keys


def run_members(true_ev: Sequence[Ev], tl: Timeline) -> dict[Run, frozenset[str]]:
    """The TRUE reports that make up each run of a timeline: a report belongs to the run of its value with the
    greatest ``start`` (first anchor) at or before its own anchor (runs of one value are consecutive blocks)."""
    by_value: dict[Value, list[Run]] = {}
    for r in tl:
        by_value.setdefault(r.value, []).append(r)
    for rs in by_value.values():
        rs.sort(key=lambda r: r.start)
    members: dict[Run, set[str]] = {r: set() for r in tl}
    for e in true_ev:
        owner: Run | None = None
        for r in by_value.get(e.value, ()):
            if r.start <= e.anchor:
                owner = r
        if owner is not None:
            members[owner].add(e.id)
    return {r: frozenset(m) for r, m in members.items()}


def base_world_envs(spec: AttrSpec, evidence: Sequence[Ev], interps: Iterable[Interp], t: int, budget: EnvBudget) -> Dist:
    """Candidate world -> its subset-minimal environments, at valid day ``t``, for one observed key.

    Per interpretation, the environment of a world ``S`` is the set of TRUE reports in the *runs that
    contribute ``S`` at* ``t`` (the runs of a value in ``S`` that cover ``t``, definitely or at an unknown
    change point). It is therefore per interval: a report that only supports the same value in another stretch
    of valid time is not in this segment's environments. The keys of the result are exactly the candidate
    family at ``t`` (same computation as ``Justification.candidates_at``); the empty world has the empty
    environment (no positive evidence)."""
    acc: dict[World, set[Env]] = {}
    for err, tl in interps:
        members = run_members([e for e in evidence if e.id not in err], tl)
        for w in observed_candidates(tl, spec, t):
            env: set[str] = set()
            for r in tl:
                if r.value in w and r.covers(t) is not False:
                    env |= members[r]
            _merge(acc, w, [frozenset(env)])
    return _finish(acc, budget)


def oracle_flat_ids(evidence: Sequence[Ev], family: Iterable[World]) -> frozenset[str]:
    """Profile ``revise-stream-v1``, observed key: admitted assertions whose value is a candidate value."""
    wanted: set[Value] = set()
    for w in family:
        wanted |= w
    return frozenset(e.id for e in evidence if e.value in wanted)


# --------------------------------------------------------------------------- derived keys


class SupportProvider(Protocol):
    """A base-evidence provider that can also explain: candidates, breakpoints, per-world environments, and
    the admitted ``(report id, value)`` pairs of a key (for the oracle's flat rule)."""

    def candidates(self, key: Key, t: int) -> frozenset[World]: ...

    def breakpoints(self, key: Key) -> frozenset[int]: ...

    def world_envs(self, key: Key, t: int, budget: EnvBudget) -> Dist: ...

    def reports(self, key: Key) -> Sequence[tuple[str, Value]]: ...


def _is_var(x: object) -> bool:
    return isinstance(x, str) and x.startswith("?")


@dataclass
class EnvEngine:
    """The rule engine of ``derive._Engine`` carrying environments with every candidate world.

    ``depth`` limits the derivation levels explained (``None`` = full closure): the queried derived key is
    level 1, the keys its rules read are level 2, and so on. A base key read beyond ``depth`` contributes its
    worlds with the empty environment and flags the result *cut*; the environments are then partial and the
    explanation is ``truncated``."""

    schema: KernelSchema
    provider: SupportProvider
    budget: EnvBudget
    depth: int | None = None
    cut: bool = False
    memo: dict[tuple[str, str, int, int], Dist] = field(default_factory=dict)

    def dist(self, entity: str, attr: str, t: int, level: int = 1) -> Dist:
        spec = self.schema.spec(attr)
        if not spec.derived:
            d = self.provider.world_envs(Key(entity=entity, attr=attr), t, self.budget)
            if self.depth is not None and level > self.depth:
                self.cut = True
                self.budget.truncated = True
                return {w: _E0 for w in d}
            return d
        ck = (entity, attr, t, level)
        hit = self.memo.get(ck)
        if hit is not None:
            return hit
        if level > MAX_LEVEL:
            raise RecursionError("rule recursion too deep")
        out = self._derive(entity, attr, t, level)
        self.memo[ck] = out
        return out

    def _derive(self, e: str, a: str, t: int, level: int) -> Dist:
        worlds: Dist = {frozenset(): _E0}
        rules = self.schema.rules_for(a)
        if not rules:
            return worlds
        for r in rules:
            rw = self._eval_rule(r, e, t, level)
            acc: dict[World, set[Env]] = {}
            for w1, e1 in worlds.items():
                for w2, e2 in rw.items():
                    _merge(acc, w1 | w2, join(e1, e2, self.budget))
            worlds = _finish(acc, self.budget)
        return worlds

    def _eval_rule(self, r: RuleSpec, e: str, t: int, level: int) -> Dist:
        _head_attr, head_e, head_v_raw = r.head
        head_v = str(head_v_raw)
        entities = self.schema.entities
        bud = self.budget

        def rec(lits: tuple[tuple[str, str, Value], ...], b: dict[str, Value]) -> Dist:
            if not lits:
                v = b.get(head_v)
                if v is None:
                    return {frozenset(): _E0}
                outs: Dist = {frozenset([v]): _E0}
                for xa, xe, xv in r.exceptions:
                    ent = str(b.get(xe, xe))
                    exc = self.dist(ent, xa, t, level + 1)
                    acc: dict[World, set[Env]] = {}
                    for w, ew in outs.items():
                        for s, es in exc.items():
                            _merge(acc, frozenset() if xv in s else w, join(ew, es, bud))
                    outs = _finish(acc, bud)
                return outs
            (la, lx, ly), rest = lits[0], lits[1:]
            xs: list[str] = [str(b[lx])] if lx in b else (list(entities) if _is_var(lx) else [lx])
            worlds_out: dict[World, set[Env]] = {}
            for x in xs:
                b1: dict[str, Value] = {**b, lx: x}
                for s, es_lit in self.dist(x, la, t, level + 1).items():
                    if _is_var(ly):
                        lyv = str(ly)
                        ys = [y for y in s if (lyv not in b1 or b1[lyv] == y)]
                    else:
                        ys = [ly] if ly in s else []
                    if not ys:
                        _merge(worlds_out, frozenset(), es_lit)
                        continue
                    combo: Dist = {frozenset(): _E0}
                    for y in ys:
                        sub = rec(rest, {**b1, str(ly): y})
                        acc2: dict[World, set[Env]] = {}
                        for c, ec in combo.items():
                            for s2, es2 in sub.items():
                                _merge(acc2, c | s2, join(ec, es2, bud))
                        combo = _finish(acc2, bud)
                    for w, ec in combo.items():
                        _merge(worlds_out, w, join(es_lit, ec, bud))
            return _finish(worlds_out, bud)

        return rec(r.body, {head_e: e})


# --------------------------------------------------------------------------- oracle flat set, derived keys


Binding = tuple[Key, Value, bool]  # (base key, value, read through an exception literal)
TraceEntry = tuple[Key, Value, frozenset[Binding]]


@dataclass
class OracleTrace:
    """A port of the study's ``timeline.candidates_at(..., trace=...)``: every derivation path that yields a
    head value ``v`` records ``(head key, v, bindings used along the path, exceptions included)``."""

    schema: KernelSchema
    provider: SupportProvider

    def candidates(self, entity: str, attr: str, t: int, depth: int, trace: list[TraceEntry] | None) -> frozenset[World]:
        spec = self.schema.spec(attr)
        key = Key(entity=entity, attr=attr)
        if spec.derived:
            return self._derive(entity, attr, t, depth, trace)
        cands = self.provider.candidates(key, t)
        if trace is not None:
            for s in cands:
                for v in s:
                    trace.append((key, v, frozenset()))
        return cands

    def _derive(self, e: str, a: str, t: int, depth: int, trace: list[TraceEntry] | None) -> frozenset[World]:
        if depth > 8:
            raise RecursionError("rule recursion too deep")
        worlds: set[World] = {frozenset()}
        rules = self.schema.rules_for(a)
        if not rules:
            return frozenset(worlds)
        for r in rules:
            rw = self._eval_rule(r, e, t, depth, trace)
            worlds = {w1 | w2 for w1 in worlds for w2 in rw}
        return frozenset(worlds)

    def _eval_rule(self, r: RuleSpec, e: str, t: int, depth: int, trace: list[TraceEntry] | None) -> set[World]:
        head_attr, head_e, head_v_raw = r.head
        head_v = str(head_v_raw)
        entities = self.schema.entities

        def rec(lits: tuple[tuple[str, str, Value], ...], b: dict[str, Value], used: frozenset[Binding]) -> set[World]:
            if not lits:
                v = b.get(head_v)
                if v is None:
                    return {frozenset()}
                outs: set[World] = {frozenset([v])}
                exc_used: set[Binding] = set()
                for xa, xe, xv in r.exceptions:
                    ent = str(b.get(xe, xe))
                    exc = self.candidates(ent, xa, t, depth + 1, None)
                    new: set[World] = set()
                    for w in outs:
                        for s in exc:
                            new.add(frozenset() if xv in s else w)
                            for y in s:
                                exc_used.add((Key(entity=ent, attr=xa), y, True))
                    outs = new
                if trace is not None:
                    for w in outs:
                        for hv in w:
                            trace.append((Key(entity=e, attr=head_attr), hv, used | frozenset(exc_used)))
                return outs
            (la, lx, ly), rest = lits[0], lits[1:]
            xs: list[str] = [str(b[lx])] if lx in b else (list(entities) if _is_var(lx) else [lx])
            worlds_out: set[World] = set()
            for x in xs:
                b1: dict[str, Value] = {**b, lx: x}
                sub_trace: list[TraceEntry] | None = [] if trace is not None else None
                for s in self.candidates(x, la, t, depth + 1, sub_trace):
                    if _is_var(ly):
                        lyv = str(ly)
                        ys = [y for y in s if (lyv not in b1 or b1[lyv] == y)]
                    else:
                        ys = [ly] if ly in s else []
                    if not ys:
                        worlds_out.add(frozenset())
                        continue
                    combo: set[World] = {frozenset()}
                    for y in ys:
                        used1 = used | {(Key(entity=x, attr=la), y, False)}
                        if sub_trace:
                            for k2, v2, u2 in sub_trace:
                                if k2 == Key(entity=x, attr=la) and v2 == y:
                                    used1 = used1 | u2
                        sub = rec(rest, {**b1, str(ly): y}, used1)
                        combo = {c | s2 for c in combo for s2 in sub}
                    worlds_out |= combo
            return worlds_out

        return rec(r.body, {head_e: e}, frozenset())


def oracle_flat_parts_derived(
    schema: KernelSchema, provider: SupportProvider, key: Key, t: int, family: Iterable[World]
) -> tuple[frozenset[str], frozenset[str]]:
    """The oracle's flat set for a derived key, split as ``(all ids, ids reached only through exception
    literals)``. The second part is diagnostic: the oracle accumulates the bindings of *every* candidate world
    of an exception key, including the worlds that block the rule, into every derivation it records."""
    wanted: set[Value] = set()
    for w in family:
        wanted |= w
    trace: list[TraceEntry] = []
    OracleTrace(schema, provider).candidates(key.entity, key.attr, t, 0, trace)
    pairs: set[Binding] = set()
    for k, v, used in trace:
        if k == key and v in wanted:
            pairs |= used
    body = {(bk, bv) for bk, bv, exc in pairs if not exc}
    exc_only = {(bk, bv) for bk, bv, exc in pairs if exc} - body

    def ids_of(pp: set[tuple[Key, Value]]) -> frozenset[str]:
        out: set[str] = set()
        for bk, bv in pp:
            if schema.spec(bk.attr).derived:  # a derived binding names no admitted report (only base keys have any)
                continue
            out |= {rid for rid, rv in provider.reports(bk) if rv == bv}
        return frozenset(out)

    return ids_of(body | exc_only), ids_of(exc_only)


def oracle_flat_ids_derived(
    schema: KernelSchema, provider: SupportProvider, key: Key, t: int, family: Iterable[World]
) -> frozenset[str]:
    """Profile ``revise-stream-v1``, derived key: admitted assertions of every ``(key, value)`` binding on a
    derivation path yielding a candidate value (``gold.support_ids``)."""
    return oracle_flat_parts_derived(schema, provider, key, t, family)[0]


def flatten(environments: Iterable[Iterable[str]]) -> frozenset[str]:
    """The adapter projection of S-12: the union of the report ids of the environments."""
    out: set[str] = set()
    for e in environments:
        out |= set(e)
    return frozenset(out)
