"""Justification of one base key: interpretations -> candidates -> segments.

``justify_key`` is the kernel entry point for a base (observed) key. It returns a :class:`Justification`:
the key's interpretation set (the audit-oracle layer) plus the views derived from it: candidate
families at a valid time, the valid-time segment partition (contract :class:`~palimem.types.Segment`
objects, one status per segment), and the truth sets that yes/no questions need.

``Justification.to_belief`` is deliberately not here: a ``Belief`` record also carries store metadata
(version, lsn, generations, pins), which only the store knows. The store builds the record from
``Justification.segments()`` and ``Justification.admitted_ids``.

Per-candidate supports (subset-minimal environments, S-12) are task T-B4 and are not computed yet:
segments carry an empty ``support`` map.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from palimem.kernel.evidence import Ev, dt_of_day, evidence_from_entries
from palimem.kernel.interpret import P0C, P0CSU, key_interpretations
from palimem.kernel.spec import AttrSpec, KernelSchema, KernelUnsupported
from palimem.kernel.timeline import INF, Interp, observed_candidates
from palimem.types import (
    Candidate,
    EmptyForm,
    KernelStatus,
    Key,
    LogEntry,
    Profile,
    ResourceLimitedReason,
    SemanticConfig,
    SetForm,
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


def build_segments(
    key: Key,
    spec: AttrSpec,
    profile: Profile,
    breakpoints: Iterable[int],
    family_at: Callable[[int], Family],
) -> tuple[PSegment, ...]:
    """Partition valid time at ``breakpoints`` and classify each piece; merge equal neighbours.

    The candidate family is piecewise constant between consecutive breakpoints (the breakpoints are the
    integer days at which any run's coverage can change), so one evaluation per piece suffices.
    """
    pts = sorted(set(breakpoints))
    if not pts:
        spans: list[tuple[int | None, int | None, int]] = [(None, None, 0)]
    else:
        spans = [(None, pts[0], pts[0] - 1)]
        spans += [(pts[i], pts[i + 1], pts[i]) for i in range(len(pts) - 1)]
        spans.append((pts[-1], None, pts[-1]))
    merged: list[tuple[int | None, int | None, Family]] = []
    for lo, hi, rep in spans:
        fam = family_at(rep)
        if merged and merged[-1][2] == fam:
            merged[-1] = (merged[-1][0], hi, fam)
        else:
            merged.append((lo, hi, fam))
    out: list[PSegment] = []
    for lo, hi, fam in merged:
        status, est, alts = classify(key, spec, profile, fam)
        out.append(
            PSegment(
                valid_from=None if lo is None else dt_of_day(lo),
                valid_to=None if hi is None else dt_of_day(hi),
                kernel_status=status,
                established=est,
                alternatives=alts,
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

    @property
    def admitted_ids(self) -> tuple[str, ...]:
        return tuple(e.id for e in self.evidence)

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
            self._segs.append(build_segments(self.key, self.spec, self.profile, self.breakpoints(), self.candidates_at))
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
