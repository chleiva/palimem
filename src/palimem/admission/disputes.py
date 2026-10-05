"""An authorised ``dispute`` in the product profile (author ruling 3 of 2026-10-05).

A dispute is a recorded, authorised statement that a target report is wrong. In the product profile its kernel meaning is
**the target's candidate becomes ``unresolved`` against "disputed", with no value asserted, until confirmation from another
origin group or withdrawal**:

* **Read as a denial.** The kernel reads an active dispute of a report that asserts ``value(v)`` / ``member(m)`` as the
  denial ``not_value(v)`` / ``not_member(m)`` carrying the dispute's own report id and origin group (so the denial's
  environment is the dispute, and a withdrawal of the dispute repairs the key like any withdrawal). The polarity kernel
  (ruling 4) then answers ``unresolved`` with both readings, ``value(v)`` and ``not_value(v)``: nothing is asserted.
* **Until confirmation.** A dispute is *overridden* when an admitted, not withdrawn report on the same key with an
  equivalent proposition comes from an origin group other than the target's and the disputer's: independent corroboration
  outweighs one dispute. The key then reads as if the dispute were absent.
* **Until withdrawal.** The dispute is inactive once it is withdrawn, once its target is withdrawn or not admissible, and
  (as for every report) when its source is excluded.
* **Inert when the kernel cannot model it.** If the key's evidence with the denial would fall outside the polarity kernel's
  scope (a ``change`` cue, a valid-time interval, competing positive values, ...), the dispute has no kernel effect: it
  stays audit-visible in ``EvidenceSet.disputes`` and appending it never fails. Flagged to the author.
* **Compat profile unchanged** (``AdmissionConfig.dispute_denial`` is False there): the paper's oracle has no disputes.

Only a dispute whose own key is its target's key can be read this way (the kernel's exactness rests on same-key relations).
The log is never altered: the denial is a *view* (:func:`dispute_view`), exactly as ``kernel_view`` is for a failed
correction.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from palimem.admission.equivalence import equivalent
from palimem.kernel.scope import polarity_scope_problem
from palimem.types import (
    Cue,
    LogEntry,
    MemberProp,
    NotMemberProp,
    NotValueProp,
    Origin,
    ValueProp,
)


def dispute_view(dispute: LogEntry, target: LogEntry) -> LogEntry | None:
    """The denial the kernel reads for ``dispute`` of ``target`` (``None`` if the target's form cannot be denied)."""
    p = target.report.proposition
    if isinstance(p, ValueProp):
        denial: NotValueProp | NotMemberProp = NotValueProp(value=p.value)
    elif isinstance(p, MemberProp):
        denial = NotMemberProp(value=p.value)
    else:
        return None
    # an authorised dispute is an operator action the host granted (an agent's included), so the denial it stands for is
    # read as an external observation by the kernel, which accepts no other origin
    rep = replace(dispute.report, cue=Cue.ASSERT, proposition=denial, target=None, origin=Origin.EXTERNAL_OBSERVATION)
    return replace(dispute, report=rep)


def confirmed_elsewhere(direct: Sequence[LogEntry], target: LogEntry, dispute: LogEntry) -> bool:
    """Is the target corroborated by a report from an origin group other than its own and the disputer's?"""
    tp = target.report.proposition
    assert tp is not None
    for c in direct:
        cp = c.report.proposition
        if (
            c.report.id != target.report.id
            and cp is not None
            and c.report.origin_group not in (target.report.origin_group, dispute.report.origin_group)
            and equivalent(cp, tp)
        ):
            return True
    return False


def apply_disputes(direct: Sequence[LogEntry], disputes: Sequence[LogEntry]) -> list[LogEntry]:
    """The kernel's evidence for one key: ``direct`` (kernel views, log order) plus the denials of its active disputes.

    ``disputes`` are the authorised, admissible, not withdrawn dispute entries on this key (log order). Returns ``direct``
    unchanged when no dispute is active or the result would be outside the polarity kernel's scope."""
    if not disputes:
        return list(direct)
    by_id = {e.report.id: e for e in direct}
    denials: list[LogEntry] = []
    for d in disputes:
        t = by_id.get(d.report.target)
        if t is None or t.report.key != d.report.key or confirmed_elsewhere(direct, t, d):
            continue
        v = dispute_view(d, t)
        if v is not None:
            denials.append(v)
    if not denials:
        return list(direct)
    merged = sorted([*direct, *denials], key=lambda e: e.lsn)
    return merged if polarity_scope_problem(merged) is None else list(direct)
