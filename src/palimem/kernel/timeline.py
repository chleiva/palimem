"""Timelines (SEMANTICS §7), ported from the study's ``revise_stream.timeline``.

A timeline is the valid-time shape of one key under one interpretation: a tuple of value runs. A run
begins at an unknown change point in ``(start_lo, start]``, holds definitely on ``[start, end_lo]`` and
ends at an unknown change point in ``(end_lo, end_hi]``; ``end_lo == end_hi == inf`` means it persists
forever (law of inertia, A4).
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

from palimem.kernel.spec import AttrSpec
from palimem.types._codec import Value

INF = float("inf")


@dataclass(frozen=True, order=True)
class Run:
    value: Value
    start_lo: float
    start: int
    end_lo: float
    end_hi: float

    def covers(self, t: int) -> bool | None:
        """True = definitely covers t; False = definitely not; None = ambiguous."""
        if t < self.start:
            return None if self.start_lo < t else False
        if t <= self.end_lo:
            return True
        if t < self.end_hi:
            return None
        return False

    def breakpoints(self) -> set[int]:
        """Integer days at which this run's coverage of t can change (the conditions of ``covers``)."""
        out: set[int] = {self.start}
        for x, shift in ((self.start_lo, 1), (self.end_lo, 1), (self.end_hi, 0)):
            if x != INF and x != -INF:
                out.add(int(x) + shift)
        return out


Timeline = tuple[Run, ...]
Interp = tuple[frozenset[str], Timeline]  # (ERR report ids, timeline)


def observed_candidates(tl: Timeline, spec: AttrSpec, t: int) -> set[frozenset[Value]]:
    """Candidate value-sets for one observed key at valid time ``t`` under one timeline."""
    definite: set[Value] = set()
    ambiguous: set[Value] = set()
    for run in tl:
        c = run.covers(t)
        if c is True:
            definite.add(run.value)
        elif c is None:
            ambiguous.add(run.value)
    ambiguous -= definite
    if spec.cardinality == "single":
        # at most one value holds; inside a gap the value is v_prev or v_next, never both
        if definite:
            return {frozenset([v]) for v in definite}
        if ambiguous:
            return {frozenset([v]) for v in ambiguous}
        return {frozenset()}
    out: set[frozenset[Value]] = set()
    amb = sorted(ambiguous, key=str)
    for mask in product([False, True], repeat=len(amb)):
        s = set(definite) | {v for v, m in zip(amb, mask, strict=True) if m}
        out.add(frozenset(s))
    return out
