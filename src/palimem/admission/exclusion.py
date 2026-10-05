"""``exclude_source``: a source going bad is an *admission* operation, not a report cue (author ruling 2 of 2026-10-05).

The contract has no source-scope ``withdraw`` (a ``withdraw`` Report targets one report id, and giving it a wider extent
would put a source-wide power on the write path an attacker reaches). When a source turns out to be compromised the host
takes an admission decision instead: ``exclude_source(source, from_lsn, reason)``. From log position ``from_lsn`` on, the
source's reports are not heard: they leave the kernel's evidence set, whether they were logged before the decision or are
logged after it (the paper's "source retraction also removes later assertions", the 85-query ``late-assert`` class that
per-report withdraws cannot reach, because a report that does not exist yet has no id to target).

How it is recorded. Like an entity merge (``palimem.entities``), the decision is a *marker report* on a reserved attribute:
an assertion ``__source_exclusion__(<source id>) = {"op", "from_lsn", "reason"}`` written by a ``system:`` or ``user:``
principal. It therefore lives in the hash-chained, idempotent, crash-safe, exportable log, carries its own admission record
(reason ``admitted`` and the admission version in force) and is audit-visible with its reason text. It is **not** a new
cue and not a ``Report`` field. A marker from any other principal kind (an agent, a connector) is logged but not honoured.

What it does. :class:`ExclusionAdmitter` adds the excluded reports to the evaluation's withdrawal map with
``Withdrawal(kind="source_exclusion", by=<marker id>)``, so a source exclusion is *repaired like a withdrawal*: the revision
of the touched keys and of everything derived from them is the one every withdrawal triggers, and a report excluded this way
neither confirms a quarantined report nor corroborates. Reports stay in the log and in their admission records.

Reversible. A later ``restore`` marker with ``from_lsn = F`` lifts the exclusion for the source's reports at or after ``F``:
for each report the *last* marker (by marker position) whose ``from_lsn`` is at most the report's position decides. A restore
is itself a recorded decision; nothing is rewritten.

Not touched here. An excluded source's own operator actions on its own reports (a self-correction, a self-withdrawal) are
moot, since those reports are not heard; its actions on other sources' reports are refused by the authority rules anyway
(default: own source only). The compat profile's source retraction (the paper's behaviour, ``__source_status__``) is a
separate mechanism and is unchanged.

The ``from_lsn`` and the reason text are in the marker's proposition (canonical JSON), so a historical ``belief_as_of`` query
re-derives exactly the exclusions that were in force then, from the log prefix alone.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING

from palimem.types import (
    AdmissionOutcome,
    Attr,
    AttrClass,
    Cue,
    Key,
    LogEntry,
    Origin,
    PrincipalKind,
    Report,
    Schema,
    Source,
    ValidationError,
    ValueProp,
    ValueType,
    principal_kind,
)

from .admitter import Admitter, Evaluation, Withdrawal

if TYPE_CHECKING:
    from .incremental import IncrementalAdmission

SOURCE_EXCLUSION_ATTR = "__source_exclusion__"
OP_EXCLUDE = "exclude"
OP_RESTORE = "restore"
EXCLUSION_KIND = "source_exclusion"
HOST_SOURCE = Source(id="host", cls="trusted")
HOST_PRINCIPALS = frozenset({PrincipalKind.SYSTEM, PrincipalKind.USER})


class ExclusionNotEnabled(ValueError):
    """The schema does not declare the reserved ``__source_exclusion__`` attribute (see :func:`enable_source_exclusions`)."""


@dataclass(frozen=True)
class Exclusion:
    """One honoured decision: ``op`` applies to the source's reports at or after ``from_lsn``."""

    marker_id: str
    marker_lsn: int
    op: str
    from_lsn: int
    reason: str


def enable_source_exclusions(schema: Schema) -> Schema:
    """A copy of a contract ``Schema`` that declares the reserved attribute (version bumped by one)."""
    if any(a.name == SOURCE_EXCLUSION_ATTR for a in schema.attrs):
        return schema
    # inertia=True: the kernel refuses inertia=False (S-08); harmless for a set of marker texts
    attr = Attr(name=SOURCE_EXCLUSION_ATTR, attr_class=AttrClass.MULTI_SET, value_type=ValueType.STRING, inertia=True)
    return replace(schema, version=schema.version + 1, attrs=(*schema.attrs, attr))


def exclusion_attr_spec() -> object:
    """The kernel's attribute spec of the reserved attribute (a set of marker texts, no inertia)."""
    from palimem.kernel import AttrSpec

    return AttrSpec(SOURCE_EXCLUSION_ATTR, "multi", False, competing_values=False)


def marker_text(op: str, from_lsn: int, reason: str) -> str:
    if op not in (OP_EXCLUDE, OP_RESTORE):
        raise ValidationError(f"source exclusion: op must be {OP_EXCLUDE!r} or {OP_RESTORE!r}, got {op!r}")
    if not isinstance(from_lsn, int) or isinstance(from_lsn, bool) or from_lsn < 1:
        raise ValidationError("source exclusion: from_lsn must be an integer >= 1")
    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError("source exclusion: a reason is required (it is what an audit asks about first)")
    return json.dumps({"op": op, "from_lsn": from_lsn, "reason": reason}, sort_keys=True, separators=(",", ":"))


def exclusion_report(
    source_id: str, *, op: str = OP_EXCLUDE, from_lsn: int = 1, reason: str, actor: str = "system:admission",
    source: Source = HOST_SOURCE,
) -> Report:
    """The marker report of one exclusion (or restore) decision. ``actor`` must be a ``system:`` or ``user:`` principal
    to be honoured."""
    return Report(
        key=Key(entity=source_id, attr=SOURCE_EXCLUSION_ATTR), cue=Cue.ASSERT,
        proposition=ValueProp(value=marker_text(op, from_lsn, reason)),
        source=source, origin=Origin.EXTERNAL_OBSERVATION, origin_group="system:admission", actor=actor,
    )


def parse_marker(entry: LogEntry) -> Exclusion | None:
    """The decision a marker entry carries, or ``None`` if it is not a well-formed marker."""
    r = entry.report
    if r.key.attr != SOURCE_EXCLUSION_ATTR or r.cue is not Cue.ASSERT or not isinstance(r.proposition, ValueProp):
        return None
    v = r.proposition.value
    if not isinstance(v, str) or r.id is None:
        return None
    try:
        o = json.loads(v)
        op, frm, reason = o["op"], o["from_lsn"], o["reason"]
    except (ValueError, KeyError, TypeError):
        return None
    if op not in (OP_EXCLUDE, OP_RESTORE) or not isinstance(frm, int) or isinstance(frm, bool) or frm < 1:
        return None
    if not isinstance(reason, str):
        return None
    return Exclusion(marker_id=r.id, marker_lsn=entry.lsn, op=op, from_lsn=frm, reason=reason)


def _honoured(entry: LogEntry, admitted: bool, withdrawn: bool) -> Exclusion | None:
    ex = parse_marker(entry)
    if ex is None or not admitted or withdrawn:
        return None
    if principal_kind(entry.report.actor) not in HOST_PRINCIPALS:
        return None  # logged, never honoured: only the host decides which sources are heard
    return ex


def excluding_marker(markers: Sequence[Exclusion], lsn: int) -> Exclusion | None:
    """The marker that excludes a report at log position ``lsn``: the last one (by marker position) among those whose
    ``from_lsn`` is at most ``lsn``, if it is an exclusion."""
    last: Exclusion | None = None
    for m in markers:  # ascending marker position
        if m.from_lsn <= lsn:
            last = m
    return last if last is not None and last.op == OP_EXCLUDE else None


class ExclusionAdmitter(Admitter):
    """The product admitter: the base rules plus honoured source exclusions.

    Overrides the base class's internal ``_evaluate`` hook, and provides the two overlay hooks incremental admission needs
    (``incremental_overlay_trigger`` / ``incremental_overlay``), which compute exactly what ``_evaluate`` computes from the
    base withdrawals (the equivalence suite compares them append by append).
    """

    def _evaluate(self, entries: list[LogEntry], as_of: int | None) -> Evaluation:
        ev = super()._evaluate(entries, as_of)
        by_source: dict[str, list[Exclusion]] = {}
        for e in ev.entries:
            rid = e.report.id
            assert rid is not None
            if e.report.key.attr != SOURCE_EXCLUSION_ATTR:
                continue
            admitted = ev.decisions[rid].record.outcome is AdmissionOutcome.ADMISSIBLE
            ex = _honoured(e, admitted, rid in ev.withdrawn)
            if ex is not None:
                by_source.setdefault(e.report.key.entity, []).append(ex)
        if not by_source:
            return ev
        withdrawn = dict(ev.withdrawn)
        for e in ev.entries:
            rid = e.report.id
            assert rid is not None
            ms = by_source.get(e.report.source.id)
            if ms is None or e.report.key.attr == SOURCE_EXCLUSION_ATTR:
                continue
            m = excluding_marker(ms, e.lsn)
            if m is not None:
                withdrawn.setdefault(rid, Withdrawal(by=m.marker_id, kind=EXCLUSION_KIND))
        return Evaluation(
            admission_version=ev.admission_version, as_of_lsn=ev.as_of_lsn, entries=ev.entries,
            decisions=ev.decisions, withdrawn=MappingProxyType(withdrawn),
        )

    # -- the same rule for incremental admission (docs/PIPELINE.md)

    @staticmethod
    def _markers(inc: IncrementalAdmission, base_withdrawn: Mapping[str, Withdrawal]) -> dict[str, list[Exclusion]]:
        out: dict[str, list[Exclusion]] = {}
        for e in inc.by_attr.get(SOURCE_EXCLUSION_ATTR, ()):
            rid = e.report.id
            assert rid is not None
            admitted = inc.decision(rid).record.outcome is AdmissionOutcome.ADMISSIBLE
            ex = _honoured(e, admitted, rid in base_withdrawn)
            if ex is not None:
                out.setdefault(e.report.key.entity, []).append(ex)
        return out

    def incremental_overlay_trigger(self, inc: IncrementalAdmission, entry: LogEntry) -> bool:
        """May this append change the exclusion overlay? A marker, or a report of a source with an honoured marker."""
        if entry.report.key.attr == SOURCE_EXCLUSION_ATTR:
            return True
        if not inc.by_attr.get(SOURCE_EXCLUSION_ATTR):
            return False
        return entry.report.source.id in self._markers(inc, inc.withdrawn_base)

    def incremental_overlay(self, inc: IncrementalAdmission, base_withdrawn: Mapping[str, Withdrawal]) -> dict[str, Withdrawal]:
        out = dict(base_withdrawn)
        for src, ms in self._markers(inc, base_withdrawn).items():
            for x in inc.by_source.get(src, ()):
                if x.report.key.attr == SOURCE_EXCLUSION_ATTR:
                    continue
                m = excluding_marker(ms, x.lsn)
                if m is not None:
                    rid = x.report.id
                    assert rid is not None
                    out.setdefault(rid, Withdrawal(by=m.marker_id, kind=EXCLUSION_KIND))
        return out


def exclusions_of(entries: Sequence[LogEntry]) -> dict[str, list[Exclusion]]:
    """The well-formed markers in a log prefix by source (audit view; admission decides which are honoured)."""
    out: dict[str, list[Exclusion]] = {}
    for e in entries:
        ex = parse_marker(e)
        if ex is not None:
            out.setdefault(e.report.key.entity, []).append(ex)
    return out


# --------------------------------------------------------------------------- host API (the admission operation)


def _require_enabled(mem: object) -> None:
    schema = mem.schema  # type: ignore[attr-defined]
    if not any(a.name == SOURCE_EXCLUSION_ATTR for a in schema.attrs):
        raise ExclusionNotEnabled(
            f"declare the reserved attribute {SOURCE_EXCLUSION_ATTR!r} (enable_source_exclusions) before excluding sources"
        )


def exclude_source(
    mem: object, source_id: str, from_lsn: int, reason: str, *, actor: str = "system:admission",
    idempotency_key: str | None = None,
) -> object:
    """Exclude ``source_id`` from log position ``from_lsn`` on (host API; an admission operation, not a report cue).

    Records the decision as a marker report and repairs everything that rested on the source like a withdrawal. Returns
    the :class:`~palimem.store.AppendResult` of the marker append. ``actor`` must be a ``system:`` or ``user:`` principal,
    otherwise the marker is logged but not honoured (:class:`ExclusionNotHonoured` is raised so the caller notices).
    """
    return _decide(mem, OP_EXCLUDE, source_id, from_lsn, reason, actor, idempotency_key)


def restore_source(
    mem: object, source_id: str, from_lsn: int, reason: str, *, actor: str = "system:admission",
    idempotency_key: str | None = None,
) -> object:
    """Lift an earlier exclusion for the source's reports at or after ``from_lsn`` (a later recorded decision)."""
    return _decide(mem, OP_RESTORE, source_id, from_lsn, reason, actor, idempotency_key)


class ExclusionNotHonoured(ValueError):
    """The marker was logged but is not honoured (not from a host principal, or not admitted)."""


def _decide(
    mem: object, op: str, source_id: str, from_lsn: int, reason: str, actor: str, idempotency_key: str | None
) -> object:
    _require_enabled(mem)
    if principal_kind(actor) not in HOST_PRINCIPALS:
        raise ExclusionNotHonoured(
            f"{actor!r} is not a system: or user: principal; only the host decides which sources are heard (nothing was logged)"
        )
    if not isinstance(source_id, str) or not source_id:
        raise ValidationError("source exclusion: a source id is required")
    rep = exclusion_report(source_id, op=op, from_lsn=from_lsn, reason=reason, actor=actor)
    res = mem.append(rep, idempotency_key=idempotency_key)  # type: ignore[attr-defined]
    return res


def source_exclusions(mem: object, source_id: str | None = None) -> list[Exclusion]:
    """The exclusion decisions in the log (audit view), oldest first: every well-formed marker, honoured or not by the
    admission version in force; the reason texts are in the markers."""
    out: list[Exclusion] = []
    for row in mem.backend.scan():  # type: ignore[attr-defined]
        if isinstance(row, LogEntry) and row.report.key.attr == SOURCE_EXCLUSION_ATTR:
            if source_id is not None and row.report.key.entity != source_id:
                continue
            ex = parse_marker(row)
            if ex is not None:
                out.append(ex)
    return out
