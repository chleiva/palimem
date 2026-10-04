"""The admission stage (T-D1, T-D2, T-D5): ``evidence log -> admission -> kernel``.

Admission decides which logged reports enter the evidence set the kernel reasons over. It is a pure
function of (the log prefix up to an LSN, one :class:`AdmissionConfig`), so a historical
``belief_as_of`` query re-derives exactly the decisions that were current then. Every decision is an
:class:`~palimem.types.AdmissionRecord` (outcome, reason code, admission version).

Rules, in the order they apply to a report:

1. **Blocked source** (the paper's ``blocked``): ``excluded / source_blocked``. Never confirmable,
   never acts.
2. **Operator cues** (``withdraw``, ``dispute``, ``allege``, and the authority half of ``correct``):
   the target must exist earlier in the log (``target_missing``), and the actor must hold authority
   (:mod:`.authz`). Failed authority downgrades the cue to ``allege``: recorded
   ``excluded / authority_failed``, no effect on admissibility, visible to audits and the inquiry.
   A failed ``correct`` is *not* an allege (S-02 recommendation A): its proposition stays an ordinary
   admissible assert carrying the correction hint (the kernel's A-CORR), it just does not withdraw
   its target. Operator effects are independent of evidence admissibility: an agent's
   self-withdrawal acts although agent-origin content is never evidence.
3. **Origin**: only ``external_observation`` is direct evidence. ``attributed`` (and any report whose
   proposition is ``belief_of``) is evidence for the attribution only (:class:`Attribution`),
   never for the inner proposition. Agent-class origins (``agent_hypothesis``, ``agent_statement``,
   ``plan``, ``simulation``, ``counterfactual``) are never admissible and never confirm.
4. **Quarantined source**: ``quarantined / source_quarantined`` until *derived confirmation*.
5. **Confirmation (S-01)**: a quarantined report becomes ``admissible / confirmed`` when a report that
   is admissible *on its own merits* (reason ``admitted``, not itself confirmed, not withdrawn) from a
   **different origin group** has the same key and an *equivalent* proposition
   (:mod:`.equivalence`). Two quarantined sources never confirm each other; a confirmed report
   never confirms a third (no chain laundering), and a confirmed report never withdraws or
   corrects anything (it is evidence, not authority). If every confirmer is withdrawn the
   confirmation lapses. ``origin_group`` counts once.

Withdrawal effects (``Evaluation.withdrawn``) leave the report in the log and its admission record
unchanged; the kernel simply does not see it. Under ``acting_reports_must_be_live`` an actor that has
itself been withdrawn no longer acts (the target is restored); processing runs from the newest
actor to the oldest, which is well defined because an actor's target always has a lower LSN. A
standing source-level withdrawal (a rule with ``targets: source``) is applied in the same pass; the
edge case of a source-level withdrawal that later withdraws an actor whose effects were already
applied does not undo those effects.

Known gap (flagged to the author): a ``withdraw`` Report carries only a report-id target, so a
*request* for a source-wide withdrawal cannot be expressed; the extent comes from the matching rule
(``targets: source``). The compat adapter (T-E3) needs a request-level discriminator to map the
paper's ``retract(source)``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from palimem.types import (
    AdmissionOutcome,
    AdmissionReason,
    AdmissionRecord,
    BeliefOfProp,
    Cue,
    Key,
    LogEntry,
    Origin,
    Power,
    Schema,
    Targets,
)
from palimem.types.authority import AGENT_CLASS_ORIGINS

from .authz import AuthDecision, Authorizer
from .config import AdmissionConfig, SourceStatus
from .equivalence import equivalent, proposition_signature
from .ids import derive_ulid
from .log import LogView

EVIDENCE_CUES = (Cue.ASSERT, Cue.CHANGE, Cue.CORRECT)
_OPERATOR_CUES = (Cue.WITHDRAW, Cue.DISPUTE, Cue.ALLEGE)
_ACTING_ORIGINS = frozenset({Origin.EXTERNAL_OBSERVATION}) | AGENT_CLASS_ORIGINS
_EVIDENCE_ORIGINS = frozenset({Origin.EXTERNAL_OBSERVATION, Origin.ATTRIBUTED})


@dataclass(frozen=True)
class Withdrawal:
    by: str  # id of the acting report
    kind: str  # "withdraw" | "self_correction" | "source_withdraw"


@dataclass(frozen=True)
class AdmissionDecision:
    record: AdmissionRecord
    effective_cue: Cue  # ALLEGE when a withdraw/dispute failed authority
    withdraws: tuple[str, ...] = ()  # report ids this report withdraws (authorised withdraw / A-SELF)
    withdraws_source: str | None = None  # a source id withdrawn wholesale (rule extent: source)
    authority: AuthDecision | None = None

    @property
    def own_merit(self) -> bool:
        """Admissible by itself, not by confirmation: the only kind that confirms or acts."""
        return self.record.outcome is AdmissionOutcome.ADMISSIBLE and self.record.reason is AdmissionReason.ADMITTED


@dataclass(frozen=True)
class Evaluation:
    """Admission over one log prefix."""

    admission_version: int
    as_of_lsn: int | None
    entries: tuple[LogEntry, ...]
    decisions: Mapping[str, AdmissionDecision]  # by report id (final, after confirmation)
    withdrawn: Mapping[str, Withdrawal]  # by withdrawn report id


@dataclass(frozen=True)
class Attribution:
    """Equivalent attributed claims ``belief_of(holder, P)``; they establish the attribution only.

    ``origin_groups`` counts each origin group once; how many reports there are is irrelevant
    to corroboration (S-11 / design: attributed claims "corroborated by any number of origin
    groups establish belief_of(holder, P) and never P").
    """

    signature: str
    proposition: BeliefOfProp
    entries: tuple[LogEntry, ...]
    origin_groups: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceSet:
    """What the kernel may read for one key (the kernel never reads the log directly)."""

    key: Key
    admission_version: int
    as_of_lsn: int | None
    direct: tuple[LogEntry, ...]  # admissible, not withdrawn, external, plain proposition; assert/change/correct
    attributions: tuple[Attribution, ...]
    disputes: tuple[LogEntry, ...]  # authorised disputes whose target is on this key (semantics: kernel lane)
    allegations: tuple[LogEntry, ...]  # allege-cue reports targeting this key: audit/inquiry only
    quarantined: tuple[LogEntry, ...]  # logged, not (yet) admissible
    withdrawn: Mapping[str, Withdrawal]  # reports of this key removed from consideration


def origin_group_count(entries: Sequence[LogEntry]) -> int:
    """Distinct origin groups among ``entries`` (reports of one group count once)."""
    return len({e.report.origin_group for e in entries})


class Admitter:
    """Admission bound to one admission version (config + optional schema for per-attribute authority).

    A historical query uses the Admitter whose config was current at its ``belief_as_of``
    (the store keeps one config per admission version).
    """

    def __init__(self, config: AdmissionConfig, schema: Schema | None = None) -> None:
        self.config = config
        self.authz = Authorizer(config, schema)

    # ------------------------------------------------------------------ public API

    def admit(self, entry: LogEntry, log: LogView) -> AdmissionDecision:
        """The decision for ``entry`` given the log up to (and including) its own LSN."""
        prefix = [e for e in log.entries(upto_lsn=entry.lsn - 1) if e.lsn < entry.lsn] + [entry]
        rid = entry.report.id
        assert rid is not None
        return self._evaluate(prefix, entry.lsn).decisions[rid]

    def evaluate(self, log: LogView, *, as_of_lsn: int | None = None) -> Evaluation:
        return self._evaluate(list(log.entries(upto_lsn=as_of_lsn)), as_of_lsn)

    def evidence_set(self, log: LogView, key: Key, *, as_of_lsn: int | None = None) -> EvidenceSet:
        """``evidence_set(key, admission_version)`` of the design: the admissible reports for ``key``
        under this Admitter's admission version, as of ``as_of_lsn`` (default: the whole log)."""
        return self.evidence_set_of(self.evaluate(log, as_of_lsn=as_of_lsn), key)

    def evidence_set_of(self, ev: Evaluation, key: Key) -> EvidenceSet:
        by_id = {_rid(e): e for e in ev.entries}
        direct: list[LogEntry] = []
        quarantined: list[LogEntry] = []
        disputes: list[LogEntry] = []
        allegations: list[LogEntry] = []
        groups: dict[str, list[LogEntry]] = {}
        for e in ev.entries:
            d = ev.decisions[_rid(e)]
            r = e.report
            if r.key == key and _rid(e) not in ev.withdrawn:
                if d.record.outcome is AdmissionOutcome.ADMISSIBLE and r.cue in EVIDENCE_CUES:
                    if isinstance(r.proposition, BeliefOfProp):
                        groups.setdefault(proposition_signature(r.proposition), []).append(e)
                    elif r.origin is Origin.EXTERNAL_OBSERVATION:
                        direct.append(e)
                elif d.record.outcome is AdmissionOutcome.QUARANTINED:
                    quarantined.append(e)
            if d.effective_cue in (Cue.DISPUTE, Cue.ALLEGE) and r.target is not None:
                t = by_id.get(r.target)
                if t is not None and t.report.key == key:
                    if d.effective_cue is Cue.DISPUTE and d.record.outcome is AdmissionOutcome.ADMISSIBLE:
                        disputes.append(e)
                    elif d.effective_cue is Cue.ALLEGE:
                        allegations.append(e)
        attributions: list[Attribution] = []
        for sig in sorted(groups):
            es = tuple(groups[sig])
            prop = es[0].report.proposition
            assert isinstance(prop, BeliefOfProp)
            attributions.append(
                Attribution(
                    signature=sig,
                    proposition=prop,
                    entries=es,
                    origin_groups=tuple(sorted({e.report.origin_group for e in es})),
                )
            )
        withdrawn = {rid: w for rid, w in ev.withdrawn.items() if by_id[rid].report.key == key}
        return EvidenceSet(
            key=key,
            admission_version=ev.admission_version,
            as_of_lsn=ev.as_of_lsn,
            direct=tuple(direct),
            attributions=tuple(attributions),
            disputes=tuple(disputes),
            allegations=tuple(allegations),
            quarantined=tuple(quarantined),
            withdrawn=MappingProxyType(withdrawn),
        )

    # ------------------------------------------------------------------ internals

    def _record(
        self, entry: LogEntry, outcome: AdmissionOutcome, reason: AdmissionReason, confirmed: tuple[str, ...] = ()
    ) -> AdmissionRecord:
        rid = _rid(entry)
        v = self.config.admission_version
        return AdmissionRecord(
            id=derive_ulid(rid, str(v), outcome.value, reason.value, *confirmed),
            report_id=rid,
            outcome=outcome,
            reason=reason,
            admission_version=v,
            confirmed_by=confirmed,
        )

    def _decision(
        self,
        entry: LogEntry,
        outcome: AdmissionOutcome,
        reason: AdmissionReason,
        cue: Cue | None = None,
        *,
        withdraws: tuple[str, ...] = (),
        withdraws_source: str | None = None,
        authority: AuthDecision | None = None,
    ) -> AdmissionDecision:
        return AdmissionDecision(
            record=self._record(entry, outcome, reason),
            effective_cue=cue or entry.report.cue,
            withdraws=withdraws,
            withdraws_source=withdraws_source,
            authority=authority,
        )

    def _evidence_decision(self, entry: LogEntry, status: SourceStatus) -> AdmissionDecision:
        """Evidence admissibility alone: origin, then source status."""
        if entry.report.origin not in _EVIDENCE_ORIGINS:
            return self._decision(entry, AdmissionOutcome.EXCLUDED, AdmissionReason.ORIGIN_NOT_ADMISSIBLE)
        if status is SourceStatus.QUARANTINED:
            return self._decision(entry, AdmissionOutcome.QUARANTINED, AdmissionReason.SOURCE_QUARANTINED)
        return self._decision(entry, AdmissionOutcome.ADMISSIBLE, AdmissionReason.ADMITTED)

    def _decide(self, entry: LogEntry, by_id: Mapping[str, LogEntry]) -> AdmissionDecision:
        """The decision on the report's own merits, before confirmation and before withdrawal effects."""
        r = entry.report
        status = self.config.status_of(r.source)
        excl, quar = AdmissionOutcome.EXCLUDED, AdmissionOutcome.QUARANTINED

        if status is SourceStatus.BLOCKED:
            return self._decision(entry, excl, AdmissionReason.SOURCE_BLOCKED)
        if r.cue in (Cue.ASSERT, Cue.CHANGE):
            return self._evidence_decision(entry, status)

        # cues that point at another report
        assert r.target is not None
        target_entry = by_id.get(r.target)
        if target_entry is not None and target_entry.lsn >= entry.lsn:
            target_entry = None  # a target must precede the report that acts on it
        if target_entry is None:
            return self._decision(entry, excl, AdmissionReason.TARGET_MISSING, Cue.ALLEGE)
        if r.cue is Cue.ALLEGE:
            return self._decision(entry, excl, AdmissionReason.AUTHORITY_FAILED, Cue.ALLEGE)

        if r.cue is Cue.CORRECT:
            auth = None
            if status is SourceStatus.NORMAL and r.origin in _ACTING_ORIGINS:
                auth = self.authz.check(r, Power.CORRECT, target_entry.report)
            base = self._evidence_decision(entry, status)
            withdraws = (r.target,) if (auth is not None and auth.allowed) else ()
            return AdmissionDecision(record=base.record, effective_cue=Cue.CORRECT, withdraws=withdraws, authority=auth)

        # withdraw / dispute: an operator action; a quarantined report acts on nothing
        if status is SourceStatus.QUARANTINED:
            return self._decision(entry, quar, AdmissionReason.SOURCE_QUARANTINED)
        if r.origin not in _ACTING_ORIGINS:
            return self._decision(entry, excl, AdmissionReason.ORIGIN_NOT_ADMISSIBLE)
        auth = self.authz.check(r, Power(r.cue.value), target_entry.report)
        if not auth.allowed:
            return self._decision(entry, excl, AdmissionReason.AUTHORITY_FAILED, Cue.ALLEGE, authority=auth)
        adm = AdmissionOutcome.ADMISSIBLE
        if r.cue is Cue.WITHDRAW:
            src = target_entry.report.source.id if auth.extent is Targets.SOURCE else None
            return self._decision(
                entry, adm, AdmissionReason.ADMITTED, Cue.WITHDRAW, withdraws=(r.target,), withdraws_source=src, authority=auth
            )
        return self._decision(entry, adm, AdmissionReason.ADMITTED, Cue.DISPUTE, authority=auth)

    def _withdrawals(
        self, entries: Sequence[LogEntry], base: Mapping[str, AdmissionDecision]
    ) -> dict[str, Withdrawal]:
        actors = [e for e in entries if base[_rid(e)].withdraws or base[_rid(e)].withdraws_source]
        if self.config.must_be_live:
            actors.sort(key=lambda e: e.lsn, reverse=True)
        withdrawn: dict[str, Withdrawal] = {}
        for a in actors:
            rid = _rid(a)
            if self.config.must_be_live and rid in withdrawn:
                continue  # a withdrawn actor no longer acts: its target is restored
            d = base[rid]
            kind = "self_correction" if a.report.cue is Cue.CORRECT else "withdraw"
            for t in d.withdraws:
                withdrawn.setdefault(t, Withdrawal(by=rid, kind=kind))
            if d.withdraws_source is not None:
                for e in entries:
                    if e.report.source.id == d.withdraws_source:
                        withdrawn.setdefault(_rid(e), Withdrawal(by=rid, kind="source_withdraw"))
        return withdrawn

    def _evaluate(self, entries: list[LogEntry], as_of: int | None) -> Evaluation:
        entries = sorted(entries, key=lambda e: e.lsn)
        by_id = {_rid(e): e for e in entries}
        base = {_rid(e): self._decide(e, by_id) for e in entries}
        withdrawn = self._withdrawals(entries, base)

        # derived confirmation: only reports admissible on their own merits, not withdrawn, confirm
        confirmers = [e for e in entries if base[_rid(e)].own_merit and _rid(e) not in withdrawn and e.report.cue in EVIDENCE_CUES]
        decisions: dict[str, AdmissionDecision] = dict(base)
        for e in entries:
            rid = _rid(e)
            d = base[rid]
            r = e.report
            if d.record.outcome is not AdmissionOutcome.QUARANTINED or r.proposition is None or r.cue not in EVIDENCE_CUES:
                continue
            conf = tuple(
                sorted(
                    _rid(c)
                    for c in confirmers
                    if c.report.key == r.key
                    and c.report.origin_group != r.origin_group
                    and c.report.proposition is not None
                    and equivalent(c.report.proposition, r.proposition)
                )
            )
            if conf:
                decisions[rid] = AdmissionDecision(
                    record=self._record(e, AdmissionOutcome.ADMISSIBLE, AdmissionReason.CONFIRMED, conf),
                    effective_cue=d.effective_cue,
                    withdraws=(),  # evidence, not authority
                    authority=d.authority,
                )
        return Evaluation(
            admission_version=self.config.admission_version,
            as_of_lsn=as_of,
            entries=tuple(entries),
            decisions=MappingProxyType(decisions),
            withdrawn=MappingProxyType(withdrawn),
        )


def _rid(e: LogEntry) -> str:
    rid = e.report.id
    assert rid is not None
    return rid
