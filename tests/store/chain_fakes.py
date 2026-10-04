"""Fakes for the generation-barrier, outbox, erasure and export tests (wave 2).

``ChainReviser`` revises a whole derivation chain (``employer -> work_city -> tax_city -> tax_band``) the way the
real kernel will: the touched key and every derived key that reads it, dependencies first. Test hooks:

* ``skip``: attributes the synchronous revision does NOT return (they stay stale until a completion job runs);
* ``incomplete``: attributes returned as incomplete beliefs (the kernel's own budget was exceeded);
* ``recompute_incomplete``: attributes that ``recompute`` cannot finish (a permanently over-budget key).

``recompute`` rebuilds any key from the log alone, for a key with no stored belief as well, which is what
``Engine.complete_pending`` and the erasure repair rely on.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from fakes import _live_values, _segment

from palimem.store import RevisionContext, StoreView
from palimem.types import (
    Attr,
    AttrClass,
    Belief,
    Candidate,
    Dependency,
    Inference,
    KernelStatus,
    Key,
    Pin,
    Rule,
    Schema,
    Segment,
    Support,
    ValueForm,
    ValueType,
    Versions,
)

DERIVED = "derived:"


def chain_schema(version: int = 1, *, band: bool = False) -> Schema:
    def attr(name: str, cls: AttrClass, reads: str | None = None) -> Attr:
        rule = None if reads is None else Rule(reads=(reads,), fn=f"f_{name}")
        return Attr(name=name, attr_class=cls, value_type=ValueType.STRING, inertia=cls is AttrClass.SINGLE_CHANGEABLE, rule=rule)

    attrs = [
        attr("employer", AttrClass.SINGLE_CHANGEABLE),
        attr("work_city", AttrClass.DERIVED, "employer"),
        attr("tax_city", AttrClass.DERIVED, "work_city"),
    ]
    if band:
        attrs.append(attr("tax_band", AttrClass.DERIVED, "tax_city"))
    attrs.append(attr("nickname", AttrClass.SINGLE_STABLE))  # a separate attribute component
    return Schema(version=version, attrs=tuple(attrs))


class ChainReviser:
    def __init__(
        self,
        *,
        skip: Iterable[str] = (),
        incomplete: Iterable[str] = (),
        recompute_incomplete: Iterable[str] = (),
        fail_recompute: bool = False,
    ) -> None:
        self.skip = set(skip)
        self.incomplete = set(incomplete)
        self.recompute_incomplete = set(recompute_incomplete)
        self.fail_recompute = fail_recompute
        self.revise_calls = 0
        self.recomputed: list[Key] = []

    # -- content shared by revise and recompute
    @staticmethod
    def _reads(view: StoreView) -> dict[str, str]:
        schema = view.schema()
        out: dict[str, str] = {}
        if schema is not None:
            for a in schema.attrs:
                if a.rule is not None:
                    out[a.name] = a.rule.reads[0]
        return out

    @staticmethod
    def _content(view: StoreView, key: Key, upstream: Belief | None, derived: bool) -> tuple[Segment, tuple[str, ...], tuple[Dependency, ...]]:
        if not derived:
            seg, pins = _segment(key, _live_values(view, key))
            return seg, pins, ()
        if upstream is None:
            return Segment(valid_from=None, valid_to=None, kernel_status=KernelStatus.UNKNOWN), (), ()
        us = upstream.segments[0]
        pins = tuple(p.report_id for p in upstream.pinned)
        dep = (Dependency(key=upstream.key, version=upstream.version),)
        if us.established is not None and isinstance(us.established.form, ValueForm):
            c = Candidate(key=key, form=ValueForm(value=DERIVED + str(us.established.form.value)))
            sup = {c.id: tuple(Support(environment=s.environment) for s in us.support[us.established.id])}
            return Segment(valid_from=None, valid_to=None, kernel_status=KernelStatus.ESTABLISHED, established=c, support=sup), pins, dep
        return Segment(valid_from=None, valid_to=None, kernel_status=KernelStatus.UNKNOWN), pins, dep

    @staticmethod
    def _belief(key: Key, seg: Segment, pins: Sequence[str], deps: tuple[Dependency, ...], *, version: int, lsn: int, gen: int, at: datetime, incomplete: bool) -> Belief:
        return Belief(
            key=key, version=version, lsn=lsn, required_generation=gen, completed_generation=gen - 1 if incomplete else gen,
            segments=(seg,), pinned=tuple(Pin(report_id=i, admission_version=1) for i in pins), depends_on=deps,
            invalidated_by=None, versions=Versions(schema=1, semantic=1, admission=1),
            inference=Inference(complete=False, reason="budget") if incomplete else Inference(complete=True), recorded_at=at,
        )

    # -- the Reviser protocol
    def revise(self, ctx: RevisionContext) -> Sequence[Belief]:
        self.revise_calls += 1
        view, e = ctx.view, ctx.entry
        key = e.report.key
        reads = self._reads(view)
        order = [key.attr]
        i = 0
        while i < len(order):
            for d in view.attr_dependents(order[i]):
                if d not in order:
                    order.append(d)
            i += 1
        produced: dict[Key, Belief] = {}
        out: list[Belief] = []
        for attr in order:
            if attr != key.attr and attr in self.skip:
                continue
            k = Key(entity=key.entity, attr=attr)
            up: Belief | None = None
            if attr in reads:
                uk = Key(entity=key.entity, attr=reads[attr])
                up = produced.get(uk) or view.current_belief(uk)
            seg, pins, deps = self._content(view, k, up, attr in reads)
            cur = view.current_belief(k)
            b = self._belief(
                k, seg, pins, deps, version=(cur.version if cur else 0) + 1, lsn=e.lsn, gen=ctx.generation,
                at=e.recorded_at, incomplete=attr in self.incomplete,
            )
            produced[k] = b
            out.append(b)
        return out

    def recompute(self, key: Key, view: StoreView) -> Belief | None:
        self.recomputed.append(key)
        if self.fail_recompute:
            raise RuntimeError("recompute failed (test)")
        reads = self._reads(view)
        up = view.current_belief(Key(entity=key.entity, attr=reads[key.attr])) if key.attr in reads else None
        seg, pins, deps = self._content(view, key, up, key.attr in reads)
        incomplete = key.attr in self.recompute_incomplete
        return self._belief(
            key, seg, pins, deps, version=1, lsn=1, gen=2 if incomplete else 1,
            at=datetime(2026, 1, 1, tzinfo=UTC), incomplete=incomplete,
        )


class SchemalessReviser:
    """Revises a base key and a ``mirror`` key that depends on it, with NO schema (dependencies are recorded in the
    beliefs only): the situation in which a traversal overflow cannot be scoped to an attribute component."""

    def __init__(self) -> None:
        self._inner = ChainReviser()

    def _mirror(self, key: Key, base: Belief, version: int, lsn: int, gen: int, at: datetime) -> Belief:
        seg, pins, deps = self._inner._content(None, key, base, True)  # type: ignore[arg-type]
        return self._inner._belief(key, seg, pins, deps, version=version, lsn=lsn, gen=gen, at=at, incomplete=False)

    def revise(self, ctx: RevisionContext) -> Sequence[Belief]:
        view, e = ctx.view, ctx.entry
        key = e.report.key
        if key.attr == "mirror":
            return []
        seg, pins = _segment(key, _live_values(view, key))
        cur = view.current_belief(key)
        base = self._inner._belief(key, seg, pins, (), version=(cur.version if cur else 0) + 1, lsn=e.lsn, gen=ctx.generation, at=e.recorded_at, incomplete=False)
        mkey = Key(entity=key.entity, attr="mirror")
        mcur = view.current_belief(mkey)
        return [base, self._mirror(mkey, base, (mcur.version if mcur else 0) + 1, e.lsn, ctx.generation, e.recorded_at)]

    def recompute(self, key: Key, view: StoreView) -> Belief | None:
        at = datetime(2026, 1, 1, tzinfo=UTC)
        if key.attr == "mirror":
            base_key = Key(entity=key.entity, attr="x")
            base = view.current_belief(base_key)
            if base is None:
                return None
            return self._mirror(key, base, 1, 1, 1, at)
        seg, pins = _segment(key, _live_values(view, key))
        return self._inner._belief(key, seg, pins, (), version=1, lsn=1, gen=1, at=at, incomplete=False)
