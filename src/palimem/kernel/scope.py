"""The scope of the polarity kernel as a pure check over log entries (no kernel imports).

Negative evidence (``not_value`` / ``not_member``, ruling 4 of 2026-10-05) is justified by :mod:`palimem.kernel.polarity`
only for a small, precisely stated class of keys. This module states that class once, over the entries alone, so the
kernel (which refuses what is outside it) and admission (which must not turn an authorised dispute into a denial the kernel
would refuse) apply the same test.
"""

from __future__ import annotations

from collections.abc import Sequence

from palimem.types import (
    Cue,
    LogEntry,
    MemberProp,
    NotMemberProp,
    NotValueProp,
    Precision,
    ValueProp,
)


def polarity_scope_problem(entries: Sequence[LogEntry]) -> str | None:
    """Why the admitted evidence of one key (which includes a denial) is outside the polarity kernel's scope, or ``None``.

    Scope: ``assert`` cues only, no valid-time cues, day precision, one kind of proposition (single-valued or set-valued),
    a single-valued key with at most one distinct positive value, a set-valued key with at most one member both
    affirmed and denied.
    """
    pos: set[object] = set()
    neg: set[object] = set()
    single = multi = False
    for e in entries:
        r = e.report
        if r.cue is not Cue.ASSERT:
            return f"report {r.id}: cue {r.cue.value!r} beside negative evidence has no oracle yet"
        if r.valid_from is not None or r.valid_to is not None:
            return f"report {r.id}: valid-time cues beside negative evidence are staged (S-09)"
        if r.precision is not Precision.DAY:
            return f"report {r.id}: precision {r.precision.value!r} (S-09: day only in 0.1)"
        p = r.proposition
        if isinstance(p, ValueProp):
            single = True
            pos.add(p.value)
        elif isinstance(p, NotValueProp):
            single = True
            neg.add(p.value)
        elif isinstance(p, MemberProp):
            multi = True
            pos.add(p.value)
        elif isinstance(p, NotMemberProp):
            multi = True
            neg.add(p.value)
        else:
            return f"report {r.id}: proposition form {type(p).__name__} does not fit"
    if single and multi:
        return "a key mixes single-valued and set-valued propositions"
    if single and len(pos) > 1:
        return "competing positive values beside negative evidence have no oracle yet"
    if multi and len(pos & neg) > 1:
        return "more than one member both affirmed and denied has no oracle yet"
    return None
