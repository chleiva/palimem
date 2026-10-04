"""Trivial stand-ins for the injected stages (the real admission and kernel are Lanes D and B).

``FakeAdmitter`` admits everything. ``FakeReviser`` builds a one-segment belief per key from the live
entries on it (a withdraw cue removes its target), and a derived belief for every attribute that reads
the key (``view.attr_dependents``), so dependency lookup and cascades are exercised without a kernel.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from palimem.store import AdmissionContext, RevisionContext, StoreView
from palimem.types import (
    AdmissionOutcome,
    AdmissionReason,
    AdmissionRecord,
    Belief,
    Candidate,
    Cue,
    Dependency,
    Inference,
    KernelStatus,
    Key,
    Origin,
    Pin,
    Report,
    Segment,
    Source,
    Support,
    ValueForm,
    ValueProp,
    Versions,
)

DERIVED_PREFIX = "derived:"


class FakeAdmitter:
    def __init__(self, admission_version: int = 1) -> None:
        self.admission_version = admission_version
        self.calls = 0

    def admit(self, ctx: AdmissionContext) -> Sequence[AdmissionRecord]:
        self.calls += 1
        assert ctx.entry.report.id is not None
        return (
            AdmissionRecord(
                id=ctx.new_id(),
                report_id=ctx.entry.report.id,
                outcome=AdmissionOutcome.ADMISSIBLE,
                reason=AdmissionReason.ADMITTED,
                admission_version=self.admission_version,
            ),
        )


def _live_values(view: StoreView, key: Key) -> dict[object, list[str]]:
    entries = view.entries_for_key(key)
    withdrawn = {e.report.target for e in entries if e.report.cue is Cue.WITHDRAW}
    out: dict[object, list[str]] = {}
    for e in entries:
        r = e.report
        if r.cue in (Cue.WITHDRAW, Cue.DISPUTE, Cue.ALLEGE) or r.id in withdrawn:
            continue
        if isinstance(r.proposition, ValueProp):
            assert r.id is not None
            out.setdefault(r.proposition.value, []).append(r.id)
    return out


def _segment(key: Key, values: dict[object, list[str]]) -> tuple[Segment, tuple[str, ...]]:
    pins = tuple(sorted(i for ids in values.values() for i in ids))
    cands = {v: Candidate(key=key, form=ValueForm(value=v)) for v in values}  # type: ignore[arg-type]
    support = {cands[v].id: tuple(Support(environment=(i,)) for i in ids) for v, ids in values.items()}
    if not values:
        return Segment(valid_from=None, valid_to=None, kernel_status=KernelStatus.UNKNOWN), pins
    if len(values) == 1:
        (c,) = cands.values()
        return Segment(valid_from=None, valid_to=None, kernel_status=KernelStatus.ESTABLISHED, established=c, support=support), pins
    ordered = tuple(sorted(cands.values(), key=lambda c: c.id))
    return Segment(valid_from=None, valid_to=None, kernel_status=KernelStatus.UNRESOLVED, alternatives=ordered, support=support), pins


class FakeReviser:
    """``corrupt`` (test hook): a callable applied to the touched key's belief before it is returned."""

    def __init__(self) -> None:
        self.calls = 0

    def _base(self, view: StoreView, key: Key, version: int, lsn: int, generation: int, at: datetime) -> Belief:
        seg, pins = _segment(key, _live_values(view, key))
        return Belief(
            key=key, version=version, lsn=lsn, required_generation=generation, completed_generation=generation,
            segments=(seg,), pinned=tuple(Pin(report_id=i, admission_version=1) for i in pins), depends_on=(),
            invalidated_by=None, versions=Versions(schema=1, semantic=1, admission=1), inference=Inference(complete=True),
            recorded_at=at,
        )

    def _derived(self, view: StoreView, base: Belief, dkey: Key, version: int, lsn: int, generation: int, at: datetime) -> Belief:
        seg = base.segments[0]
        if seg.established is not None and isinstance(seg.established.form, ValueForm):
            c = Candidate(key=dkey, form=ValueForm(value=DERIVED_PREFIX + str(seg.established.form.value)))
            sup = {c.id: tuple(Support(environment=s.environment) for s in seg.support[seg.established.id])}
            dseg = Segment(valid_from=None, valid_to=None, kernel_status=KernelStatus.ESTABLISHED, established=c, support=sup)
        else:
            dseg = Segment(valid_from=None, valid_to=None, kernel_status=KernelStatus.UNKNOWN)
        return Belief(
            key=dkey, version=version, lsn=lsn, required_generation=generation, completed_generation=generation,
            segments=(dseg,), pinned=base.pinned, depends_on=(Dependency(key=base.key, version=base.version),),
            invalidated_by=None, versions=Versions(schema=1, semantic=1, admission=1), inference=Inference(complete=True),
            recorded_at=at,
        )

    def revise(self, ctx: RevisionContext) -> Sequence[Belief]:
        self.calls += 1
        view, e = ctx.view, ctx.entry
        key = e.report.key
        cur = view.current_belief(key)
        base = self._base(view, key, (cur.version if cur else 0) + 1, e.lsn, ctx.generation, e.recorded_at)
        out = [base]
        for dep_attr in view.attr_dependents(key.attr):
            dk = Key(entity=key.entity, attr=dep_attr)
            dcur = view.current_belief(dk)
            out.append(self._derived(view, base, dk, (dcur.version if dcur else 0) + 1, e.lsn, ctx.generation, e.recorded_at))
        return out

    def recompute(self, key: Key, view: StoreView) -> Belief | None:
        stored = view.current_belief(key)
        if stored is None:
            return None
        schema = view.schema()
        derived_from = None
        if schema is not None:
            for a in schema.attrs:
                if a.name == key.attr and a.rule is not None:
                    derived_from = a.rule.reads[0]
        if derived_from is not None:
            bkey = Key(entity=key.entity, attr=derived_from)
            bcur = view.current_belief(bkey)
            if bcur is None:
                return None
            base = self._base(view, bkey, bcur.version, stored.lsn, stored.required_generation, stored.recorded_at)
            return self._derived(view, base, key, stored.version, stored.lsn, stored.required_generation, stored.recorded_at)
        return self._base(view, key, stored.version, stored.lsn, stored.required_generation, stored.recorded_at)


def make_report(
    entity: str = "alice",
    attr: str = "employer",
    value: object = "acme",
    *,
    cue: Cue = Cue.ASSERT,
    target: str | None = None,
    source: str = "registry",
    origin_group: str | None = None,
    actor: str | None = None,
    origin: Origin = Origin.EXTERNAL_OBSERVATION,
    raw_ref: str | None = None,
) -> Report:
    prop = None if cue is Cue.WITHDRAW else ValueProp(value=value)  # type: ignore[arg-type]
    return Report(
        key=Key(entity=entity, attr=attr),
        cue=cue,
        target=target,
        proposition=prop,
        source=Source(id=source, cls="trusted"),
        origin=origin,
        origin_group=origin_group or f"g-{source}",
        actor=actor or f"connector:{source}",
        raw_ref=raw_ref,
    )
