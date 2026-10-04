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

from collections import deque
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime

from palimem.admission import EVIDENCE_CUES, Admitter, Evaluation
from palimem.engine.logview import ViewLog
from palimem.kernel import (
    DerivedJustification,
    Justification,
    KernelSchema,
    ResourceLimitedResult,
    base_attrs_closure,
    check_schema,
    day_of,
    justify_derived,
    justify_key,
)
from palimem.kernel.justify import Family, segment_at
from palimem.store import AdmissionContext, InputKind, RevisionContext, StoreView
from palimem.types import (
    AdmissionOutcome,
    AdmissionRecord,
    Belief,
    BeliefOfProp,
    Dependency,
    EmptyForm,
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
    ValueForm,
    Versions,
)
from palimem.types import Segment as PSegment
from palimem.types._codec import Value
from palimem.types.limits import DEFAULT_ENVIRONMENT_BUDGET

ChangeFrom = Callable[[Report], Value | None]


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


def _ids(entries: Sequence[LogEntry] | None) -> tuple[str, ...]:
    return tuple(e.report.id or "" for e in (entries or ()))


def family_of_segment(seg: PSegment) -> Family:
    """The candidate family a stored segment stands for (the inverse of ``classify``): what a derived key needs
    from a stored base belief."""
    st = seg.kernel_status

    def one(f: ValueForm | SetForm | EmptyForm | object) -> frozenset[Value]:
        if isinstance(f, ValueForm):
            return frozenset([f.value])
        if isinstance(f, SetForm):
            return frozenset(f.values)
        return frozenset()

    if st is KernelStatus.ESTABLISHED:
        assert seg.established is not None
        return frozenset({one(seg.established.form)})
    if st is KernelStatus.UNRESOLVED:
        return frozenset(one(c.form) for c in seg.alternatives)
    return frozenset({frozenset()})  # unknown / established_empty: the empty world


class _Incomplete(Exception):
    """A base belief a derived key needs is incomplete (e.g. over the environment budget)."""

    def __init__(self, key: Key) -> None:
        super().__init__(f"base key {key.entity}/{key.attr} is incomplete")
        self.key = key


class _BeliefProvider:
    """Kernel ``Provider`` over stored base beliefs. Records the keys whose candidates were actually read: those
    (and only those) become the derived belief's ``depends_on``."""

    def __init__(self, resolve: Callable[[Key], Belief | None]) -> None:
        self._resolve = resolve
        self.consulted: dict[Key, Belief | None] = {}

    def candidates(self, key: Key, t: int) -> Family:
        b = self._resolve(key)
        self.consulted[key] = b
        if b is None:
            return frozenset({frozenset()})
        if not b.inference.complete:
            raise _Incomplete(key)
        return family_of_segment(segment_at(b.segments, t))

    def breakpoints(self, key: Key) -> frozenset[int]:
        b = self._resolve(key)
        if b is None or not b.inference.complete:
            return frozenset()  # an irrelevant incomplete key must not poison unrelated derived keys
        pts: set[int] = set()
        for s in b.segments:
            if s.valid_from is not None:
                pts.add(day_of(s.valid_from))
            if s.valid_to is not None:
                pts.add(day_of(s.valid_to))
        return frozenset(pts)


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
    ) -> None:
        check_schema(kernel_schema)  # static exactness: refuse a schema the per-key kernel cannot justify exactly
        self.schema = schema
        self._kernel_schema = kernel_schema
        self.semantic = semantic
        self.admitter = admitter
        self.entities = None if entities is None else tuple(entities)
        self.budget = budget
        self.change_from_of = change_from_of
        self._log: ViewLog | None = None
        self._evals: dict[tuple[int, str | None, int], Evaluation] = {}

    # -- binding and caches

    def bind(self, view: StoreView) -> None:
        self._log = ViewLog(view)
        self._evals.clear()

    @property
    def log(self) -> ViewLog:
        assert self._log is not None, "Pipeline.bind(view) has not been called"
        return self._log

    def invalidate(self) -> None:
        """The log changed under the caches (an erasure): forget decoded entries and evaluations."""
        self.log.invalidate()
        self._evals.clear()

    def set_admitter(self, admitter: Admitter) -> None:
        self.admitter = admitter
        self._evals.clear()

    def evaluate(self, upto_lsn: int) -> Evaluation:
        """Admission over the log prefix ``<= upto_lsn`` under the current admission config (cached)."""
        entries = self.log.entries(upto_lsn=upto_lsn)
        last = entries[-1].report.id if entries else None
        ck = (upto_lsn, last, self.admitter.config.admission_version)
        hit = self._evals.get(ck)
        if hit is None:
            if len(self._evals) > 512:
                self._evals.clear()
            hit = self.admitter.evaluate(self.log, as_of_lsn=upto_lsn)
            self._evals[ck] = hit
        return hit

    def kernel_schema(self, ev: Evaluation) -> KernelSchema:
        """The kernel schema with the entity universe: configured, else the entities that appear in the log."""
        if self.entities is not None:
            ents = self.entities
        else:
            ents = tuple(sorted({e.report.key.entity for e in ev.entries}))
        ks = self._kernel_schema
        if tuple(ks.entities) == ents:
            return ks
        return KernelSchema(attrs=ks.attrs, rules=ks.rules, entities=ents)

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
        inputs: Mapping[str, int], recorded_at: datetime,
    ) -> Belief:
        j = self.justify_base(ks, key, entries)
        av = self.admitter.config.admission_version
        if isinstance(j, ResourceLimitedResult):
            return Belief(
                key=key, version=version, lsn=lsn, required_generation=generation,
                completed_generation=max(generation - 1, 0), segments=(),
                pinned=tuple(Pin(report_id=e.report.id or "", admission_version=av) for e in entries),
                depends_on=(), invalidated_by=None, versions=self._versions(inputs),
                inference=Inference(complete=False, reason=f"{j.reason.value}: {j.detail}"), recorded_at=recorded_at,
            )
        return Belief(
            key=key, version=version, lsn=lsn, required_generation=generation, completed_generation=generation,
            segments=j.segments(), pinned=tuple(Pin(report_id=i, admission_version=av) for i in j.admitted_ids),
            depends_on=(), invalidated_by=None, versions=self._versions(inputs),
            inference=Inference(complete=True), recorded_at=recorded_at,
        )

    def derived_belief(
        self, ks: KernelSchema, key: Key, resolve: Callable[[Key], Belief | None], *, version: int, lsn: int,
        generation: int, inputs: Mapping[str, int], recorded_at: datetime,
    ) -> Belief:
        prov = _BeliefProvider(resolve)
        dj: DerivedJustification = justify_derived(ks, key, prov, self.semantic)
        def deps_of() -> tuple[Dependency, ...]:
            return tuple(
                Dependency(key=k, version=b.version)
                for k, b in sorted(prov.consulted.items(), key=lambda kv: (kv[0].entity, kv[0].attr))
                if b is not None and k != key
            )

        try:
            segments = dj.segments()
        except _Incomplete as inc:
            return Belief(
                key=key, version=version, lsn=lsn, required_generation=generation,
                completed_generation=max(generation - 1, 0), segments=(), pinned=(), depends_on=deps_of(),
                invalidated_by=None, versions=self._versions(inputs),
                inference=Inference(complete=False, reason=f"stale_dependency: {inc}"), recorded_at=recorded_at,
            )
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
        ev = p.evaluate(lsn)
        records: list[AdmissionRecord] = [ev.decisions[rid].record]
        if lsn > 1:
            prev = p.evaluate(lsn - 1)
            for e in prev.entries:
                r2 = e.report.id
                assert r2 is not None
                a, b = prev.decisions[r2].record, ev.decisions[r2].record
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
        ev = p.evaluate(lsn)
        now = direct_entries(ev)
        prev = direct_entries(p.evaluate(lsn - 1)) if lsn > 1 else {}
        ks = p.kernel_schema(ev)
        touched = ctx.entry.report.key
        changed = {k for k in now.keys() | prev.keys() if _ids(now.get(k)) != _ids(prev.get(k))}
        changed.add(touched)
        base_changed = sorted((k for k in changed if not ks.spec(k.attr).derived), key=lambda k: (k.entity, k.attr))

        def next_version(k: Key) -> int:
            cur = view.current_belief(k)
            return (cur.version if cur is not None else 0) + 1

        overlay: dict[Key, Belief] = {}
        for k in base_changed:
            overlay[k] = p.base_belief(
                ks, k, now.get(k, []), version=next_version(k), lsn=lsn, generation=ctx.generation,
                inputs=ctx.inputs, recorded_at=ctx.entry.recorded_at,
            )
        out: list[Belief] = list(overlay.values())

        changed_attrs = {k.attr for k in base_changed}
        derived_attrs = [a for a, s in ks.attrs.items() if s.derived]
        affected = [a for a in derived_attrs if base_attrs_closure(ks, a) & changed_attrs]
        if affected:
            marked = store_closure(view, touched)

            def resolve(k: Key) -> Belief | None:
                return overlay.get(k) or view.current_belief(k)

            for a in sorted(affected):
                for e in ks.entities:
                    dk = Key(entity=e, attr=a)
                    cur = view.current_belief(dk)
                    new = p.derived_belief(
                        ks, dk, resolve, version=(cur.version if cur is not None else 0) + 1, lsn=lsn,
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
        return out

    def recompute(self, key: Key, view: StoreView) -> Belief | None:
        """Rebuild one key from the log alone, as of the head (verify, completion jobs, erasure repair)."""
        p = self.p
        head = view.head()
        lsn = max(head.lsn, 1)
        ev = p.evaluate(head.lsn)
        ks = p.kernel_schema(ev)
        cur = view.current_belief(key)
        version = (cur.version if cur is not None else 0) + 1
        recorded_at = ev.entries[-1].recorded_at if ev.entries else datetime.now(UTC)
        inputs = {k: v for k, v in _inputs_of(view).items()}
        gen = max(head.generation, 0)
        if ks.spec(key.attr).derived:
            return p.derived_belief(
                ks, key, view.current_belief, version=version, lsn=lsn, generation=gen, inputs=inputs,
                recorded_at=recorded_at,
            )
        return p.base_belief(
            ks, key, direct_entries(ev).get(key, []), version=version, lsn=lsn, generation=gen, inputs=inputs,
            recorded_at=recorded_at,
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
