"""The entity layer: what the pipeline does differently once entities are merged.

The pipeline justifies every key ``(entity, attr)`` from its *own* admitted evidence and stores that belief. The layer
adds one thing on top: for a class of merged entities, the **representative's** key ``(R, attr)`` is justified from the
evidence of *every member* (their reports re-keyed to ``(R, attr)`` for the kernel; report ids, supports and pins keep
the original reports). The members' own keys keep their own beliefs untouched, so reversing a merge only has to
recompute the representative's keys. Three hooks in :mod:`palimem.engine.pipeline` and :mod:`palimem.memory` call it:

* ``revise_overlay``: after the base beliefs of an append are built, add the representative's aggregated beliefs
  (for a plain append on a member, and for exactly the keys a merge or unmerge changes);
* ``recompute_base``: ``verify_beliefs`` / completion / erasure repair produce the *same* aggregated belief;
* ``canon_key``: a read of any member key is answered by the representative's belief at that snapshot, and the rules
  of derived keys read the representative too (``Resolver`` canonicalisation), so a rule that names ``"Veltran Inc"``
  sees what was reported about ``"veltran"``.

A merge pins its merge id on exactly the beliefs that consumed evidence across it (:meth:`ClassState.merge_pins`);
reversing it therefore recomputes exactly those and nothing else.

Known limits (docs/ENTITIES.md): admission (confirmation, A-SELF, quarantine) still works per *raw* key; the audit
paths (``Memory.justification`` and the yes/no slots) do not apply merges; entity-valued *objects* are canonicalised
when a rule reads them, but the dependents index is keyed by the raw value.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from palimem.entities.registry import (
    EMPTY_STATE,
    ENTITY_MERGE_ATTR,
    ClassState,
    MergeDecision,
    MergeRegistry,
)
from palimem.types import AdmissionOutcome, AuthorityRule, Belief, Key, LogEntry, Pin

if TYPE_CHECKING:
    from palimem.admission import Attribution
    from palimem.engine.pipeline import Pipeline
    from palimem.kernel import KernelSchema
    from palimem.store import RevisionContext, StoreView

EntriesOf = Callable[[Key], Sequence[LogEntry]]
AttributionsOf = Callable[[Key], Sequence["Attribution"]]
Canon = Callable[[Key], Key]


@dataclass(frozen=True)
class OverlayResult:
    """What the revision hook hands back: the canonicalisation the derived keys of this revision must use, and the
    keys it added (their attributes count as changed for the derived keys that read them)."""

    canon: Canon | None
    added: tuple[Key, ...]
    alias_keys: tuple[Key, ...] = ()
    """The same attributes under every other name of the affected classes (before and after the decision): a rule
    that binds a value equal to an alias name must still find its dependents when the representative's key changes."""


class EntityLayer:
    def __init__(self) -> None:
        self.registry = MergeRegistry()
        self._view: StoreView | None = None
        self.rules: tuple[AuthorityRule, ...] = ()
        """``merge`` grants declared on the reserved attribute (``Attr.authority``); the built-in default is
        ``system`` and ``user`` principals. Set by :func:`palimem.entities.api.attach_layer`."""

    # -- binding and log scanning

    def bind(self, view: StoreView) -> None:
        self._view = view
        self.registry.reset()

    def reset(self) -> None:
        """The log changed under the layer (an erasure, a restore, a rebind): forget what was scanned."""
        self.registry.reset()

    def sync(self, upto_lsn: int, view: StoreView | None = None) -> None:
        """Scan the committed rows ``scanned + 1 .. upto_lsn`` for honoured merge decisions."""
        v = view if view is not None else self._view
        if v is None or upto_lsn <= self.registry.scanned:
            return
        for row in v.scan(self.registry.scanned + 1, upto_lsn):
            if not isinstance(row, LogEntry) or row.report.key.attr != ENTITY_MERGE_ATTR or row.report.id is None:
                continue
            recs = v.admissions_for_report(row.report.id)
            outcome = recs[-1].outcome if recs else None
            version = recs[-1].admission_version if recs else None
            dec = self.registry.decision_of(row, outcome, version, self.rules)
            if dec is not None:
                self.registry.add(dec)
        self.registry.scanned = upto_lsn

    def state_at(self, lsn: int, view: StoreView | None = None) -> ClassState:
        self.sync(lsn, view)
        return self.registry.state_at(lsn)

    # -- reads

    def canon_key(self, key: Key, lsn: int) -> Key:
        """The key whose belief answers a read of ``key`` at log position ``lsn``."""
        st = self.state_at(lsn)
        if st.empty:
            return key
        rep = st.canon(key.entity)
        return key if rep == key.entity else Key(entity=rep, attr=key.attr)

    def canon_fn(self, state: ClassState) -> Canon | None:
        if state.empty:
            return None

        def canon(key: Key) -> Key:
            rep = state.canon(key.entity)
            return key if rep == key.entity else Key(entity=rep, attr=key.attr)

        return canon

    def head_canon(self, view: StoreView) -> Canon | None:
        head = view.head().lsn
        return self.canon_fn(self.state_at(head, view))

    # -- the aggregated belief

    @staticmethod
    def base_attrs(ks: KernelSchema) -> list[str]:
        return [a for a, s in ks.attrs.items() if not s.derived and a != ENTITY_MERGE_ATTR]

    @staticmethod
    def _gather(
        state: ClassState, rep: str, attr: str, entries_of: EntriesOf
    ) -> tuple[list[LogEntry], tuple[str, ...]]:
        """Every member's admitted entries for ``attr``, re-keyed to ``(rep, attr)`` and in log order, and the members
        that contributed at least one."""
        out: list[LogEntry] = []
        contributors: list[str] = []
        key = Key(entity=rep, attr=attr)
        for m in state.members(rep):
            es = entries_of(Key(entity=m, attr=attr))
            if not es:
                continue
            contributors.append(m)
            for e in es:
                out.append(e if m == rep else replace(e, report=replace(e.report, key=key)))
        out.sort(key=lambda e: e.lsn)
        return out, tuple(contributors)

    def aggregated(
        self, p: Pipeline, ks: KernelSchema, state: ClassState, rep: str, attr: str, entries_of: EntriesOf,
        attributions_of: AttributionsOf, *, version: int, lsn: int, generation: int, inputs: Mapping[str, int],
        recorded_at: object,
    ) -> Belief:
        """The belief of ``(rep, attr)`` over the whole class, pinning the merges its evidence crossed."""
        key = Key(entity=rep, attr=attr)
        entries, contributors = self._gather(state, rep, attr, entries_of)
        b = p.base_belief(
            ks, key, entries, version=version, lsn=lsn, generation=generation, inputs=inputs,
            recorded_at=recorded_at,  # type: ignore[arg-type]
            attributions=attributions_of(key),
        )
        mids = state.merge_pins(rep, contributors)
        if not mids:
            return b
        av = p.admitter.config.admission_version
        have = {(x.report_id, x.admission_version) for x in b.pinned}
        extra = tuple(Pin(report_id=m, admission_version=av) for m in mids if (m, av) not in have)
        return replace(b, pinned=(*b.pinned, *extra))

    # -- revision hook

    def pending_decision(self, ctx: RevisionContext) -> MergeDecision | None:
        r = ctx.entry.report
        if r.key.attr != ENTITY_MERGE_ATTR or r.id is None:
            return None
        outcome: AdmissionOutcome | None = None
        version: int | None = None
        for rec in ctx.admissions:
            if rec.report_id == r.id:
                outcome = rec.outcome
                version = rec.admission_version
                break
        return self.registry.decision_of(ctx.entry, outcome, version, self.rules)

    def revise_overlay(
        self, p: Pipeline, ctx: RevisionContext, ks: KernelSchema, overlay: dict[Key, Belief], entries_of: EntriesOf,
        attributions_of: AttributionsOf, base_changed: Sequence[Key], next_version: Callable[[Key], int],
    ) -> OverlayResult:
        lsn = ctx.entry.lsn
        self.sync(lsn - 1, ctx.view)
        dec = self.pending_decision(ctx)
        before = self.registry.state_at(lsn - 1)
        after = self.registry.state_with(lsn - 1, dec) if dec is not None else before
        if before.empty and after.empty:
            return OverlayResult(canon=None, added=())
        attrs = self.base_attrs(ks)
        affected: dict[Key, ClassState] = {}  # key -> the class state its belief is rebuilt under
        if dec is not None:
            nodes = {n for e in (*before.edges, *after.edges) for n in (e.alias, e.into)}
            nodes.add(dec.alias)
            if dec.into is not None:
                nodes.add(dec.into)
            for rep in sorted({after.canon(n) for n in nodes}):
                for a in attrs:
                    was = (
                        {m for m in before.members(rep) if entries_of(Key(entity=m, attr=a))}
                        if before.canon(rep) == rep
                        else ({rep} if entries_of(Key(entity=rep, attr=a)) else set())
                    )
                    now = {m for m in after.members(rep) if entries_of(Key(entity=m, attr=a))}
                    if was != now:
                        affected[Key(entity=rep, attr=a)] = after
            # a representative that stops being one (it was merged into another class) goes back to its own evidence
            for n in sorted(nodes):
                if before.canon(n) == n and len(before.members(n)) > 1 and after.canon(n) != n:
                    for a in attrs:
                        was = {m for m in before.members(n) if entries_of(Key(entity=m, attr=a))}
                        now = {n} if entries_of(Key(entity=n, attr=a)) else set()
                        if was != now:
                            affected[Key(entity=n, attr=a)] = EMPTY_STATE
        for k in base_changed:
            if k.attr == ENTITY_MERGE_ATTR:
                continue
            rep = after.canon(k.entity)
            if len(after.members(rep)) > 1:
                affected[Key(entity=rep, attr=k.attr)] = after
        added: list[Key] = []
        for k in sorted(affected, key=lambda x: (x.entity, x.attr)):
            version = overlay[k].version if k in overlay else next_version(k)
            overlay[k] = self.aggregated(
                p, ks, affected[k], k.entity, k.attr, entries_of, attributions_of, version=version, lsn=lsn,
                generation=ctx.generation, inputs=ctx.inputs, recorded_at=ctx.entry.recorded_at,
            )
            added.append(k)
        aliases: dict[Key, None] = {}
        for k in added:
            for m in {*before.members(k.entity), *after.members(k.entity)}:
                if m != k.entity:
                    aliases[Key(entity=m, attr=k.attr)] = None
        return OverlayResult(canon=self.canon_fn(after), added=tuple(added), alias_keys=tuple(aliases))

    # -- recompute hook (verify, completion jobs, erasure repair)

    def recompute_base(
        self, p: Pipeline, ks: KernelSchema, key: Key, entries_of: EntriesOf, attributions_of: AttributionsOf, *,
        view: StoreView, version: int, lsn: int, generation: int, inputs: Mapping[str, int], recorded_at: object,
    ) -> Belief | None:
        """The aggregated belief of ``key`` at the head, or ``None`` when ``key`` is not a merged class's
        representative (the caller then recomputes it from its own evidence as before)."""
        if key.attr == ENTITY_MERGE_ATTR or ks.spec(key.attr).derived:
            return None
        st = self.state_at(view.head().lsn, view)
        if st.empty or st.canon(key.entity) != key.entity or len(st.members(key.entity)) <= 1:
            return None
        return self.aggregated(
            p, ks, st, key.entity, key.attr, entries_of, attributions_of, version=version, lsn=lsn,
            generation=generation, inputs=inputs, recorded_at=recorded_at,
        )


__all__ = ["EntityLayer", "OverlayResult"]
