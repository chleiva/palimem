"""Evidence: what the kernel reasons over, derived from admitted log entries.

The kernel never sees raw reports. Admission (not this package) decides which log entries are
admissible and applies withdrawals, so the input is the *admitted* entries of one key. Each is reduced
to an :class:`Ev`: id, anchor day, value, operator cue and origin group.

Time. The kernel works at day granularity (S-09 stages partial dates out of 0.1): the *anchor* of a
report is the day of ``valid_from`` when the source gave one, otherwise the day of the log's
``recorded_at`` (assumption A1: a report with no temporal cue asserts the value held at least when it was
reported). Days are counted from :data:`EPOCH`.

Contract gap, reported to the author: ``Report`` has no field for a ``change`` cue's *from* value
(``op_from`` in the paper, present on 3,593 of the 7,135 change reports of Setting 1). A-CHG needs it, so
the kernel accepts it out of band as ``change_from: {report id -> previous value}``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from palimem.kernel.spec import KernelUnsupported
from palimem.types import Cue, LogEntry, MemberProp, Origin, Precision, ValueProp
from palimem.types._codec import Value

EPOCH = date(2026, 1, 1)
_EPOCH_DT = datetime(2026, 1, 1, tzinfo=UTC)


def day_of(dt: datetime) -> int:
    """Day ordinal of a timestamp (UTC date, days since :data:`EPOCH`)."""
    return (dt.astimezone(UTC).date() - EPOCH).days


def dt_of_day(day: int) -> datetime:
    """Midnight UTC of a day ordinal."""
    return _EPOCH_DT + timedelta(days=day)


@dataclass(frozen=True, eq=False)
class Ev:
    id: str
    anchor: int
    value: Value
    op_cue: str  # "none" | "change" | "correction"
    op_from: Value | None
    op_of: str | None
    origin_group: str
    lsn: int


def evidence_from_entries(entries: Sequence[LogEntry], change_from: Mapping[str, Value] | None = None) -> list[Ev]:
    """Reduce admitted log entries of ONE key to kernel evidence (log order)."""
    out: list[Ev] = []
    for e in entries:
        r = e.report
        assert r.id is not None  # LogEntry guarantees it
        if r.origin is not Origin.EXTERNAL_OBSERVATION:
            raise ValueError(f"report {r.id}: only admissible external observations reach the kernel")
        if r.precision is not Precision.DAY:
            raise KernelUnsupported(f"report {r.id}: precision {r.precision.value!r} (S-09: day only in 0.1)")
        if r.valid_to is not None:
            raise KernelUnsupported(f"report {r.id}: valid_to / interval cues have no oracle (S-09)")
        if not isinstance(r.proposition, ValueProp | MemberProp):
            raise KernelUnsupported(f"report {r.id}: only value/member propositions are in scope (no oracle for negative evidence)")
        if r.cue is Cue.ASSERT:
            op_cue = "none"
        elif r.cue is Cue.CHANGE:
            op_cue = "change"
        elif r.cue is Cue.CORRECT:
            op_cue = "correction"
        else:
            raise ValueError(f"report {r.id}: cue {r.cue.value!r} is resolved by admission, not by the kernel")
        anchor = day_of(r.valid_from) if r.valid_from is not None else day_of(e.recorded_at)
        out.append(
            Ev(
                id=r.id,
                anchor=anchor,
                value=r.proposition.value,
                op_cue=op_cue,
                op_from=(change_from or {}).get(r.id) if op_cue == "change" else None,
                op_of=r.target if op_cue == "correction" else None,
                origin_group=r.origin_group,
                lsn=e.lsn,
            )
        )
    return out


def cross_key_corrections(entries: Sequence[LogEntry]) -> list[str]:
    """Ids of correction reports whose target lies on another key.

    The kernel's exactness rests on every admissibility relation being same-key (design v0.3,
    Conceptual model). Admission uses this to refuse or route such reports."""
    keys = {e.report.id: e.report.key for e in entries}
    return [
        e.report.id
        for e in entries
        if e.report.id is not None
        and e.report.cue is Cue.CORRECT
        and e.report.target in keys
        and keys[e.report.target] != e.report.key
    ]
