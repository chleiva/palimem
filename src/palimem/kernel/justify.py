"""Justification of one base key: interpretations -> candidates -> segments.

``justify_key`` is the kernel entry point for a base (observed) key. It returns a :class:`Justification`:
the key's interpretation set (the audit-oracle layer) plus the views derived from it: candidate
families at a valid time, the valid-time segment partition (contract :class:`~palimem.types.Segment`
objects, one status per segment), and the truth sets that yes/no questions need.

``Justification.to_belief`` is deliberately not here: a ``Belief`` record also carries store metadata
(version, lsn, generations, pins), which only the store knows. The store builds the record from
``Justification.segments()`` and ``Justification.admitted_ids``.

Per-candidate supports (subset-minimal environments over base reports, S-12, task T-B4) come from
``palimem.kernel.provenance`` and are carried in every segment's ``support`` map; ``explain_at`` answers
``explain(key, valid_at, mode, depth)`` and ``oracle_flat_ids`` reproduces the study's flat provenance set
for the ``revise-stream-v1`` profile.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from palimem.kernel.evidence import Ev, dt_of_day, evidence_from_entries
from palimem.kernel.interpret import P0C, P0CSU, key_interpretations
from palimem.kernel.provenance import (
    DEFAULT_ENV_CAP,
    Dist,
    Env,
    EnvBudget,
    base_world_envs,
    oracle_flat_ids,
)
from palimem.kernel.spec import AttrSpec, KernelSchema, KernelUnsupported
from palimem.kernel.timeline import INF, Interp, observed_candidates
from palimem.types import (
    Candidate,
    EmptyForm,
    ExplainMode,
    Explanation,
    ExplanationState,
    KernelStatus,
    Key,
    LogEntry,
    Profile,
    ResourceLimitedReason,
    SegmentBounds,
    SemanticConfig,
    SetForm,
    Support,
    ValueForm,
)
from palimem.types import Segment as PSegment
from palimem.types._codec import Value, canonical_json
from palimem.types.limits import DEFAULT_ENVIRONMENT_BUDGET

Family = frozenset[frozenset[Value]]


@dataclass(frozen=True)
class ResourceLimitedResult:
    """The key cannot be justified within the resource contract. Carries no kernel status (design v0.3)."""

    key: Key
    reason: ResourceLimitedReason
    detail: str
    n: int = 0
    budget: int = 0


def policy_of(semantic: SemanticConfig) -> str:
    """P0c (``self_update`` off) or P0cSU (on); the semantic configuration fixes it (S-02/S-08)."""
    if semantic.semantics != "v0.3":
        raise KernelUnsupported(f"semantics {semantic.semantics!r}: only v0.3 is implemented")
    return P0CSU if semantic.self_update else P0C


# --------------------------------------------------------------------------- classification


def _form_of(spec: AttrSpec, s: frozenset[Value]) -> EmptyForm | ValueForm | SetForm:
    if not s:
        return EmptyForm()
    if spec.cardinality == "single":
        if len(s) > 1:
            raise KernelUnsupported(f"single-valued attr {spec.name!r}: a world holds several values {sorted(map(str, s))}")
        return ValueForm(value=next(iter(s)))
    return SetForm(values=tuple(s))


def classify(
    key: Key, spec: AttrSpec, profile: Profile, family: Family
) -> tuple[KernelStatus, Candidate | None, tuple[Candidate, ...]]:
    """Map a candidate family at one valid time to (status, established, alternatives).

    Single-valued: no evidence -> ``unknown``; one value -> ``established``; several (the empty world
    included) -> ``unresolved``. Multi-valued: a family of just the empty set is ``established_empty`` under
    the compat profile ``revise-stream-v1`` (the paper's closed-world convention, S-04) and ``unknown`` under
    the open-world profile (absence is not evidence of emptiness).
    """
    forms = [_form_of(spec, s) for s in family]
    if not forms or all(isinstance(f, EmptyForm) for f in forms):
        if spec.cardinality == "multi" and profile is Profile.REVISE_STREAM_V1 and forms:
            return KernelStatus.ESTABLISHED_EMPTY, Candidate(key=key, form=EmptyForm()), ()
        return KernelStatus.UNKNOWN, None, ()
    cands = [Candidate(key=key, form=f) for f in forms]
    if len(cands) == 1:
        return KernelStatus.ESTABLISHED, cands[0], ()
    cands.sort(key=lambda c: canonical_json(c.form.to_dict()))
    return KernelStatus.UNRESOLVED, None, tuple(cands)


def _env_key(e: Env) -> tuple[int, list[str]]:
    return (len(e), sorted(e))


def _supports_of(
    key: Key,
    spec: AttrSpec,
    fam: Family,
    sup: Dist | None,
    lo: int | None,
    hi: int | None,
    cand_ids: frozenset[str],
) -> dict[str, tuple[Support, ...]]:
    """Per-candidate supports of one segment: each candidate world's subset-minimal environments, stamped
    with the segment's valid interval. A world whose only environment is empty (no positive evidence, e.g.
    the empty world) has no support entry."""
    out: dict[str, tuple[Support, ...]] = {}
    if sup is None:
        return out
    vf = None if lo is None else dt_of_day(lo)
    vt = None if hi is None else dt_of_day(hi)
    for w in fam:
        cid = Candidate(key=key, form=_form_of(spec, w)).id
        if cid not in cand_ids:
            continue
        envs = sorted((e for e in sup.get(w, frozenset()) if e), key=_env_key)
        if envs:
            out[cid] = tuple(Support(environment=tuple(sorted(e)), valid_from=vf, valid_to=vt) for e in envs)
    return out


def build_segments(
    key: Key,
    spec: AttrSpec,
    profile: Profile,
    breakpoints: Iterable[int],
    family_at: Callable[[int], Family],
    supports_at: Callable[[int], Dist] | None = None,
) -> tuple[PSegment, ...]:
    """Partition valid time at ``breakpoints`` and classify each piece; merge equal neighbours.

    The candidate family is piecewise constant between consecutive breakpoints (the breakpoints are the
    integer days at which any run's coverage can change), so one evaluation per piece suffices. With
    ``supports_at`` the pieces also carry per-candidate supports (S-12) and neighbours merge only when both
    the family **and** the supports are equal: a support is *per interval* (the same candidate justified by
    different reports in January and February stays two segments).
    """
    pts = sorted(set(breakpoints))
    if not pts:
        spans: list[tuple[int | None, int | None, int]] = [(None, None, 0)]
    else:
        spans = [(None, pts[0], pts[0] - 1)]
        spans += [(pts[i], pts[i + 1], pts[i]) for i in range(len(pts) - 1)]
        spans.append((pts[-1], None, pts[-1]))
    merged: list[tuple[int | None, int | None, Family, Dist | None]] = []
    for lo, hi, rep in spans:
        fam = family_at(rep)
        sup = supports_at(rep) if supports_at is not None else None
        if merged and merged[-1][2] == fam and merged[-1][3] == sup:
            merged[-1] = (merged[-1][0], hi, fam, sup)
        else:
            merged.append((lo, hi, fam, sup))
    out: list[PSegment] = []
    for lo, hi, fam, sup in merged:
        status, est, alts = classify(key, spec, profile, fam)
        cand_ids = frozenset(c.id for c in ([est] if est is not None else []) + list(alts))
        out.append(
            PSegment(
                valid_from=None if lo is None else dt_of_day(lo),
                valid_to=None if hi is None else dt_of_day(hi),
                kernel_status=status,
                established=est,
                alternatives=alts,
                support=_supports_of(key, spec, fam, sup, lo, hi, cand_ids),
            )
        )
    return tuple(out)


def segment_at(segments: Sequence[PSegment], day: int) -> PSegment:
    """The segment containing valid day ``day`` (segment lists are short; a scan is clear)."""
    t = dt_of_day(day)
    for s in segments:
        if (s.valid_from is None or s.valid_from <= t) and (s.valid_to is None or t < s.valid_to):
            return s
    raise ValueError(f"no segment contains day {day}")


# --------------------------------------------------------------------------- explain


class Explainable(Protocol):
    """What ``explain_at`` needs: a base or derived justification."""

    @property
    def key(self) -> Key: ...

    def segment_at(self, day: int) -> PSegment: ...

    def world_envs_at(self, t: int, *, depth: int | None = None, budget: EnvBudget | None = None) -> Dist: ...


def canonical_environment(envs: Iterable[Env]) -> Env | None:
    """``explain(mode=one)``: the lexicographically least environment by sorted report ids (deterministic
    across versions, so agents may cache it; S-12 open question 1)."""
    best: Env | None = None
    for e in envs:
        if best is None or sorted(e) < sorted(best):
            best = e
    return best


def explain_at(
    j: Explainable,
    day: int,
    *,
    mode: ExplainMode = ExplainMode.ALL,
    depth: int | None = None,
    env_cap: int = DEFAULT_ENV_CAP,
) -> Explanation:
    """``explain(key, valid_at, mode, depth)`` at kernel level (S-12).

    Returns the subset-minimal environments over **base** reports of every candidate of the segment
    containing ``day`` (an ``unresolved`` segment explains all its alternatives). ``depth=None`` is the full
    derivation closure; ``depth=d`` explains ``d`` derivation levels (1 = the key's own reports) and marks the
    explanation ``truncated`` when deeper evidence was left out. ``env_cap`` bounds the minimal environments
    kept per candidate; truncation never changes status or value.
    """
    if depth is not None and depth < 1:
        raise ValueError("depth must be at least 1")
    seg = j.segment_at(day)
    budget = EnvBudget(cap=env_cap)
    dist = j.world_envs_at(day, depth=depth, budget=budget)
    envs: set[Env] = set()
    for es in dist.values():
        envs |= {e for e in es if e}
    ordered = sorted(envs, key=_env_key)
    if mode is ExplainMode.ONE:
        one = canonical_environment(ordered)
        ordered = [] if one is None else [one]
    state = ExplanationState.TRUNCATED if budget.truncated else ExplanationState.COMPLETE
    return Explanation(
        key=j.key,
        segment=SegmentBounds(valid_from=seg.valid_from, valid_to=seg.valid_to),
        mode=mode,
        depth=depth,
        state=state,
        environments=tuple(
            Support(environment=tuple(sorted(e)), valid_from=seg.valid_from, valid_to=seg.valid_to) for e in ordered
        ),
    )


# --------------------------------------------------------------------------- Justification


@dataclass(frozen=True, eq=False)
class Justification:
    """The justification of one base key at one belief point."""

    key: Key
    spec: AttrSpec
    evidence: tuple[Ev, ...]
    interpretations: frozenset[Interp]
    relax_level: int
    profile: Profile
    policy: str
    _cache: dict[int, Family] = field(default_factory=dict, repr=False, compare=False)
    _segs: list[tuple[PSegment, ...]] = field(default_factory=list, repr=False, compare=False)
    _budget: EnvBudget = field(default_factory=EnvBudget, repr=False, compare=False)

    @property
    def admitted_ids(self) -> tuple[str, ...]:
        return tuple(e.id for e in self.evidence)

    # ---- provenance (T-B4, S-12)

    def world_envs_at(self, t: int, *, depth: int | None = None, budget: EnvBudget | None = None) -> Dist:
        """Candidate world -> subset-minimal environments at valid day ``t``. ``depth`` is irrelevant for a
        base key: its own reports are level 1."""
        return base_world_envs(self.spec, self.evidence, self.interpretations, t, budget or self._budget)

    def oracle_flat_ids(self, t: int) -> frozenset[str]:
        """Profile ``revise-stream-v1``: the study's flat provenance set at ``t`` (see ``provenance``)."""
        return oracle_flat_ids(self.evidence, self.candidates_at(t))

    def oracle_exception_ids(self, t: int) -> frozenset[str]:
        """Diagnostic: a base key has no exception literals."""
        return frozenset()

    def always_err_ids(self) -> frozenset[str]:
        """Diagnostic: admitted reports that every admissible interpretation labels ERR."""
        return frozenset(e.id for e in self.evidence if all(e.id in err for err, _tl in self.interpretations))

    @property
    def explanation_state(self) -> ExplanationState:
        return ExplanationState.TRUNCATED if self._budget.truncated else ExplanationState.COMPLETE

    def explain(
        self, day: int, *, mode: ExplainMode = ExplainMode.ALL, depth: int | None = None, env_cap: int = DEFAULT_ENV_CAP
    ) -> Explanation:
        return explain_at(self, day, mode=mode, depth=depth, env_cap=env_cap)

    def candidates_at(self, t: int) -> Family:
        """Union over the key's interpretations of the candidate value-sets at valid day ``t``."""
        fam = self._cache.get(t)
        if fam is None:
            cands: set[frozenset[Value]] = set()
            for _err, tl in self.interpretations:
                cands |= observed_candidates(tl, self.spec, t)
            fam = frozenset(cands)
            self._cache[t] = fam
        return fam

    def breakpoints(self) -> frozenset[int]:
        pts: set[int] = set()
        for _err, tl in self.interpretations:
            for run in tl:
                pts |= run.breakpoints()
        return frozenset(pts)

    def segments(self) -> tuple[PSegment, ...]:
        if not self._segs:
            self._segs.append(
                build_segments(self.key, self.spec, self.profile, self.breakpoints(), self.candidates_at, self.world_envs_at)
            )
        return self._segs[0]

    def segment_at(self, day: int) -> PSegment:
        return segment_at(self.segments(), day)

    # ---- truth sets for yes/no slots (audit-oracle layer)

    def holds_truths(self, value: Value, t: int) -> list[bool]:
        """For each candidate world at ``t``: does ``value`` hold? (``holds`` yes/no slots.)"""
        return [value in c for c in self.candidates_at(t)]

    def changed_truths(self, v1: Value, v2: Value) -> list[bool]:
        """Per interpretation: a run of ``v1`` that ends (not forever) followed later by a run of ``v2``."""
        out: list[bool] = []
        for _err, tl in self.interpretations:
            hit = False
            for s1 in tl:
                if s1.value != v1 or s1.end_hi == INF:
                    continue
                if any(s2.value == v2 and s2.start >= s1.end_lo and s2.start > s1.start for s2 in tl):
                    hit = True
                    break
            out.append(hit)
        return out

    def erroneous_truths(self, report_id: str) -> list[bool]:
        """Per interpretation: is the report labelled ERR? (``erroneous`` yes/no slots.)"""
        return [report_id in err for err, _tl in self.interpretations]


def justify_key(
    schema: KernelSchema,
    key: Key,
    admitted: Sequence[LogEntry],
    semantic: SemanticConfig,
    *,
    profile: Profile | None = None,
    budget: int = DEFAULT_ENVIRONMENT_BUDGET,
    change_from: Mapping[str, Value] | None = None,
) -> Justification | ResourceLimitedResult:
    """Justify a base key from its admitted evidence.

    ``admitted`` holds the log entries admission let through for ``key`` (withdrawals and
    same-origin self-corrections already applied). ``budget`` is the maximum number of admitted reports
    on the key (default 7, the validated envelope, S-06); above it the answer is
    ``ResourceLimitedResult(environment_budget)``, never a silent fallback. ``change_from`` carries the
    out-of-band ``from`` value of ``change`` cues (see :mod:`palimem.kernel.evidence`).
    """
    spec = schema.spec(key.attr)
    if spec.derived:
        raise ValueError(f"{key.attr!r} is derived: use justify_derived")
    for e in admitted:
        if e.report.key != key:
            raise ValueError(f"report {e.report.id} is on key {e.report.key}, not {key}")
    from palimem.kernel.polarity import (  # circular at import time
        has_negative_evidence,
        justify_polarity,
    )

    if has_negative_evidence(admitted):  # product profile only: the compat profile has no oracle for denials
        return justify_polarity(schema, key, admitted, semantic, profile=profile, budget=budget)
    ev = evidence_from_entries(admitted, change_from)
    n = len(ev)
    if n > budget:
        return ResourceLimitedResult(
            key=key,
            reason=ResourceLimitedReason.ENVIRONMENT_BUDGET,
            detail=f"{n} admitted reports on the key exceed the environment budget {budget}",
            n=n,
            budget=budget,
        )
    policy = policy_of(semantic)
    interps, level = key_interpretations(spec, ev, policy)
    return Justification(
        key=key,
        spec=spec,
        evidence=tuple(ev),
        interpretations=interps,
        relax_level=level,
        profile=profile if profile is not None else semantic.profile,
        policy=policy,
    )
