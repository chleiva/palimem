"""T-B10: the candidate fast kernel (existence queries), NOT the production kernel.

The enumeration kernel (:mod:`palimem.kernel.interpret`) walks all 2^n TRUE/ERR labellings of a key's reports.
It is the production kernel through G1 and the permanent audit oracle (author decision 2026-10-04, S-06).
This module is a **candidate** to replace it for one class of keys, promoted only if the differential CI shows
identical answers (see ``docs/KERNEL.md``, "Candidate fast kernel"). It is a typed, productionised port of the
R4.1 spike (``research/r41/kernel.py``, ``docs/research/R41_MEMO.md``).

Proven class (what the fast path accepts)
-----------------------------------------
A **single-valued, changeable, observed** key, under P0c or P0cSU, whose admissible set is non-empty without
relaxation (totality-ladder level 0), with at most :data:`DEFAULT_FAST_LIMIT` reports and a branch bound under
:data:`DEFAULT_BRANCH_BUDGET`. Everything the study consumes from such a key is a *question about the family
of admissible TRUE sets T*, not the family itself:

* the candidate family at a valid time (union over admissible T of the candidate value-sets),
* ``erroneous(o)``: can some admissible T contain o, can some omit it,
* ``changed(v1, v2)``: does some admissible T have a v1 report followed by a later v2 report, does some not.

Admissibility is a monotone dominating-set condition (every report outside T is explained by one inside T), a
unique greatest solution of the change-from closure, and branching only over corrections and same-anchor value
conflicts. Cost: O(n^3) with no corrections and no tied anchors; exponential only in (#corrections + #tied
anchors), which :func:`branch_bound` checks *before* running.

Dispatch is explicit and never silent (:func:`dispatch_key`): outside the class (multi-valued, stable,
derived keys, ladder levels above 0, a branch bound over budget) the answer comes from the enumeration kernel
(``route == "enumeration"``) and, if that is over its own environment budget too, is
``ResourceLimitedResult(environment_budget)``.

What this kernel does NOT do
----------------------------
* Interpretations are not materialised. Anything that needs them (per-candidate supports, ``always_err_ids``)
  is produced by running the enumeration lazily **only when the key has at most ``enumeration_limit`` reports**
  (default: the environment budget). Above that, segments carry no ``support`` and the explanation is
  ``truncated`` (a bounded explanation, never a different status or value). So today the fast kernel is
  provenance-complete only inside the enumeration envelope; this is the main promotion gap.
* Multi-valued keys: the answer itself (alternatives as sets) is 2^n - 1 long; needs a factored contract (R4.1).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import product

from palimem.kernel.evidence import Ev, evidence_from_entries
from palimem.kernel.interpret import P0C, P0CSU, key_interpretations
from palimem.kernel.justify import (
    Family,
    Justification,
    ResourceLimitedResult,
    build_segments,
    explain_at,
    justify_key,
    policy_of,
    segment_at,
)
from palimem.kernel.provenance import (
    DEFAULT_ENV_CAP,
    Dist,
    EnvBudget,
    base_world_envs,
    oracle_flat_ids,
)
from palimem.kernel.spec import AttrSpec, KernelSchema, KernelUnsupported
from palimem.kernel.timeline import Interp
from palimem.types import (
    ExplainMode,
    Explanation,
    ExplanationState,
    Key,
    LogEntry,
    Profile,
    ResourceLimitedReason,
    SemanticConfig,
)
from palimem.types import Segment as PSegment
from palimem.types._codec import Value
from palimem.types.limits import DEFAULT_ENVIRONMENT_BUDGET

DEFAULT_FAST_LIMIT = 128
"""Maximum reports on a key for the fast path (cost grows as O(n^3); 100 reports take about a second)."""

DEFAULT_BRANCH_BUDGET = 1 << 14
"""Maximum number of (correction subset x same-anchor value choice) branches one feasibility test may need."""

NEG_INF = float("-inf")
INF = float("inf")


# --------------------------------------------------------------------------- the existence-query core


@dataclass(frozen=True)
class _Rep:
    id: int
    value: Value
    anchor: int
    origin: str
    cue: str  # none | change | correction
    op_from: Value | None
    op_of: int | None  # correction: target report index


class _KeyKernel:
    """Existence-query kernel for one single-valued changeable key at one belief point (policy P0c / P0cSU)."""

    def __init__(self, reps: Sequence[_Rep], *, self_update: bool, error_allowed: bool, competing: bool) -> None:
        self.reps: list[_Rep] = sorted(reps, key=lambda r: (r.anchor, r.id))
        self.by_id: dict[int, _Rep] = {r.id: r for r in self.reps}
        self.shield: tuple[str, ...] = ("change",)  # P0c and P0cSU both let a change cue shield against earlier anchors
        self.su = self_update
        self.error_allowed = error_allowed
        self.competing = competing
        self.anchors: list[int] = sorted({r.anchor for r in self.reps})
        self.at: dict[int, list[_Rep]] = {}
        for r in self.reps:
            self.at.setdefault(r.anchor, []).append(r)
        # A-CHG supports: id -> ids of earlier reports carrying the 'from' value (absent = no requirement)
        self.support: dict[int, list[int]] = {}
        for p in self.reps:
            if p.cue == "change" and p.op_from is not None:
                prev = [q.id for q in self.reps if q.value == p.op_from and q.anchor < p.anchor and q.id != p.id]
                if prev:
                    self.support[p.id] = prev
        self.corrections: list[_Rep] = [r for r in self.reps if r.cue == "correction" and r.op_of in self.by_id]
        self._memo: dict[tuple[frozenset[int], frozenset[int], frozenset[frozenset[int]]], bool] = {}
        self._pairs: set[tuple[int, Value | None, int, Value | None]] | None = None

    # ---- cost bound, checked before anything runs

    def branch_bound(self) -> int:
        """Upper bound on the branches one feasibility test enumerates."""
        bound = 1 << len(self.corrections)
        for a in self.anchors:
            distinct = len({r.value for r in self.at[a]})
            if distinct > 1:
                bound *= distinct
        return bound

    # ---- core decision

    def feasible(self, include: Iterable[int] = (), exclude: Iterable[int] = (),
                 include_any: Iterable[Iterable[int]] = ()) -> bool:
        """Is there an admissible T with ``include`` inside T, T disjoint from ``exclude`` and T meeting every
        set of ``include_any`` (each holds reports at one anchor with one value)?"""
        inc, exc = frozenset(include), frozenset(exclude)
        anys = frozenset(frozenset(s) for s in include_any)
        k = (inc, exc, anys)
        if k not in self._memo:
            self._memo[k] = self._feasible(inc, exc, anys)
        return self._memo[k]

    def _feasible(self, inc: frozenset[int], exc: frozenset[int], anys: frozenset[frozenset[int]]) -> bool:
        if inc & exc:
            return False
        if any(not (s - exc) for s in anys):
            return False
        base = [r for r in self.reps if r.id not in exc]
        conflicts: list[tuple[int, list[Value]]] = []
        forced: dict[int, Value] = {}
        for i in [*inc, *(j for s in anys for j in s)]:
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
            win = {a: v for (a, _), v in zip(conflicts, vchoice, strict=True)}
            for cchoice in product(*corr_choices):
                if self._branch(inc, anys, base, win, free_corr, cchoice):
                    return True
        return False

    def _branch(self, inc: frozenset[int], anys: frozenset[frozenset[int]], base: list[_Rep], win: dict[int, Value],
                free_corr: list[_Rep], cchoice: tuple[bool, ...]) -> bool:
        chosen = {c.id for c, on in zip(free_corr, cchoice, strict=True) if on}
        dropped = {c.id for c, on in zip(free_corr, cchoice, strict=True) if not on}
        killed = {self.by_id[c].op_of for c in chosen}
        if chosen & killed:  # an accepted correction rejects its target, so it cannot itself be accepted
            return False
        allowed = [r for r in base if r.id not in dropped and r.id not in killed and win.get(r.anchor, r.value) == r.value]
        kept: set[int] = set()
        for r in allowed:  # ascending anchor: the 'from' supports are strictly earlier
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
        cnt_v: dict[Value, int] = {}
        cnt_g: dict[str, int] = {}
        cnt_vg: dict[tuple[Value, str], int] = {}
        best_anchor: float = NEG_INF
        best_value: Value | None = None
        n = 0
        for i in kept:
            r = self.by_id[i]
            n += 1
            cnt_v[r.value] = cnt_v.get(r.value, 0) + 1
            cnt_g[r.origin] = cnt_g.get(r.origin, 0) + 1
            cnt_vg[(r.value, r.origin)] = cnt_vg.get((r.value, r.origin), 0) + 1
            if r.anchor > best_anchor:
                best_anchor, best_value = r.anchor, r.value
        second: float = NEG_INF  # latest anchor among kept with a value other than the best
        for i in kept:
            r = self.by_id[i]
            if r.value != best_value and r.anchor > second:
                second = r.anchor
        corrected = {self.by_id[c].op_of for c in chosen_corr}
        for o in rest:
            if o.id in corrected:
                continue
            if not self.competing:
                return False
            latest_diff = best_anchor if o.value != best_value else second
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

    # ---- query answers

    def feasible_pairs(self) -> set[tuple[int, Value | None, int, Value | None]]:
        """All (p, u, q, w): an admissible T whose last report at ``anchors[p]`` has value ``u`` and whose next
        report (at ``anchors[q]``, value ``w``) follows with nothing of T in between. ``p == -1``: no report
        before; ``q == len(anchors)``: no report after."""
        if self._pairs is not None:
            return self._pairs
        m = len(self.anchors)
        vals_at = [sorted({r.value for r in self.at[a]}, key=repr) for a in self.anchors]
        out: set[tuple[int, Value | None, int, Value | None]] = set()
        for p in range(-1, m):
            for q in range(p + 1, m + 1):
                if p == -1 and q == m:
                    continue
                lo = self.anchors[p] if p >= 0 else NEG_INF
                hi = self.anchors[q] if q < m else INF
                window = [r.id for r in self.reps if lo < r.anchor < hi]
                for u in (vals_at[p] if p >= 0 else [None]):
                    for w in (vals_at[q] if q < m else [None]):
                        anys: list[list[int]] = []
                        if p >= 0:
                            anys.append([r.id for r in self.at[self.anchors[p]] if r.value == u])
                        if q < m:
                            anys.append([r.id for r in self.at[self.anchors[q]] if r.value == w])
                        if self.feasible((), window, anys):
                            out.add((p, u, q, w))
        self._pairs = out
        return out

    def family_at(self, t: int) -> Family:
        """Union over admissible T of the single-valued candidate value-sets at valid day ``t``."""
        pairs = self.feasible_pairs()
        m = len(self.anchors)
        idx = -1
        for k, a in enumerate(self.anchors):
            if a <= t:
                idx = k
        cands: set[frozenset[Value]] = set()
        for (p, u, q, w) in pairs:
            if p <= idx < q:
                if p == -1:
                    cands.add(frozenset())
                else:
                    assert u is not None  # p >= 0 always has a value
                    cands.add(frozenset([u]))
                    # strictly inside a gap between two different values: the value is u or w, never both
                    if not (self.anchors[p] == t or q == m or u == w):
                        assert w is not None
                        cands.add(frozenset([w]))
        return frozenset(cands)

    def erroneous(self, rid: int) -> tuple[bool, bool]:
        """(some admissible T has the report TRUE, some admissible T has it ERR)."""
        return self.feasible([rid]), self.feasible([], [rid])

    def changed(self, v1: Value, v2: Value) -> tuple[bool, bool]:
        """(some interpretation has a v1 report followed by a later v2 report, some interpretation does not)."""
        yes = any(self.feasible([p.id, q.id]) for p in self.reps if p.value == v1
                  for q in self.reps if q.value == v2 and q.anchor > p.anchor)
        return yes, self._exists_without_pair(v1, v2)

    def _exists_without_pair(self, v1: Value, v2: Value) -> bool:
        """Exists admissible T with no v1 report followed by a later v2 report? T avoids the pattern iff it has
        no v1, or no v2, or there is a cut with v2 only strictly before it and v1 only at or after it."""
        ps = [p.id for p in self.reps if p.value == v1]
        qs = [q.id for q in self.reps if q.value == v2]
        if self.feasible([], ps) or self.feasible([], qs):
            return True
        for cut in self.anchors:
            ex = [p.id for p in self.reps if p.value == v1 and p.anchor < cut] + [
                q.id for q in self.reps if q.value == v2 and q.anchor >= cut
            ]
            if self.feasible([], ex):
                return True
        return False


def _kernel_of(spec: AttrSpec, ev: Sequence[Ev], policy: str) -> _KeyKernel:
    index = {e.id: i for i, e in enumerate(ev)}
    reps = [
        _Rep(
            id=i,
            value=e.value,
            anchor=e.anchor,
            origin=e.origin_group,
            cue=e.op_cue,
            op_from=e.op_from,
            op_of=index.get(e.op_of) if e.op_of is not None else None,
        )
        for i, e in enumerate(ev)
    ]
    return _KeyKernel(reps, self_update=policy == P0CSU, error_allowed=spec.error_allowed, competing=spec.competing_values)


# --------------------------------------------------------------------------- the justification


class FastJustification:
    """The read interface of :class:`~palimem.kernel.justify.Justification`, backed by existence queries."""

    route = "fast"

    def __init__(self, key: Key, spec: AttrSpec, evidence: Sequence[Ev], kernel: _KeyKernel, profile: Profile,
                 policy: str, enumeration_limit: int) -> None:
        self.key = key
        self.spec = spec
        self.evidence: tuple[Ev, ...] = tuple(evidence)
        self.relax_level = 0
        self.profile = profile
        self.policy = policy
        self.enumeration_limit = enumeration_limit
        self._kernel = kernel
        self._index = {e.id: i for i, e in enumerate(self.evidence)}
        self._cache: dict[int, Family] = {}
        self._segs: list[tuple[PSegment, ...]] = []
        self._budget = EnvBudget()
        self._interps: frozenset[Interp] | None = None

    @property
    def admitted_ids(self) -> tuple[str, ...]:
        return tuple(e.id for e in self.evidence)

    # ---- interpretations: lazy, inside the enumeration envelope only

    @property
    def interpretations(self) -> frozenset[Interp]:
        """Materialised only when the key has at most ``enumeration_limit`` reports (it is the 2^n object)."""
        if self._interps is None:
            if len(self.evidence) > self.enumeration_limit:
                raise KernelUnsupported(
                    f"{len(self.evidence)} reports exceed the enumeration limit {self.enumeration_limit}: "
                    "the fast kernel does not materialise interpretations"
                )
            self._interps, _ = key_interpretations(self.spec, self.evidence, self.policy)
        return self._interps

    @property
    def supports_available(self) -> bool:
        return len(self.evidence) <= self.enumeration_limit

    # ---- provenance

    def world_envs_at(self, t: int, *, depth: int | None = None, budget: EnvBudget | None = None) -> Dist:
        b = budget or self._budget
        if not self.supports_available:
            b.truncated = True  # a bounded explanation: no environments, same status and value
            return {w: frozenset() for w in self.candidates_at(t)}
        return base_world_envs(self.spec, self.evidence, self.interpretations, t, b)

    def oracle_flat_ids(self, t: int) -> frozenset[str]:
        return oracle_flat_ids(self.evidence, self.candidates_at(t))

    def oracle_exception_ids(self, t: int) -> frozenset[str]:
        return frozenset()

    def always_err_ids(self) -> frozenset[str]:
        """Reports every admissible interpretation labels ERR: asks ``exists T containing it`` per report."""
        return frozenset(e.id for i, e in enumerate(self.evidence) if not self._kernel.feasible([i]))

    @property
    def explanation_state(self) -> ExplanationState:
        return ExplanationState.TRUNCATED if (self._budget.truncated or not self.supports_available) else ExplanationState.COMPLETE

    def explain(self, day: int, *, mode: ExplainMode = ExplainMode.ALL, depth: int | None = None,
                env_cap: int = DEFAULT_ENV_CAP) -> Explanation:
        return explain_at(self, day, mode=mode, depth=depth, env_cap=env_cap)

    # ---- candidates, segments

    def candidates_at(self, t: int) -> Family:
        fam = self._cache.get(t)
        if fam is None:
            fam = self._kernel.family_at(t) if self.evidence else frozenset({frozenset()})
            self._cache[t] = fam
        return fam

    def breakpoints(self) -> frozenset[int]:
        """A superset of the days at which the candidate family can change: every report anchor and the day
        after it (the enumeration's run boundaries are anchors and anchors + 1). ``build_segments`` merges
        equal neighbours, so the segments are those of the enumeration kernel."""
        pts: set[int] = set()
        for e in self.evidence:
            pts.add(e.anchor)
            pts.add(e.anchor + 1)
        return frozenset(pts)

    def segments(self) -> tuple[PSegment, ...]:
        if not self._segs:
            supports = self.world_envs_at if self.supports_available else None
            self._segs.append(build_segments(self.key, self.spec, self.profile, self.breakpoints(), self.candidates_at, supports))
        return self._segs[0]

    def segment_at(self, day: int) -> PSegment:
        return segment_at(self.segments(), day)

    # ---- truth sets for yes/no slots

    def holds_truths(self, value: Value, t: int) -> list[bool]:
        return [value in c for c in self.candidates_at(t)]

    def changed_truths(self, v1: Value, v2: Value) -> list[bool]:
        if not self.evidence:
            return [False]
        yes, no = self._kernel.changed(v1, v2)
        return [True] * int(yes) + [False] * int(no)

    def erroneous_truths(self, report_id: str) -> list[bool]:
        i = self._index.get(report_id)
        if i is None:
            return []
        in_t, err = self._kernel.erroneous(i)
        return [True] * int(err) + [False] * int(in_t)


# --------------------------------------------------------------------------- dispatch


@dataclass(frozen=True)
class Dispatch:
    """The explicit routing decision: which kernel answered, and why the other did not."""

    result: FastJustification | Justification | ResourceLimitedResult
    route: str  # "fast" | "enumeration" | "resource_limited"
    reason: str = field(default="")


def in_fast_class(spec: AttrSpec, policy: str) -> str | None:
    """None if the key is in the proven class, else the reason it is not."""
    if spec.derived:
        return "derived key"
    if spec.cardinality != "single":
        return "multi-valued key (the answer itself is exponential; needs a factored contract)"
    if not spec.changeable:
        return "stable key (not covered by the R4.1 differential)"
    if policy not in (P0C, P0CSU):
        return f"policy {policy}"
    return None


def dispatch_key(
    schema: KernelSchema,
    key: Key,
    admitted: Sequence[LogEntry],
    semantic: SemanticConfig,
    *,
    profile: Profile | None = None,
    budget: int = DEFAULT_ENVIRONMENT_BUDGET,
    fast_limit: int = DEFAULT_FAST_LIMIT,
    branch_budget: int = DEFAULT_BRANCH_BUDGET,
    change_from: Mapping[str, Value] | None = None,
) -> Dispatch:
    """Justify a base key with the fast kernel where it is proven, else with the enumeration kernel.

    ``budget`` is the enumeration kernel's environment budget (it also bounds the lazily materialised
    supports of the fast path). The routing reason is returned, never hidden."""
    spec = schema.spec(key.attr)
    if spec.derived:
        raise ValueError(f"{key.attr!r} is derived: use justify_derived")
    policy = policy_of(semantic)
    why = in_fast_class(spec, policy)
    ev: list[Ev] | None = None
    from palimem.kernel.polarity import has_negative_evidence  # circular at import time

    if why is None and has_negative_evidence(admitted):
        why = "negative evidence (the polarity kernel, product profile)"
    if why is None:
        for e in admitted:
            if e.report.key != key:
                raise ValueError(f"report {e.report.id} is on key {e.report.key}, not {key}")
        ev = evidence_from_entries(admitted, change_from)
        if not ev:
            why = "no evidence"
        elif len(ev) > fast_limit:
            why = f"{len(ev)} reports exceed the fast limit {fast_limit}"
    if why is None:
        assert ev is not None
        kernel = _kernel_of(spec, ev, policy)
        if kernel.branch_bound() > branch_budget:
            why = f"branch bound {kernel.branch_bound()} exceeds the branch budget {branch_budget}"
        elif not kernel.any_admissible():
            why = "no admissible interpretation without relaxation (totality ladder level > 0)"
        else:
            fj = FastJustification(key, spec, ev, kernel, profile if profile is not None else semantic.profile, policy, budget)
            return Dispatch(fj, "fast", "single-valued changeable key inside the proven class")
    res = justify_key(schema, key, admitted, semantic, profile=profile, budget=budget, change_from=change_from)
    if isinstance(res, ResourceLimitedResult):
        return Dispatch(
            ResourceLimitedResult(
                key=res.key, reason=ResourceLimitedReason.ENVIRONMENT_BUDGET,
                detail=f"{res.detail}; the fast kernel was not used ({why})", n=res.n, budget=res.budget,
            ),
            "resource_limited",
            why or "",
        )
    return Dispatch(res, "enumeration", why or "")


def justify_key_fast(
    schema: KernelSchema,
    key: Key,
    admitted: Sequence[LogEntry],
    semantic: SemanticConfig,
    *,
    profile: Profile | None = None,
    budget: int = DEFAULT_ENVIRONMENT_BUDGET,
    fast_limit: int = DEFAULT_FAST_LIMIT,
    branch_budget: int = DEFAULT_BRANCH_BUDGET,
    change_from: Mapping[str, Value] | None = None,
) -> FastJustification | Justification | ResourceLimitedResult:
    """:func:`dispatch_key` without the routing record (same result)."""
    return dispatch_key(schema, key, admitted, semantic, profile=profile, budget=budget, fast_limit=fast_limit,
                        branch_budget=branch_budget, change_from=change_from).result


def justify_ev_fast(spec: AttrSpec, ev: Sequence[Ev], policy: str, key: Key | None = None,
                    profile: Profile = Profile.REVISE_STREAM_V1, enumeration_limit: int = DEFAULT_ENVIRONMENT_BUDGET) -> FastJustification:
    """The fast justification straight from evidence (tests and benchmarks; no dispatch, no fallback)."""
    reason = in_fast_class(spec, policy)
    if reason is not None:
        raise KernelUnsupported(reason)
    kernel = _kernel_of(spec, ev, policy)
    if ev and not kernel.any_admissible():
        raise KernelUnsupported("no admissible interpretation without relaxation (totality ladder level > 0)")
    return FastJustification(key or Key(entity="e", attr=spec.name), spec, ev, kernel, profile, policy, enumeration_limit)
