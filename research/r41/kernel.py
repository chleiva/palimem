"""R4.1 spike: exact fast per-key kernel for single-valued, changeable keys.

Instead of enumerating the 2^n TRUE/ERR labellings of oracle_v1._key_interps, this
decides *existence queries* over admissible TRUE sets T (a report is ERR iff it is
not in T). Every answer the study consumes from a single-valued observed key is such
a query:

  * cand(t)         union over interpretations of the candidate value-sets at valid time t
  * erroneous(o)    exists admissible T with o in T / exists admissible T with o not in T
  * changed(v1,v2)  exists admissible T with a v1 run followed by a later v2 run

Structure used (derivation in docs/research/R41_MEMO.md):

  T is admissible iff
    (C1) every o not in T is *explained*: some p in T has E(p, o), where
           E(p, o) = p is a correction targeting o
                  or (value differs, competing_values, and Q(o, p))
           Q(o, p) = p.anchor >= o.anchor                          if o's cue shields (P0c: change)
                   = p.origin != o.origin or p.anchor >= o.anchor   under A-SU (P0cSU)
                   = True                                           otherwise
         (SU's extra "not self-superseded" test is implied by Q; see memo)
    (C2) no correction in T targets a member of T
    (C3) each change-from report p in T has an earlier report of the 'from' value in T,
         whenever any such earlier report exists in the log
    (C4) no two different values share an anchor inside T
  C1 is monotone in T; C3 has a unique greatest solution inside any allowed set
  (one anchor-ordered pass). So for a fixed choice of (which corrections are in T,
  which value wins each conflicted anchor) the maximal T is computed in O(n) and
  decides feasibility of any "must contain I, must avoid X" query. The branching is
  over corrections and over same-anchor value conflicts only.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from itertools import product

POLICIES = {                    # name -> (shielding cues, self-update rule on)
    "P0": ((), False),
    "P0c": (("change",), False),
    "P0cc": (("change", "correction"), False),
    "P0cSU": (("change",), True),
}
NEG_INF = float("-inf")


@dataclass(frozen=True)
class Rep:
    id: int
    value: object
    anchor: int
    origin: str = "g0"
    cue: str = "none"            # none | change | correction
    op_from: object = None       # change: previous value
    op_of: int | None = None  # correction: target report id


class KeyKernel:
    """Existence-query kernel for one single-valued changeable key at one belief time."""

    def __init__(self, reps: Iterable[Rep], policy: str = "P0c", relax: int = 0,
                 error_allowed: bool = True, competing: bool = True):
        self.reps: list[Rep] = sorted(reps, key=lambda r: (r.anchor, r.id))
        self.by_id: dict[int, Rep] = {r.id: r for r in self.reps}
        self.shield, self.su = POLICIES[policy]
        self.relax = relax
        self.error_allowed = error_allowed
        self.competing = competing
        self.anchors: list[int] = sorted({r.anchor for r in self.reps})
        self.at: dict[int, list[Rep]] = {}
        for r in self.reps:
            self.at.setdefault(r.anchor, []).append(r)
        # C3 supports: id -> ids of earlier reports carrying the 'from' value (absent = no requirement)
        self.support: dict[int, list[int]] = {}
        if relax == 0:
            for p in self.reps:
                if p.cue == "change" and p.op_from is not None:
                    prev = [q.id for q in self.reps if q.value == p.op_from and q.anchor < p.anchor and q.id != p.id]
                    if prev:
                        self.support[p.id] = prev
        self.corrections: list[Rep] = [r for r in self.reps if r.cue == "correction" and r.op_of in self.by_id]
        self.n_branches = 0
        self._memo: dict[tuple, bool] = {}

    # ------------------------------------------------------------------ core decision
    def feasible(self, include: Iterable[int] = (), exclude: Iterable[int] = (),
                 include_any: Iterable[Iterable[int]] = ()) -> bool:
        """Is there an admissible T with include <= T, T disjoint from exclude, and T meeting each set in
        include_any (each such set holds reports at one anchor with one value)?"""
        inc, exc = frozenset(include), frozenset(exclude)
        anys = frozenset(frozenset(s) for s in include_any)
        key = (inc, exc, anys)
        if key not in self._memo:
            self._memo[key] = self._feasible(inc, exc, anys)
        return self._memo[key]

    def _feasible(self, inc: frozenset[int], exc: frozenset[int], anys: frozenset[frozenset[int]]) -> bool:
        if inc & exc:
            return False
        if any(not (s - exc) for s in anys):
            return False
        base = [r for r in self.reps if r.id not in exc]
        # anchors whose surviving reports disagree on value: branch on the winning value
        conflicts: list[tuple[int, list[object]]] = []
        forced: dict[int, object] = {}
        for i in list(inc) + [j for s in anys for j in s]:
            r = self.by_id[i]
            if r.anchor in forced and forced[r.anchor] != r.value:
                return False
            forced[r.anchor] = r.value
        for a in self.anchors:
            vals = sorted({r.value for r in base if r.anchor == a}, key=repr)
            if len(vals) > 1:
                conflicts.append((a, [forced[a]] if a in forced else vals))
        free_corr = [c for c in self.corrections if c.id not in exc]
        corr_choices = [(True,) if c.id in inc else (True, False) for c in free_corr]
        for vchoice in product(*[v for _, v in conflicts]):
            win = {a: v for (a, _), v in zip(conflicts, vchoice)}
            for cchoice in product(*corr_choices):
                self.n_branches += 1
                if self._branch(inc, anys, base, win, free_corr, cchoice):
                    return True
        return False

    def _branch(self, inc, anys, base, win, free_corr, cchoice) -> bool:
        chosen = {c.id for c, on in zip(free_corr, cchoice) if on}
        dropped_corr = {c.id for c, on in zip(free_corr, cchoice) if not on}
        killed = {self.by_id[c].op_of for c in chosen}
        if chosen & killed:
            return False
        allowed = [r for r in base
                   if r.id not in dropped_corr and r.id not in killed and win.get(r.anchor, r.value) == r.value]
        kept: set[int] = set()
        for r in allowed:                                       # ascending anchor: supports are strictly earlier
            sup = self.support.get(r.id)
            if sup is None or any(q in kept for q in sup):
                kept.add(r.id)
        if not inc <= kept or not chosen <= kept or any(not (s & kept) for s in anys):
            return False
        return self._dominating(kept, chosen)

    def _dominating(self, kept: set[int], chosen_corr: set[int]) -> bool:
        rest = [r for r in self.reps if r.id not in kept]
        if not rest:
            return True
        if not self.error_allowed:
            return False
        cnt_v: dict[object, int] = {}
        cnt_g: dict[str, int] = {}
        cnt_vg: dict[tuple[object, str], int] = {}
        best: tuple[float, object] = (NEG_INF, None)             # (anchor, value) of the latest kept report
        n = 0
        for i in kept:
            r = self.by_id[i]
            n += 1
            cnt_v[r.value] = cnt_v.get(r.value, 0) + 1
            cnt_g[r.origin] = cnt_g.get(r.origin, 0) + 1
            cnt_vg[(r.value, r.origin)] = cnt_vg.get((r.value, r.origin), 0) + 1
            if r.anchor > best[0]:
                best = (r.anchor, r.value)
        second = NEG_INF                                         # latest anchor among kept with value != best value
        for i in kept:
            r = self.by_id[i]
            if r.value != best[1] and r.anchor > second:
                second = r.anchor
        corrected = {self.by_id[c].op_of for c in chosen_corr}
        for o in rest:
            if o.id in corrected:
                continue
            if not self.competing:
                return False
            latest_diff = best[0] if o.value != best[1] else second
            if o.cue in self.shield:
                ok = latest_diff >= o.anchor
            elif self.su:
                diff_origin = n - cnt_v.get(o.value, 0) - cnt_g.get(o.origin, 0) + cnt_vg.get((o.value, o.origin), 0)
                ok = diff_origin > 0 or latest_diff >= o.anchor
            else:
                ok = n - cnt_v.get(o.value, 0) > 0
            if not ok:
                return False
        return True

    def any_admissible(self) -> bool:
        return self.feasible() if self.reps else True

    # ------------------------------------------------------------------ query answers
    def feasible_pairs(self) -> set[tuple[int, object, int, object]]:
        """All (p, u, q, w): an admissible T whose last report at anchors[p] has value u and whose
        next report (at anchors[q], value w) follows with nothing in between.
        p == -1: no report before; q == len(anchors): no report after."""
        m = len(self.anchors)
        vals_at = [sorted({r.value for r in self.at[a]}, key=repr) for a in self.anchors]
        out: set[tuple[int, object, int, object]] = set()
        for p in range(-1, m):
            for q in range(p + 1, m + 1):
                if p == -1 and q == m:
                    continue
                lo = self.anchors[p] if p >= 0 else NEG_INF
                hi = self.anchors[q] if q < m else float("inf")
                window = [r.id for r in self.reps if lo < r.anchor < hi]
                for u in (vals_at[p] if p >= 0 else [None]):
                    for w in (vals_at[q] if q < m else [None]):
                        anys = []
                        if p >= 0:
                            anys.append([r.id for r in self.at[self.anchors[p]] if r.value == u])
                        if q < m:
                            anys.append([r.id for r in self.at[self.anchors[q]] if r.value == w])
                        # other-valued reports at the boundary anchors cannot coexist with the chosen
                        # value: feasible() enforces that through the same-anchor conflict branching
                        if self.feasible((), window, anys):
                            out.add((p, u, q, w))
        return out

    def candidates_all(self, ts: Iterable[int]) -> dict[int, set[frozenset]]:
        pairs = self.feasible_pairs()
        m = len(self.anchors)
        res: dict[int, set[frozenset]] = {}
        for t in ts:
            idx = -1
            for k, a in enumerate(self.anchors):
                if a <= t:
                    idx = k
            cands: set[frozenset] = set()
            for (p, u, q, w) in pairs:
                if p <= idx < q:
                    if p == -1:
                        cands.add(frozenset())
                    elif self.anchors[p] == t or q == m or u == w:
                        cands.add(frozenset([u]))
                    else:
                        cands.add(frozenset([u]))
                        cands.add(frozenset([w]))
            res[t] = cands
        return res

    def erroneous(self, rid: int) -> tuple[bool, bool]:
        """(some admissible T has the report TRUE, some admissible T has it ERR)."""
        return self.feasible([rid]), self.feasible([], [rid])

    def changed(self, v1, v2) -> tuple[bool, bool]:
        """(some interpretation has a v1 run followed by a later v2 run, some interpretation does not)."""
        yes = any(self.feasible([p.id, q.id]) for p in self.reps if p.value == v1
                  for q in self.reps if q.value == v2 and q.anchor > p.anchor)
        return yes, self._exists_without_pair(v1, v2)

    def _exists_without_pair(self, v1, v2) -> bool:
        """Exists admissible T with no v1 report followed by a later v2 report?
        T avoids the pattern iff it has no v1, or no v2, or every v2 precedes-or-ties every v1,
        i.e. there is a cut with v1 only before... (v2 at anchors <= cut-side, v1 at anchors >= cut)."""
        ps = [p.id for p in self.reps if p.value == v1]
        qs = [q.id for q in self.reps if q.value == v2]
        if self.feasible([], ps) or self.feasible([], qs):
            return True
        for cut in self.anchors:
            # v2 only at anchors < cut (strictly earlier than every kept v1, which sit at >= cut)
            ex = [p.id for p in self.reps if p.value == v1 and p.anchor < cut] + \
                 [q.id for q in self.reps if q.value == v2 and q.anchor >= cut]
            if self.feasible([], ex):
                return True
        return False


def with_ladder(reps: list[Rep], policy: str, error_allowed: bool = True,
                competing: bool = True) -> tuple[KeyKernel | None, int]:
    """SEMANTICS §6 totality ladder. Returns (kernel, level); kernel None at level 3 (reject all)."""
    if not reps:
        return KeyKernel([], policy, 0, error_allowed, competing), 0
    for level, (pol, rl) in enumerate(((policy, 0), (policy, 1), ("P0", 1))):
        k = KeyKernel(reps, pol, rl, error_allowed, competing)
        if k.any_admissible():
            return k, level
    return None, 3
