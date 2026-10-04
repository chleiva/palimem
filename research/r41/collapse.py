"""Equivalence collapsing experiments: blocks of consecutive same-value, same-origin, cue-free reports."""
from __future__ import annotations

from . import oracle_bridge as ob
from .kernel import Rep


def blocks(reps: list[Rep], by_origin: bool = True) -> list[list[Rep]]:
    """Maximal runs, in anchor order, of reports with equal value (and origin when `by_origin`),
    no operator cue, not a correction and not corrected."""
    corrected = {r.op_of for r in reps if r.cue == "correction"}

    def plain(r: Rep) -> bool:
        return r.cue == "none" and r.id not in corrected

    out: list[list[Rep]] = []
    for r in sorted(reps, key=lambda x: (x.anchor, x.id)):
        if out and plain(r) and plain(out[-1][0]) and out[-1][0].value == r.value \
                and (not by_origin or out[-1][0].origin == r.origin):
            out[-1].append(r)
        else:
            out.append([r])
    return out


def answers(s, interps, ts) -> dict[int, set[frozenset]]:
    return {t: ob.oracle_cand(s, interps, t) for t in ts}


def raw_vs_tied(reps: list[Rep], policy: str):
    """Candidate sets over time for the raw instance and for 'tied' labels (every block entirely TRUE
    or entirely ERR). Returns (ts, raw, tied or None when no tied interpretation is admissible)."""
    ts = list(range(min(r.anchor for r in reps) - 1, max(r.anchor for r in reps) + 3))
    s, interps = ob.oracle_interps(reps, policy)
    block_ids = [frozenset(f"o{r.id}" for r in b) for b in blocks(reps, True)]
    tied = [it for it in interps if all(b <= it[0] or not (b & it[0]) for b in block_ids)]
    return ts, answers(s, interps, ts), (answers(s, tied, ts) if tied else None)


def reduced(reps: list[Rep], pick) -> list[Rep]:
    """Keep one report per block (`pick` chooses which), re-indexed."""
    kept = [pick(b) for b in blocks(reps, True)]
    return [Rep(i, r.value, r.anchor, r.origin, r.cue, r.op_from, r.op_of) for i, r in enumerate(kept)]
