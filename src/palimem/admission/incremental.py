"""Incremental admission (Lane O2): the same decisions as the whole-log pass, one append at a time.

:meth:`Admitter.evaluate` is a pure function of the log prefix and one config, and costs O(log length): it
derives the withdrawal effects over every actor and the derived confirmations over every quarantined report. An
append needs to know only what *this* report changed, so :class:`IncrementalAdmission` keeps the evaluation's
state for the committed head and updates it by the reports whose status can change because of the new one:

* **Own-merit decision** of the new report: memoised per report (:meth:`Admitter._decide`), never changes later.
* **Withdrawal effects**: a non-actor report changes them only if a standing source-level withdrawal covers its
  source; an actor (withdraw, authorised correct) changes them for the reports it reaches. Those are recomputed
  over the *actors* only (``actors`` is the sparse set of reports that withdraw something), with source-level
  expansion through a by-source index, in exactly the order the whole-log pass uses. This is the one rule kept on a
  bounded slow path: O(actors + reports of the covered sources) per *actor* append, never per plain append.
* **Derived confirmation**: only the keys that have a quarantined evidence report, and only the keys whose
  evidence or withdrawal status changed, are recomputed (confirmers and quarantined reports of one key).
* **Direct evidence** (what the kernel reads): kept per key; a plain append extends one tuple, any key touched by
  a withdrawal or confirmation change is rebuilt from that key's own reports.

Every mutation goes through an undo journal, because the store's append is one transaction that can roll back
after admission ran: the journal of the last append stays *pending* until the next call finds that report
committed in the log (kept) or absent (undone). A history rewrite (erasure, restore, config change) rebuilds the
state from the log. The whole-log evaluator stays the audit oracle: ``snapshot()`` is compared with it decision for
decision in the equivalence tests and, in ``crosscheck`` mode, after every append.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from palimem.types import (
    AdmissionOutcome,
    AdmissionReason,
    AdmissionRecord,
    BeliefOfProp,
    Cue,
    Key,
    LogEntry,
    Origin,
)

from .admitter import (
    EVIDENCE_CUES,
    AdmissionDecision,
    Admitter,
    Attribution,
    Withdrawal,
    attributions_from_groups,
)
from .equivalence import proposition_signature


class SyncLog(Protocol):
    """What :meth:`IncrementalAdmission.sync` needs of the log: committed rows only."""

    def get(self, report_id: str) -> LogEntry | None: ...

    def entries(self, *, upto_lsn: int | None = None) -> Sequence[LogEntry]: ...

    def head_lsn(self) -> int: ...


def supports_incremental(admitter: Admitter) -> bool:
    """May this admitter be evaluated incrementally? Only if it does not override the whole-log internals
    (``_evaluate``, ``_withdrawals``) behind the incremental path's back; a subclass that adjusts the withdrawal
    effects (the compat profile's source-level retraction) opts in by defining the two overlay hooks
    ``incremental_overlay_trigger(inc, entry) -> bool`` and ``incremental_overlay(inc, base_withdrawn) -> dict``,
    which must compute exactly what its ``_evaluate`` override computes from the base withdrawals."""
    cls = type(admitter)
    if cls._withdrawals is not Admitter._withdrawals:
        return False
    if cls._evaluate is Admitter._evaluate:
        return True
    return hasattr(admitter, "incremental_overlay") and hasattr(admitter, "incremental_overlay_trigger")


@dataclass(frozen=True)
class AdmissionDelta:
    """What one append changed in admission (the input of revision and of the admission records)."""

    lsn: int
    report_id: str
    records: tuple[AdmissionRecord, ...]
    """The admission records of the append: the new report's first, then every earlier report whose outcome, reason
    or confirmers changed because of it, in log order (a confirmation lifting a quarantine, a lapsed one)."""
    changed_keys: frozenset[Key]
    """Keys whose set of direct (kernel-visible) reports changed. The appended report's own key is *not* implied."""


@dataclass
class _Pending:
    lsn: int
    report_id: str
    undo: list[Callable[[], object]]


def _rid(e: LogEntry) -> str:
    rid = e.report.id
    assert rid is not None
    return rid


class IncrementalAdmission:
    """Admission state for the committed head of one log under one :class:`Admitter` (one admission version)."""

    def __init__(self, admitter: Admitter) -> None:
        self.admitter = admitter
        self._tx: list[Callable[[], object]] | None = None
        self._pending: _Pending | None = None
        self.rebuilds = 0
        """How many times the state had to be rebuilt from the log (a cold start, a rewritten log). A rolled-back append
        is undone from the journal and must not count."""
        self._reset()

    # ------------------------------------------------------------------ state

    def _reset(self) -> None:
        self.entries: list[LogEntry] = []
        self.by_id: dict[str, LogEntry] = {}
        self.by_key: dict[Key, list[LogEntry]] = {}
        self.by_source: dict[str, list[LogEntry]] = {}
        self.by_attr: dict[str, list[LogEntry]] = {}
        self.actors: dict[str, LogEntry] = {}
        self.src_cover: dict[str, int] = {}
        self.quar_keys: dict[Key, int] = {}
        self.withdrawn_base: Mapping[str, Withdrawal] = {}
        self.withdrawn: Mapping[str, Withdrawal] = {}
        self.confirmed: dict[str, AdmissionDecision] = {}
        self.direct: dict[Key, tuple[LogEntry, ...]] = {}
        self.belief_of_count = 0
        self.entity_set: set[str] = set()
        self.entities: tuple[str, ...] = ()
        self._sig: dict[str, str] = {}
        self._pending = None
        self._tx = None

    @property
    def head(self) -> int:
        return self.entries[-1].lsn if self.entries else 0

    @property
    def last_id(self) -> str | None:
        return _rid(self.entries[-1]) if self.entries else None

    def decision(self, rid: str) -> AdmissionDecision:
        d = self.confirmed.get(rid)
        return d if d is not None else self._base(self.by_id[rid])

    def direct_of(self, key: Key) -> tuple[LogEntry, ...]:
        return self.direct.get(key, ())

    # ------------------------------------------------------------------ undo journal

    def _record(self, fn: Callable[[], object]) -> None:
        if self._tx is not None:
            self._tx.append(fn)

    def _put(self, d: dict, k: object, v: object) -> None:  # type: ignore[type-arg]
        had = k in d
        old = d.get(k)
        d[k] = v
        self._record((lambda: d.__setitem__(k, old)) if had else (lambda: d.pop(k, None)))

    def _del(self, d: dict, k: object) -> None:  # type: ignore[type-arg]
        old = d.pop(k)
        self._record(lambda: d.__setitem__(k, old))

    def _append(self, d: dict, k: object, e: LogEntry) -> None:  # type: ignore[type-arg]
        lst = d.get(k)
        if lst is None:
            lst = d[k] = []
            self._record(lambda: d.pop(k, None))
        lst.append(e)
        self._record(lambda: lst.pop())

    def _set(self, name: str, value: object) -> None:
        old = getattr(self, name)
        setattr(self, name, value)
        self._record(lambda: setattr(self, name, old))

    # ------------------------------------------------------------------ synchronisation with the committed log

    def settle(self, log: SyncLog) -> None:
        """Resolve the pending append: committed in the log (keep) or gone (undo)."""
        p = self._pending
        if p is None:
            return
        self._pending = None
        row = log.get(p.report_id)
        if row is not None and row.lsn == p.lsn:
            return
        for fn in reversed(p.undo):
            fn()

    def sync(self, log: SyncLog, *, before_lsn: int) -> None:
        """Bring the state to the committed log prefix ``lsn < before_lsn`` (settle, catch up, or rebuild)."""
        self.settle(log)
        target = min(log.head_lsn(), before_lsn - 1)
        if self.head == target:
            last = self.entries[-1] if self.entries else None
            if last is None or ((row := log.get(_rid(last))) is not None and row.lsn == last.lsn):
                return
            self._rebuild(log, target)
            return
        if self.head < target:
            last = self.entries[-1] if self.entries else None
            if last is None or ((row := log.get(_rid(last))) is not None and row.lsn == last.lsn):
                for e in log.entries(upto_lsn=target):
                    if e.lsn > self.head:
                        self._apply(e)
                return
        self._rebuild(log, target)

    def _rebuild(self, log: SyncLog, target: int) -> None:
        self.rebuilds += 1
        self._reset()
        for e in log.entries(upto_lsn=target):
            self._apply(e)

    # ------------------------------------------------------------------ the update

    def append(self, entry: LogEntry) -> AdmissionDelta:
        """Apply one new report (its LSN above the head). The change stays pending (undoable) until the next
        :meth:`settle`/:meth:`sync` finds the report committed."""
        if self.entries and entry.lsn <= self.head:
            raise ValueError("IncrementalAdmission.append: LSN must be above the head")
        if self._pending is not None:
            raise RuntimeError("IncrementalAdmission.append: the previous append is unsettled (call sync first)")
        tx: list[Callable[[], object]] = []
        self._tx = tx
        try:
            delta = self._apply(entry)
        except BaseException:
            self._tx = None
            for fn in reversed(tx):
                fn()
            raise
        self._tx = None
        self._pending = _Pending(lsn=entry.lsn, report_id=_rid(entry), undo=tx)
        return delta

    def _base(self, e: LogEntry) -> AdmissionDecision:
        memo = self.admitter._own_merit
        rid = _rid(e)
        d = memo.get(rid)
        if d is None:
            d = memo[rid] = self.admitter._decide(e, self.by_id)
        return d

    def _signature(self, e: LogEntry) -> str:
        rid = _rid(e)
        s = self._sig.get(rid)
        if s is None:
            prop = e.report.proposition
            assert prop is not None
            s = self._sig[rid] = proposition_signature(prop)
            self._record(lambda: self._sig.pop(rid, None))
        return s

    def _is_direct(self, e: LogEntry) -> bool:
        rid = _rid(e)
        r = e.report
        return (
            rid not in self.withdrawn
            and r.cue in EVIDENCE_CUES
            and not isinstance(r.proposition, BeliefOfProp)
            and r.origin is Origin.EXTERNAL_OBSERVATION
            and self.decision(rid).record.outcome is AdmissionOutcome.ADMISSIBLE
        )

    def _compute_withdrawn(self) -> dict[str, Withdrawal]:
        """The whole-log ``_withdrawals`` over the actors only: identical order, identical ``setdefault`` ownership."""
        cfg = self.admitter.config
        actors = list(self.actors.values())  # ascending LSN (insertion order)
        if cfg.must_be_live:
            actors.reverse()  # newest first; a withdrawn actor no longer acts
        out: dict[str, Withdrawal] = {}
        for a in actors:
            rid = _rid(a)
            if cfg.must_be_live and rid in out:
                continue
            d = self._base(a)
            kind = "self_correction" if a.report.cue is Cue.CORRECT else "withdraw"
            for t in d.withdraws:
                out.setdefault(t, Withdrawal(by=rid, kind=kind))
            if d.withdraws_source is not None:
                for x in self.by_source.get(d.withdraws_source, ()):
                    out.setdefault(_rid(x), Withdrawal(by=rid, kind="source_withdraw"))
        return out

    def _apply(self, e: LogEntry) -> AdmissionDelta:
        rid = _rid(e)
        r = e.report
        # ---- indexes
        self.entries.append(e)
        self._record(lambda: self.entries.pop())
        self._put(self.by_id, rid, e)
        self._append(self.by_key, r.key, e)
        self._append(self.by_source, r.source.id, e)
        self._append(self.by_attr, r.key.attr, e)
        if r.key.entity not in self.entity_set:
            self.entity_set.add(r.key.entity)
            ent = r.key.entity
            self._record(lambda: self.entity_set.discard(ent))
            self._set("entities", tuple(sorted(self.entity_set)))
        if isinstance(r.proposition, BeliefOfProp):
            self._set("belief_of_count", self.belief_of_count + 1)
        d0 = self._base(e)
        is_actor = bool(d0.withdraws or d0.withdraws_source)
        if is_actor:
            self._put(self.actors, rid, e)
            if d0.withdraws_source is not None:
                self._put(self.src_cover, d0.withdraws_source, self.src_cover.get(d0.withdraws_source, 0) + 1)
        is_quarantined_evidence = (
            d0.record.outcome is AdmissionOutcome.QUARANTINED and r.proposition is not None and r.cue in EVIDENCE_CUES
        )
        if is_quarantined_evidence:
            self._put(self.quar_keys, r.key, self.quar_keys.get(r.key, 0) + 1)

        # ---- withdrawal effects (slow path only for an actor, or a report a standing source withdrawal covers)
        dirty: set[Key] = set()
        recompute = is_actor or self.src_cover.get(r.source.id, 0) > 0
        trigger = getattr(self.admitter, "incremental_overlay_trigger", None)
        overlay = getattr(self.admitter, "incremental_overlay", None)
        if recompute:
            new_base = self._compute_withdrawn()
            if new_base != self.withdrawn_base:
                self._set("withdrawn_base", new_base)
        if recompute or (trigger is not None and trigger(self, e)):
            new_w = overlay(self, self.withdrawn_base) if overlay is not None else self.withdrawn_base
            old_w = self.withdrawn
            if new_w != old_w:
                for item in old_w.keys() | new_w.keys():
                    if old_w.get(item) != new_w.get(item):
                        dirty.add(self.by_id[item].report.key)
                self._set("withdrawn", new_w)

        # ---- derived confirmation, only on keys that hold quarantined evidence
        changed_records: list[LogEntry] = []
        for k in {r.key, *dirty}:
            if k in self.quar_keys:
                changed_records.extend(self._reconfirm(k))
        dirty.update(c.report.key for c in changed_records)

        # ---- direct evidence per key
        changed_keys: set[Key] = set()
        for k in dirty:
            new = tuple(x for x in self.by_key.get(k, ()) if self._is_direct(x))
            old = self.direct.get(k, ())
            if tuple(_rid(x) for x in new) != tuple(_rid(x) for x in old):
                changed_keys.add(k)
            if new:
                self._put(self.direct, k, new)
            elif k in self.direct:
                self._del(self.direct, k)
        if r.key not in dirty and self._is_direct(e):
            self._put(self.direct, r.key, self.direct.get(r.key, ()) + (e,))
            changed_keys.add(r.key)

        records: list[AdmissionRecord] = [self.decision(rid).record]
        for c in sorted(changed_records, key=lambda x: x.lsn):
            crid = _rid(c)
            if crid != rid:
                records.append(self.decision(crid).record)
        return AdmissionDelta(lsn=e.lsn, report_id=rid, records=tuple(records), changed_keys=frozenset(changed_keys))

    def _reconfirm(self, key: Key) -> list[LogEntry]:
        """Recompute the derived confirmations of one key; return the entries whose (outcome, reason, confirmers) changed."""
        ents = self.by_key.get(key, ())
        quars = [
            q
            for q in ents
            if self._base(q).record.outcome is AdmissionOutcome.QUARANTINED
            and q.report.proposition is not None
            and q.report.cue in EVIDENCE_CUES
        ]
        if not quars:
            return []
        confirmers = [
            c
            for c in ents
            if self._base(c).own_merit
            and _rid(c) not in self.withdrawn
            and c.report.cue in EVIDENCE_CUES
            and c.report.proposition is not None
        ]
        changed: list[LogEntry] = []
        for q in quars:
            qid = _rid(q)
            base = self._base(q)
            qsig = self._signature(q)
            conf = tuple(
                sorted(
                    _rid(c)
                    for c in confirmers
                    if c.report.origin_group != q.report.origin_group and self._signature(c) == qsig
                )
            )
            old = self.confirmed.get(qid)
            before = (old if old is not None else base).record
            if conf:
                if old is not None and old.record.confirmed_by == conf:
                    continue
                new = AdmissionDecision(
                    record=self.admitter._record(q, AdmissionOutcome.ADMISSIBLE, AdmissionReason.CONFIRMED, conf),
                    effective_cue=base.effective_cue,
                    withdraws=(),
                    authority=base.authority,
                )
                self._put(self.confirmed, qid, new)
                after = new.record
            else:
                if old is None:
                    continue
                self._del(self.confirmed, qid)
                after = base.record
            if (before.outcome, before.reason, before.confirmed_by) != (after.outcome, after.reason, after.confirmed_by):
                changed.append(q)
        return changed

    # ------------------------------------------------------------------ reads

    def attributions_of(self, key: Key) -> tuple[Attribution, ...]:
        """The attributed claims on ``key`` (what ``EvidenceSet.attributions`` holds)."""
        groups: dict[str, list[LogEntry]] = {}
        for e in self.by_key.get(key, ()):
            rid = _rid(e)
            r = e.report
            if (
                rid not in self.withdrawn
                and r.cue in EVIDENCE_CUES
                and isinstance(r.proposition, BeliefOfProp)
                and self.decision(rid).record.outcome is AdmissionOutcome.ADMISSIBLE
            ):
                groups.setdefault(proposition_signature(r.proposition), []).append(e)
        return attributions_from_groups(groups)

    def snapshot(self) -> tuple[dict[str, AdmissionDecision], dict[str, Withdrawal], dict[Key, tuple[str, ...]]]:
        """Final decisions, withdrawals and direct report ids per key (audit/equivalence comparison; O(n))."""
        decisions = {_rid(e): self.decision(_rid(e)) for e in self.entries}
        direct = {k: tuple(_rid(x) for x in v) for k, v in self.direct.items() if v}
        return decisions, dict(self.withdrawn), direct
