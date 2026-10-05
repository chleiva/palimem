"""The pipeline between the store and the two decision stages (Lane M): ``log -> admission -> kernel -> beliefs``.

``Pipeline`` is the shared configuration and cache; ``StoreAdmitter`` and ``KernelReviser`` implement the store's two
injected protocols (:class:`palimem.store.Admitter`, :class:`palimem.store.Reviser`) on top of
:mod:`palimem.admission` and :mod:`palimem.kernel`.

How a revision works (docs/PIPELINE.md):

1. Admission is a pure function of (log prefix, admission config). Evaluating it at ``lsn - 1`` and at ``lsn`` gives
   the *evidence sets* of every key before and after the append; a key whose admitted evidence changed is **touched**
   (this one rule covers a plain assert, a withdrawal, a correction, a confirmation lifting a quarantine and a
   compat source-level retraction alike).
2. Each touched base key is justified by the kernel from its admitted entries and stored as a new belief version.
3. Each derived key whose rule closure reads a touched attribute is rebuilt **from the stored base beliefs**
   (segments -> candidate families), the new versions of this append overlaid, and pins exactly the base versions
   it consumed. A derived version is written when its content changed or when the store's own dependency closure
   will mark it (an unreturned marked key would be stale).
"""

from __future__ import annotations

import os
from collections import Counter, OrderedDict, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from palimem.admission import (
    EVIDENCE_CUES,
    AdmissionDelta,
    Admitter,
    Attribution,
    Evaluation,
    IncrementalAdmission,
    supports_incremental,
)
from palimem.engine.dependents import DependentsPlans, ValueIndex, resolve
from palimem.engine.logview import ViewLog
from palimem.kernel import (
    AttrSpec,
    DerivedJustification,
    Justification,
    KernelSchema,
    KernelUnsupported,
    ResourceLimitedResult,
    base_attrs_closure,
    check_schema,
    day_of,
    justify_derived,
    justify_key,
)
from palimem.kernel.derive import MAX_DEPTH as MAX_RULE_DEPTH
from palimem.kernel.justify import Family, segment_at
from palimem.kernel.provenance import Dist, EnvBudget, Envs, World, minimize
from palimem.store import AdmissionContext, InputKind, RevisionContext, StoreView
from palimem.types import (
    AdmissionOutcome,
    AdmissionRecord,
    Belief,
    BeliefOfForm,
    BeliefOfProp,
    Candidate,
    Dependency,
    Inference,
    KernelStatus,
    Key,
    LogEntry,
    Origin,
    Pin,
    Report,
    Schema,
    SemanticConfig,
    SetForm,
    Support,
    ValueForm,
    Versions,
)
from palimem.types import Segment as PSegment
from palimem.types._codec import Value
from palimem.types.limits import DEFAULT_ENVIRONMENT_BUDGET

if TYPE_CHECKING:
    from palimem.entities.layer import EntityLayer

ChangeFrom = Callable[[Report], Value | None]

ADMISSION_MODES = ("incremental", "whole-log", "crosscheck")

EVAL_CACHE_SIZE = 4
"""Admission evaluations kept (an append reads the one at ``lsn`` and the one at ``lsn - 1``; the rest serve the
historical queries that re-evaluate). Each holds a decision per log entry, so the cache must stay small: the
first version kept 512, which was ~100-200 KiB per report of heap."""


# --------------------------------------------------------------------------- helpers


def direct_entries(ev: Evaluation) -> dict[Key, list[LogEntry]]:
    """Per key, the entries the kernel may read, in log order: the same set as ``EvidenceSet.direct`` (admissible,
    not withdrawn, external, plain proposition, evidence cue), computed in one pass."""
    out: dict[Key, list[LogEntry]] = {}
    for e in ev.entries:
        r = e.report
        rid = r.id
        assert rid is not None
        if rid in ev.withdrawn:
            continue
        if (
            ev.decisions[rid].record.outcome is AdmissionOutcome.ADMISSIBLE
            and r.cue in EVIDENCE_CUES
            and not isinstance(r.proposition, BeliefOfProp)
            and r.origin is Origin.EXTERNAL_OBSERVATION
        ):
            out.setdefault(r.key, []).append(e)
    return out


def incremental_mismatches(inc: IncrementalAdmission, ev: Evaluation) -> list[str]:
    """Differences between the incremental admission state and a whole-log evaluation of the same prefix: decisions
    (every field), withdrawals (``by`` and ``kind`` included), direct evidence per key, attributions per key, the
    entity universe and the attribution flag. Empty when they agree."""
    problems: list[str] = []
    if inc.head != (ev.entries[-1].lsn if ev.entries else 0):
        problems.append(f"head {inc.head} != {ev.entries[-1].lsn if ev.entries else 0}")
        return problems
    decisions, withdrawn, direct = inc.snapshot()
    if set(decisions) != set(ev.decisions):
        problems.append("different report sets")
        return problems
    for rid, d in ev.decisions.items():
        if decisions[rid] != d:
            problems.append(f"decision of {rid}: {decisions[rid].record.outcome.value}/{decisions[rid].record.reason.value} vs {d.record.outcome.value}/{d.record.reason.value}")
    if dict(ev.withdrawn) != withdrawn:
        problems.append("withdrawn differs")
    want = {k: _ids(v) for k, v in direct_entries(ev).items()}
    if want != direct:
        problems.append("direct evidence differs")
    if inc.entities != tuple(sorted({e.report.key.entity for e in ev.entries})):
        problems.append("entities differ")
    n_belief = sum(isinstance(e.report.proposition, BeliefOfProp) for e in ev.entries)
    if inc.belief_of_count != n_belief:
        problems.append("belief_of count differs")
    if n_belief:
        for k in {e.report.key for e in ev.entries if isinstance(e.report.proposition, BeliefOfProp)}:
            if inc.attributions_of(k) != inc.admitter.evidence_set_of(ev, k).attributions:
                problems.append(f"attributions of {k.entity}/{k.attr} differ")
    return problems


def whole_log_records(prev: Evaluation | None, ev: Evaluation, rid: str) -> list[AdmissionRecord]:
    """The admission records of the append that took ``prev`` to ``ev``: the new report's first, then every earlier
    report whose (outcome, reason, confirmers) changed, in log order. The whole-log definition the incremental
    delta is compared with."""
    out = [ev.decisions[rid].record]
    if prev is not None:
        for e in prev.entries:
            r2 = e.report.id
            assert r2 is not None
            a, b = prev.decisions[r2].record, ev.decisions[r2].record
            if (a.outcome, a.reason, a.confirmed_by) != (b.outcome, b.reason, b.confirmed_by):
                out.append(b)
    return out


def attribution_support(a: Attribution) -> tuple[Support, ...]:
    """The support of an attributed claim: one environment per **origin group** (its earliest report). Independent
    groups each suffice to establish ``belief_of(holder, P)``, and a second report of the same group is only a copy
    (S-11, design: "corroborated by any number of origin groups"). The kernel has no semantics for attributions (they
    never enter the interpretation enumeration), so this is the pipeline's own rule: per-group alternatives, not the
    joint environment the kernel gives agreeing base reports (flagged for the author, see docs/PIPELINE.md)."""
    first: dict[str, str] = {}
    for e in a.entries:
        rid = e.report.id
        if rid is not None:
            first.setdefault(e.report.origin_group, rid)
    return tuple(Support(environment=(rid,)) for _g, rid in sorted(first.items(), key=lambda kv: kv[1]))


def attribution_segments(key: Key, attributions: Sequence[Attribution]) -> tuple[PSegment, ...]:
    """The segment of a key that has attributed claims and no direct evidence: ``belief_of(holder, P)`` is established
    (several different claims: unresolved), and the inner proposition ``P`` is **never** a candidate (T-D5, S-11)."""
    pairs = [
        (Candidate(key=key, form=BeliefOfForm(holder=a.proposition.holder, proposition=a.proposition.proposition)), a)
        for a in attributions
    ]
    pairs.sort(key=lambda p: p[0].id)
    support = {c.id: sup for c, a in pairs if (sup := attribution_support(a))}
    if len(pairs) == 1:
        return (PSegment(
            valid_from=None, valid_to=None, kernel_status=KernelStatus.ESTABLISHED, established=pairs[0][0],
            support=support,
        ),)
    return (PSegment(
        valid_from=None, valid_to=None, kernel_status=KernelStatus.UNRESOLVED, alternatives=tuple(c for c, _ in pairs),
        support=support,
    ),)


def _ids(entries: Sequence[LogEntry] | None) -> tuple[str, ...]:
    return tuple(e.report.id or "" for e in (entries or ()))


def world_of(form: object) -> World:
    """The candidate world (a set of values) a stored candidate form stands for."""
    if isinstance(form, ValueForm):
        return frozenset([form.value])
    if isinstance(form, SetForm):
        return frozenset(form.values)
    return frozenset()


def family_of_segment(seg: PSegment) -> Family:
    """The candidate family a stored segment stands for (the inverse of ``classify``): what a derived key needs
    from a stored base belief."""
    st = seg.kernel_status
    if st is KernelStatus.ESTABLISHED:
        assert seg.established is not None
        return frozenset({world_of(seg.established.form)})
    if st is KernelStatus.UNRESOLVED:
        return frozenset(world_of(c.form) for c in seg.alternatives)
    return frozenset({frozenset()})  # unknown / established_empty: the empty world


def worlds_with_envs(seg: PSegment, budget: EnvBudget) -> Dist:
    """Candidate world -> its subset-minimal environments, read from the **stored** per-candidate supports of a
    segment (S-12). A candidate with no support entry has the empty environment: the empty world (no positive
    evidence) is the only candidate stored that way, because a ``Support`` needs at least one report. The keys equal
    :func:`family_of_segment`, which is what a derived key's rule engine consumes."""
    out: dict[World, Envs] = {}
    st = seg.kernel_status
    if st is KernelStatus.ESTABLISHED:
        assert seg.established is not None
        cands: tuple[Candidate, ...] = (seg.established,)
    elif st is KernelStatus.UNRESOLVED:
        cands = tuple(seg.alternatives)
    else:
        return {frozenset(): frozenset({frozenset()})}
    for c in cands:
        sups = seg.support.get(c.id, ())
        envs = minimize((frozenset(s.environment) for s in sups), budget) if sups else frozenset({frozenset()})
        w = world_of(c.form)
        out[w] = out[w] | envs if w in out else envs
    return out


class _Incomplete(Exception):
    """A base belief a derived key needs is incomplete (e.g. over the environment budget)."""

    def __init__(self, key: Key) -> None:
        super().__init__(f"base key {key.entity}/{key.attr} is incomplete")
        self.key = key


@dataclass(frozen=True, eq=False)
class EvalFacts:
    """Per-evaluation facts every revision and every ``recompute`` needs: computed once per :class:`Evaluation`, not
    once per key (``verify_beliefs`` recomputes every key, and each recomputation used to rescan the whole log)."""

    direct: Mapping[Key, Sequence[LogEntry]]
    has_attributions: bool
    entities: tuple[str, ...]


class Resolver:
    """Per-revision cache over "the belief of a key" (new versions of this append overlaid on the stored current
    ones) and over the breakpoints of each belief. Decoding a stored belief is the expensive step, so every derived
    key of one revision shares one resolver."""

    def __init__(
        self, view: StoreView, overlay: Mapping[Key, Belief] | None = None,
        canon: Callable[[Key], Key] | None = None,
    ) -> None:
        self._view = view
        self._overlay = overlay or {}
        self._canon = canon  # entity layer: a read of a merged entity's key is a read of its representative's
        self._cache: dict[Key, Belief | None] = {}
        self._bps: dict[Key, frozenset[int]] = {}

    def belief(self, key: Key) -> Belief | None:
        if self._canon is not None:
            key = self._canon(key)
        b = self._overlay.get(key)
        if b is not None:
            return b
        if key not in self._cache:
            self._cache[key] = self._view.current_belief(key)
        return self._cache[key]

    def breakpoints(self, key: Key) -> frozenset[int]:
        hit = self._bps.get(key)
        if hit is None:
            b = self.belief(key)
            pts: set[int] = set()
            if b is not None and b.inference.complete:  # an irrelevant incomplete key must not poison derived keys
                for s in b.segments:
                    if s.valid_from is not None:
                        pts.add(day_of(s.valid_from))
                    if s.valid_to is not None:
                        pts.add(day_of(s.valid_to))
            hit = self._bps[key] = frozenset(pts)
        return hit


class _BeliefProvider:
    """Kernel ``Provider`` over stored base beliefs. Records the keys whose candidates were actually read: those
    (and only those) become the derived belief's ``depends_on``.

    It is also a ``SupportProvider`` (``world_envs``): the environments of a derived world are joins of the stored
    supports of the base worlds it consumed, so a derived belief carries per-candidate supports over **base**
    reports without replaying the log. ``reports`` (the admitted ``(id, value)`` pairs the oracle's flat rule needs)
    is deliberately unavailable here: a stored belief does not carry per-report values, and the profile projection is
    computed on the audit path (:meth:`palimem.memory.Memory.justification`)."""

    def __init__(self, resolver: Resolver) -> None:
        self._r = resolver
        self.consulted: dict[Key, Belief | None] = {}

    def _stored(self, key: Key) -> Belief | None:
        b = self._r.belief(key)
        self.consulted[key] = b
        if b is not None and not b.inference.complete:
            raise _Incomplete(key)
        return b

    def candidates(self, key: Key, t: int) -> Family:
        b = self._stored(key)
        if b is None:
            return frozenset({frozenset()})
        return family_of_segment(segment_at(b.segments, t))

    def breakpoints(self, key: Key) -> frozenset[int]:
        return self._r.breakpoints(key)

    def peek_candidates(self, key: Key, t: int) -> Family:
        """The candidates of a stored base key **without** recording a read (the kernel traces which keys a derivation
        can reach with this; only what ``candidates`` returns becomes a dependency). An absent or incomplete key
        looks empty here: whatever a derivation could only reach through it, it cannot reach."""
        b = self._r.belief(key)
        if b is None or not b.inference.complete:
            return frozenset({frozenset()})
        return family_of_segment(segment_at(b.segments, t))

    def world_envs(self, key: Key, t: int, budget: EnvBudget) -> Dist:
        b = self._stored(key)
        if b is None:
            return {frozenset(): frozenset({frozenset()})}
        return worlds_with_envs(segment_at(b.segments, t), budget)

    def reports(self, key: Key) -> Sequence[tuple[str, Value]]:
        raise NotImplementedError(
            "stored beliefs carry no per-report values; the profile's flat provenance is computed on the audit path"
        )


def derivation_depths(ks: KernelSchema) -> dict[str, int]:
    """Derivation depth per attribute: 0 for a base attribute, ``1 + max(depth of the derived attributes its rules
    read)`` for a derived one. Also the order in which derived keys are revised."""
    memo: dict[str, int] = {}

    def depth(a: str, stack: tuple[str, ...]) -> int:
        if a in memo:
            return memo[a]
        if not ks.spec(a).derived:
            return 0
        if a in stack:
            raise KernelUnsupported(f"rule cycle through {a!r}")
        reads = [x for r in ks.rules_for(a) for (x, _e, _v) in (*r.body, *r.exceptions)]
        memo[a] = 1 + max((depth(x, (*stack, a)) for x in reads), default=0)
        return memo[a]

    return {a: depth(a, ()) for a in ks.attrs}


class RuleDepthError(ValueError):
    """A derivation chain is deeper than the kernel's rule-evaluation limit; refused at load (never at query time)."""


def store_closure(view: StoreView, seed: Key) -> set[Key]:
    """Mirror of the store's own dependency closure (no budget): the keys it will mark for this append."""
    seen: set[Key] = set()
    queue: deque[Key] = deque([seed])
    while queue:
        k = queue.popleft()
        if k in seen:
            continue
        seen.add(k)
        queue.extend(view.key_dependents(k))
        queue.extend(Key(entity=k.entity, attr=a) for a in view.attr_dependents(k.attr))
    return seen


# --------------------------------------------------------------------------- the pipeline


class Pipeline:
    """Shared configuration of admission and revision, bound to one store view."""

    def __init__(
        self,
        *,
        schema: Schema,
        kernel_schema: KernelSchema,
        semantic: SemanticConfig,
        admitter: Admitter,
        entities: Sequence[str] | None = None,
        budget: int = DEFAULT_ENVIRONMENT_BUDGET,
        change_from_of: ChangeFrom | None = None,
        revision_budget: int | None = None,
        exhaustive: bool = False,
        admission: str | None = None,
    ) -> None:
        check_schema(kernel_schema)  # static exactness: refuse a schema the per-key kernel cannot justify exactly
        depths = derivation_depths(kernel_schema)
        if max(depths.values(), default=0) > MAX_RULE_DEPTH:
            raise RuleDepthError(
                f"derivation chain of depth {max(depths.values())} exceeds the kernel limit {MAX_RULE_DEPTH}"
            )
        self._depths = depths
        self.schema = schema
        self._kernel_schema = kernel_schema
        self.semantic = semantic
        self.admitter = admitter
        self.entities = None if entities is None else tuple(entities)
        self.budget = budget
        self.change_from_of = change_from_of
        self.revision_budget = revision_budget
        """Maximum number of belief versions one ``revise`` call may return. Keys beyond it (derived keys, after the
        touched ones) stay marked stale and are finished by a completion job (the design's inference budget)."""
        self._log: ViewLog | None = None
        self._evals: OrderedDict[tuple[int, str | None, int], Evaluation] = OrderedDict()
        self._facts: OrderedDict[int, tuple[Evaluation, EvalFacts]] = OrderedDict()
        self.exhaustive = exhaustive
        """Audit mode: re-justify the derived keys of **every** entity on each revision, with the exhaustive
        breakpoint gathering (the first version's behaviour). The default revises only the dependents of the changed
        keys; the tests compare the two."""
        mode = admission if admission is not None else os.environ.get("PALIMEM_ADMISSION", "incremental")
        if mode not in ADMISSION_MODES:
            raise ValueError(f"admission mode {mode!r}: expected one of {ADMISSION_MODES}")
        if exhaustive:
            mode = "whole-log"  # the audit mode is the first version's behaviour end to end
        self.admission_mode = mode
        """``incremental`` (default): admission is updated one append at a time (:class:`IncrementalAdmission`);
        ``whole-log``: the audit path, a full evaluation per append; ``crosscheck``: incremental, compared with the
        whole-log evaluation after every append (tests). Also selectable with ``PALIMEM_ADMISSION``."""
        self._inc: IncrementalAdmission | None = None
        self._delta: AdmissionDelta | None = None
        self._ks_cache: tuple[tuple[str, ...], KernelSchema] | None = None
        self.plans = DependentsPlans.of(kernel_schema)
        self._vi: ValueIndex | None = None
        self._vi_lsn: int | None = None
        self.layer: EntityLayer | None = None
        """Optional entity layer (:mod:`palimem.entities`): merged entities are justified as one. ``None`` = no merges."""
        self.stats: Counter[str] = Counter()
        """Operation counters (never wall-clock): ``revisions``, ``derived_justified``, ``derived_keys_read``,
        ``derived_written``, ``index_builds``. Used by the regression test that an append's work does not grow with the
        entity count."""

    # -- binding and caches

    def bind(self, view: StoreView) -> None:
        self._log = ViewLog(view)
        self.admitter.clear_cache()
        self._evals.clear()
        self._facts.clear()
        self._inc = None
        self._delta = None
        self.drop_index()
        if self.layer is not None:
            self.layer.bind(view)

    def drop_index(self) -> None:
        """Forget the value index: a belief may have changed outside a revision (completion, erasure repair, a
        rolled-back append); it is rebuilt lazily from the store."""
        self._vi = None
        self._vi_lsn = None

    def value_index(self, view: StoreView, ks: KernelSchema, lsn: int) -> ValueIndex | None:
        """The value index as of the store state **before** the revision of ``lsn``: reused only when the previous
        revision (``lsn - 1``) is the one that last updated it, rebuilt from the stored beliefs otherwise."""
        if not self.plans.binders:
            return None
        if self._vi is None or self._vi_lsn != lsn - 1:
            vi = ValueIndex(self.plans.binders)
            for attr in sorted(self.plans.binders):
                for e in ks.entities:
                    k = Key(entity=e, attr=attr)
                    vi.update(k, view.current_belief(k))
            self._vi = vi
            self.stats["index_builds"] += 1
        return self._vi

    @property
    def log(self) -> ViewLog:
        assert self._log is not None, "Pipeline.bind(view) has not been called"
        return self._log

    def invalidate(self) -> None:
        """The log changed under the caches (an erasure): forget decoded entries and evaluations."""
        self.log.invalidate()
        self.admitter.clear_cache()
        self._evals.clear()
        self._facts.clear()
        self._inc = None
        self._delta = None
        self.drop_index()
        if self.layer is not None:
            self.layer.reset()

    def set_admitter(self, admitter: Admitter) -> None:
        self.admitter = admitter
        self._evals.clear()
        self._facts.clear()
        self._inc = None
        self._delta = None
        self.drop_index()
        if self.layer is not None:
            self.layer.reset()

    # -- incremental admission

    @property
    def incremental_enabled(self) -> bool:
        """The incremental path runs unless the audit mode is selected or the admitter overrides whole-log internals
        without providing the overlay hooks (then every append is a whole-log evaluation, as before)."""
        return self.admission_mode != "whole-log" and supports_incremental(self.admitter)

    def incremental_append(self, entry: LogEntry) -> AdmissionDelta:
        """Admission of one appended report by update, not by re-evaluation (the state first settles the previous
        append: committed in the log, or rolled back)."""
        inc = self.synced_incremental(entry.lsn - 1)
        delta = inc.append(entry)
        self._delta = delta
        return delta

    def synced_incremental(self, head_lsn: int) -> IncrementalAdmission:
        """The incremental state brought to the committed log prefix ``<= head_lsn`` (settle, catch up or rebuild)."""
        inc = self._inc
        if inc is None or inc.admitter is not self.admitter:
            inc = self._inc = IncrementalAdmission(self.admitter)
        inc.sync(self.log, before_lsn=head_lsn + 1)
        return inc

    def incremental_state(self) -> IncrementalAdmission:
        assert self._inc is not None, "no incremental admission state (admit has not run)"
        return self._inc

    def take_delta(self, lsn: int) -> AdmissionDelta | None:
        """The delta of the append at ``lsn`` if the incremental path ran for it (consumed once)."""
        d = self._delta
        self._delta = None
        if d is not None and d.lsn == lsn and self.incremental_enabled:
            return d
        return None

    def crosscheck(self, delta: AdmissionDelta) -> None:
        """Compare the incremental state with the whole-log evaluation decision for decision (audit/tests)."""
        inc = self.incremental_state()
        ev = self.admitter.evaluate(self.log, as_of_lsn=delta.lsn)
        problems = incremental_mismatches(inc, ev)
        if problems:
            raise AssertionError("incremental admission differs from the whole-log evaluation: " + "; ".join(problems[:5]))
        prev = self.admitter.evaluate(self.log, as_of_lsn=delta.lsn - 1) if delta.lsn > 1 else None
        expect = whole_log_records(prev, ev, delta.report_id)
        if list(delta.records) != expect:
            raise AssertionError("incremental admission records differ from the whole-log diff")
        direct_ev = direct_entries(ev)
        got = {k for k in direct_ev if _ids(direct_ev[k]) != _ids(inc.direct_of(k))}
        want_changed = {k for k in direct_ev.keys() | inc.direct.keys() if _ids(direct_ev.get(k)) != _ids(inc.direct_of(k))}
        if got or want_changed:
            raise AssertionError("incremental direct evidence differs from the whole-log evaluation")

    def evaluate(self, upto_lsn: int) -> Evaluation:
        """Admission over the log prefix ``<= upto_lsn`` under the current admission config (cached)."""
        entries = self.log.entries(upto_lsn=upto_lsn)
        last = entries[-1].report.id if entries else None
        ck = (upto_lsn, last, self.admitter.config.admission_version)
        hit = self._evals.get(ck)
        if hit is None:
            hit = self.admitter.evaluate(self.log, as_of_lsn=upto_lsn)
            self._evals[ck] = hit
            while len(self._evals) > EVAL_CACHE_SIZE:  # an Evaluation holds a decision per log entry: keep a few
                self._evals.popitem(last=False)
        else:
            self._evals.move_to_end(ck)
        return hit

    def attr_spec(self, attr: str) -> AttrSpec:
        return self._kernel_schema.spec(attr)

    def depth_of(self, attr: str) -> int:
        return self._depths.get(attr, 0)

    def facts(self, ev: Evaluation) -> EvalFacts:
        """The facts of one evaluation (cached; the cache keeps the evaluation alive, so ``id`` cannot be reused)."""
        hit = self._facts.get(id(ev))
        if hit is not None and hit[0] is ev:
            self._facts.move_to_end(id(ev))
            return hit[1]
        f = EvalFacts(
            direct=direct_entries(ev),
            has_attributions=any(isinstance(e.report.proposition, BeliefOfProp) for e in ev.entries),
            entities=tuple(sorted({e.report.key.entity for e in ev.entries})),
        )
        self._facts[id(ev)] = (ev, f)
        while len(self._facts) > EVAL_CACHE_SIZE:
            self._facts.popitem(last=False)
        return f

    def kernel_schema(self, ev: Evaluation) -> KernelSchema:
        """The kernel schema with the entity universe: configured, else the entities that appear in the log."""
        return self.kernel_schema_for(self.facts(ev).entities)

    def kernel_schema_for(self, entities: tuple[str, ...]) -> KernelSchema:
        """The kernel schema for an entity universe (cached by the identity of the entity tuple, so an append that
        introduces no entity neither rebuilds the schema nor compares the universes)."""
        ents = self.entities if self.entities is not None else entities
        hit = self._ks_cache
        if hit is not None and hit[0] is ents:
            return hit[1]
        ks = self._kernel_schema
        out = ks if tuple(ks.entities) == ents else KernelSchema(attrs=ks.attrs, rules=ks.rules, entities=ents)
        self._ks_cache = (ents, out)
        return out

    # -- justification

    def change_from_map(self, entries: Sequence[LogEntry]) -> dict[str, Value]:
        out: dict[str, Value] = {}
        if self.change_from_of is None:
            return out
        for e in entries:
            v = self.change_from_of(e.report)
            if v is not None and e.report.id is not None:
                out[e.report.id] = v
        return out

    def justify_base(self, ks: KernelSchema, key: Key, entries: Sequence[LogEntry]) -> Justification | ResourceLimitedResult:
        return justify_key(ks, key, entries, self.semantic, budget=self.budget, change_from=self.change_from_map(entries))

    # -- belief construction

    def _versions(self, inputs: Mapping[str, int]) -> Versions:
        return Versions(
            schema=inputs.get("schema", 1), semantic=inputs.get("semantic", 1), admission=inputs.get("admission", 1)
        )

    def base_belief(
        self, ks: KernelSchema, key: Key, entries: Sequence[LogEntry], *, version: int, lsn: int, generation: int,
        inputs: Mapping[str, int], recorded_at: datetime, attributions: Sequence[Attribution] = (),
    ) -> Belief:
        j = self.justify_base(ks, key, entries)
        av = self.admitter.config.admission_version
        attributed = tuple(e.report.id or "" for a in attributions for e in a.entries)
        if isinstance(j, ResourceLimitedResult):
            return Belief(
                key=key, version=version, lsn=lsn, required_generation=generation,
                completed_generation=max(generation - 1, 0), segments=(),
                pinned=tuple(Pin(report_id=e.report.id or "", admission_version=av) for e in entries),
                depends_on=(), invalidated_by=None, versions=self._versions(inputs),
                inference=Inference(complete=False, reason=f"{j.reason.value}: {j.detail}"), recorded_at=recorded_at,
            )
        segments = j.segments()
        if not entries and attributions:
            segments = attribution_segments(key, attributions)
        pins = tuple(Pin(report_id=i, admission_version=av) for i in (*j.admitted_ids, *attributed))
        return Belief(
            key=key, version=version, lsn=lsn, required_generation=generation, completed_generation=generation,
            segments=segments, pinned=pins, depends_on=(), invalidated_by=None, versions=self._versions(inputs),
            inference=Inference(complete=True), recorded_at=recorded_at,
        )

    def derived_belief(
        self, ks: KernelSchema, key: Key, resolver: Resolver, *, version: int, lsn: int,
        generation: int, inputs: Mapping[str, int], recorded_at: datetime,
    ) -> Belief:
        prov = _BeliefProvider(resolver)
        dj: DerivedJustification = justify_derived(ks, key, prov, self.semantic, all_entities=self.exhaustive)
        def deps_of() -> tuple[Dependency, ...]:
            read = {b.key: b for b in prov.consulted.values() if b is not None and b.key != key}  # merged entities read one belief
            return tuple(Dependency(key=k, version=b.version) for k, b in sorted(read.items(), key=lambda kv: (kv[0].entity, kv[0].attr)))

        try:
            segments = dj.segments()
        except _Incomplete as inc:
            self.stats["derived_justified"] += 1
            self.stats["derived_keys_read"] += len(prov.consulted)
            return Belief(
                key=key, version=version, lsn=lsn, required_generation=generation,
                completed_generation=max(generation - 1, 0), segments=(), pinned=(), depends_on=deps_of(),
                invalidated_by=None, versions=self._versions(inputs),
                inference=Inference(complete=False, reason=f"stale_dependency: {inc}"), recorded_at=recorded_at,
            )
        self.stats["derived_justified"] += 1
        self.stats["derived_keys_read"] += len(prov.consulted)
        pins: dict[tuple[str, int], Pin] = {}
        for b in prov.consulted.values():
            if b is not None:
                for p in b.pinned:
                    pins[(p.report_id, p.admission_version)] = p
        return Belief(
            key=key, version=version, lsn=lsn, required_generation=generation, completed_generation=generation,
            segments=segments, pinned=tuple(pins[k] for k in sorted(pins)), depends_on=deps_of(),
            invalidated_by=None, versions=self._versions(inputs), inference=Inference(complete=True),
            recorded_at=recorded_at,
        )


# --------------------------------------------------------------------------- store stages


class StoreAdmitter:
    """Adapts :class:`palimem.admission.Admitter` to the store's admission interface.

    The record for the appended report comes first; an earlier report whose decision changed because of this append
    (a confirmation lifting a quarantine, a lapsed confirmation) gets a fresh record, which is how admission history
    stays append-only."""

    def __init__(self, pipeline: Pipeline) -> None:
        self.p = pipeline

    def admit(self, ctx: AdmissionContext) -> Sequence[AdmissionRecord]:
        p, lsn = self.p, ctx.entry.lsn
        rid = ctx.entry.report.id
        assert rid is not None
        if p.incremental_enabled:
            delta = p.incremental_append(ctx.entry)
            if p.admission_mode == "crosscheck":
                p.crosscheck(delta)
            return list(delta.records)
        ev = p.evaluate(lsn)
        records: list[AdmissionRecord] = [ev.decisions[rid].record]
        if lsn > 1:
            prev = p.evaluate(lsn - 1)
            for e in prev.entries:
                r2 = e.report.id
                assert r2 is not None
                da, db = prev.decisions[r2], ev.decisions[r2]
                if da is db:  # the memoised own-merit decision, nothing changed for this report
                    continue
                a, b = da.record, db.record
                if (a.outcome, a.reason, a.confirmed_by) != (b.outcome, b.reason, b.confirmed_by):
                    records.append(b)
        return records


class KernelReviser:
    """Adapts :mod:`palimem.kernel` to the store's ``Reviser`` protocol (see the module docstring)."""

    def __init__(self, pipeline: Pipeline) -> None:
        self.p = pipeline

    def revise(self, ctx: RevisionContext) -> Sequence[Belief]:
        p, view = self.p, ctx.view
        lsn = ctx.entry.lsn
        touched = ctx.entry.report.key
        delta = p.take_delta(lsn)
        entries_of: Callable[[Key], Sequence[LogEntry]]
        attributions_of: Callable[[Key], Sequence[Attribution]]
        if delta is not None:
            # incremental admission already knows which keys' admitted evidence changed and holds each key's
            # direct entries: nothing here scans the log
            inc = p.incremental_state()
            ks = p.kernel_schema_for(inc.entities)
            changed = set(delta.changed_keys)
            entries_of = inc.direct_of
            attributions_of = inc.attributions_of if inc.belief_of_count else (lambda _k: ())
        else:
            ev = p.evaluate(lsn)
            now = p.facts(ev).direct
            prev = p.facts(p.evaluate(lsn - 1)).direct if lsn > 1 else {}
            ks = p.kernel_schema(ev)
            changed = {k for k in now.keys() | prev.keys() if _ids(now.get(k)) != _ids(prev.get(k))}
            has_attr_ev = p.facts(ev).has_attributions

            def entries_of(k: Key) -> Sequence[LogEntry]:
                return now.get(k, [])

            def attributions_of(k: Key) -> Sequence[Attribution]:
                return p.admitter.evidence_set_of(ev, k).attributions if has_attr_ev else ()

        changed.add(touched)
        base_changed = sorted((k for k in changed if not ks.spec(k.attr).derived), key=lambda k: (k.entity, k.attr))

        def next_version(k: Key) -> int:
            cur = view.current_belief(k)
            return (cur.version if cur is not None else 0) + 1

        overlay: dict[Key, Belief] = {}
        for k in base_changed:
            overlay[k] = p.base_belief(
                ks, k, entries_of(k), version=next_version(k), lsn=lsn, generation=ctx.generation,
                inputs=ctx.inputs, recorded_at=ctx.entry.recorded_at,
                attributions=attributions_of(k),
            )
        canon = None
        added: tuple[Key, ...] = ()
        alias_keys: tuple[Key, ...] = ()
        if p.layer is not None:  # entity layer: the representative's aggregated beliefs for merged classes
            res = p.layer.revise_overlay(p, ctx, ks, overlay, entries_of, attributions_of, base_changed, next_version)
            canon, added, alias_keys = res.canon, res.added, res.alias_keys
        out: list[Belief] = list(overlay.values())

        changed_attrs = {k.attr for k in base_changed} | {k.attr for k in added}
        derived_attrs = [a for a, s in ks.attrs.items() if s.derived]
        affected = [a for a in derived_attrs if base_attrs_closure(ks, a) & changed_attrs]
        p.stats["revisions"] += 1
        index: ValueIndex | None = None
        if not p.exhaustive:
            index = p.value_index(view, ks, lsn)
            if index is not None:
                for k, b in overlay.items():
                    index.update(k, b)
        if affected:
            marked = store_closure(view, touched)
            resolver = Resolver(view, overlay, canon)
            order = {e: i for i, e in enumerate(ks.entities)}
            changed_keys: dict[Key, None] = dict.fromkeys(overlay)
            changed_keys.update(dict.fromkeys(alias_keys))  # rules that name an alias value find their dependents too
            for a in sorted(affected, key=lambda x: (p.depth_of(x), x)):  # shallow first: a revision budget keeps these
                heads = None if p.exhaustive else resolve(p.plans, a, changed_keys, index, ks.entities)
                if heads is None:
                    todo = [Key(entity=e, attr=a) for e in ks.entities]
                else:
                    # the dependents the rules and the value index name, plus every key of this attribute the store
                    # itself marks for this append (an unreturned marked key would be stale)
                    names = {e for e in heads if e in order} | {k.entity for k in marked if k.attr == a and k.entity in order}
                    todo = [Key(entity=e, attr=a) for e in sorted(names, key=order.__getitem__)]
                for dk in todo:
                    cur = view.current_belief(dk)
                    new = p.derived_belief(
                        ks, dk, resolver, version=(cur.version if cur is not None else 0) + 1, lsn=lsn,
                        generation=ctx.generation, inputs=ctx.inputs, recorded_at=ctx.entry.recorded_at,
                    )
                    same = (
                        cur is not None
                        and cur.segments == new.segments
                        and cur.pinned == new.pinned
                        and cur.depends_on == new.depends_on
                        and cur.inference == new.inference
                    )
                    if not same or dk in marked:
                        out.append(new)
                        p.stats["derived_written"] += 1
                        if same:
                            p.stats["derived_written_only_because_marked"] += 1
                        changed_keys[dk] = None
                        if index is not None:
                            index.update(dk, new)
        if p.revision_budget is not None and len(out) > max(p.revision_budget, 1):
            out = out[: max(p.revision_budget, 1)]  # touched keys come first; the rest are completed later
            p.drop_index()  # the index saw versions the store will not have until the completion job runs
        elif index is not None:
            p._vi_lsn = lsn  # the index now matches the store as it will be once this revision is stored
        return out

    def recompute(self, key: Key, view: StoreView) -> Belief | None:
        """Rebuild one key from the log alone, as of the head (verify, completion jobs, erasure repair)."""
        p = self.p
        p.drop_index()  # its result becomes a stored belief outside a revision
        head = view.head()
        lsn = max(head.lsn, 1)
        cur = view.current_belief(key)
        version = (cur.version if cur is not None else 0) + 1
        inputs = {k: v for k, v in _inputs_of(view).items()}
        gen = max(head.generation, 0)
        if p.incremental_enabled:
            # the incremental state is brought to the head (usually it already is: completion runs right after the
            # append), so a completion job costs the key it recomputes, not a whole-log evaluation
            inc = p.synced_incremental(head.lsn)
            ks = p.kernel_schema_for(inc.entities)
            recorded_at = inc.entries[-1].recorded_at if inc.entries else datetime.now(UTC)
            if ks.spec(key.attr).derived:
                return p.derived_belief(
                    ks, key, Resolver(view, canon=p.layer.head_canon(view) if p.layer is not None else None),
                    version=version, lsn=lsn, generation=gen, inputs=inputs, recorded_at=recorded_at,
                )
            if p.layer is not None:
                agg = p.layer.recompute_base(
                    p, ks, key, inc.direct_of, inc.attributions_of if inc.belief_of_count else (lambda _k: ()),
                    view=view, version=version, lsn=lsn, generation=gen, inputs=inputs, recorded_at=recorded_at,
                )
                if agg is not None:
                    return agg
            return p.base_belief(
                ks, key, inc.direct_of(key), version=version, lsn=lsn, generation=gen, inputs=inputs,
                recorded_at=recorded_at, attributions=inc.attributions_of(key) if inc.belief_of_count else (),
            )
        ev = p.evaluate(head.lsn)
        ks = p.kernel_schema(ev)
        recorded_at = ev.entries[-1].recorded_at if ev.entries else datetime.now(UTC)
        if ks.spec(key.attr).derived:
            return p.derived_belief(
                ks, key, Resolver(view, canon=p.layer.head_canon(view) if p.layer is not None else None),
                version=version, lsn=lsn, generation=gen, inputs=inputs, recorded_at=recorded_at,
            )
        has_attr = p.facts(ev).has_attributions
        if p.layer is not None:
            direct_now = p.facts(ev).direct
            agg = p.layer.recompute_base(
                p, ks, key, lambda k: direct_now.get(k, []),
                lambda k: p.admitter.evidence_set_of(ev, k).attributions if has_attr else (),
                view=view, version=version, lsn=lsn, generation=gen, inputs=inputs, recorded_at=recorded_at,
            )
            if agg is not None:
                return agg
        return p.base_belief(
            ks, key, p.facts(ev).direct.get(key, []), version=version, lsn=lsn, generation=gen, inputs=inputs,
            recorded_at=recorded_at, attributions=p.admitter.evidence_set_of(ev, key).attributions if has_attr else (),
        )


def _inputs_of(view: StoreView) -> dict[str, int]:
    out: dict[str, int] = {}
    schema = view.schema()
    if schema is not None:
        out["schema"] = schema.version
    for kind in (InputKind.SEMANTIC, InputKind.ADMISSION):
        found = view.input_at(kind)
        if found is not None:
            out[kind.value] = found[0]
    return out
