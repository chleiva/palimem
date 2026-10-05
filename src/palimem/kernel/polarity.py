"""Negative evidence in the open-world product kernel (author ruling 4 of 2026-10-05).

The paper's oracle has no negative evidence (``until`` / ``interval`` cues and negative polarity raise
``NotImplementedError`` in the deposited model), so this module is **product-profile only**: the compat profile still
rejects what its oracle cannot represent. It extends the kernel with the two negative propositions of the contract,
``not_value(v)`` for single-valued keys and ``not_member(v)`` for set-valued ones, under a deliberately small, precisely
stated semantics, and refuses everything outside it (``KernelUnsupported``, never a guess).

Semantics. Take the admitted reports of one key; each is *positive* (``value(v)`` / ``member(v)``) or *negative*
(``not_value(v)`` / ``not_member(v)``). An **interpretation** labels every report TRUE or ERR such that

* **consistency**: no TRUE positive about ``v`` stands beside a TRUE negative about the same ``v`` (a denial is evidence
  against, not a veto: they *conflict*; denials of different values, and a denial beside a positive of another value, are
  compatible), and
* **A-ERR** (error needs a dispute): a report may be ERR only if some TRUE report conflicts with it.

(This is the paper's A-ERR / A-CONS restricted to the conflict relation "positive and negative about the same value".)
The key's candidates are read off the admissible interpretations; the status follows:

* **only denials** (no positive evidence): one denied value alone is ``established_false`` carrying ``not_value(w)`` /
  ``not_member(w)`` (explicit negative evidence suffices, design v0.3); **two or more compatible denials are
  ``unknown`` with every denial listed as a constraint in ``alternatives``** (the value is not determined, only
  narrowed; one established candidate cannot carry several denials). No completeness plumbing exists, so an exhaustive
  ``declared`` completeness does not turn several denials into ``established_false``: flagged to the author.
* **positive evidence, no denial of it**: ``established`` (denials of other values are consistent constraints, true in
  every interpretation, and are not listed).
* **positive evidence and a denial of the same value**: ``unresolved`` with both readings as candidates
  (``value(v)`` and ``not_value(v)``; for a set, ``set(...)`` with and without ``v``, the negative reading standing for
  an otherwise empty set), the design's "unresolved while both stand; the surviving one alone decides after a withdrawal".

Supports are the subset-minimal environments over base reports: the TRUE positives of a value world, the TRUE denials of a
denial candidate.

Scope (checked; outside it the kernel raises ``KernelUnsupported``): reports with the ``assert`` cue only (no ``change`` /
``correct``: their interplay with denials has no oracle), no ``valid_from`` / ``valid_to`` (evidence is read as claims about the
key now; interval semantics are staged, S-09), a single-valued key with at most one distinct positive value (competing
positives with denials are not defined here), a set-valued key with at most one member both affirmed and denied.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from palimem.kernel.evidence import Ev, evidence_from_entries
from palimem.kernel.justify import Justification, ResourceLimitedResult, policy_of
from palimem.kernel.provenance import Dist, Env, EnvBudget
from palimem.kernel.scope import polarity_scope_problem
from palimem.kernel.spec import AttrSpec, KernelSchema, KernelUnsupported
from palimem.types import (
    Candidate,
    Cue,
    ExplanationState,
    KernelStatus,
    Key,
    LogEntry,
    MemberProp,
    NotMemberForm,
    NotMemberProp,
    NotValueForm,
    NotValueProp,
    Origin,
    Precision,
    Profile,
    ResourceLimitedReason,
    SemanticConfig,
    SetForm,
    Support,
    ValueForm,
    ValueProp,
)
from palimem.types import Segment as PSegment
from palimem.types._codec import Value, canonical_json
from palimem.types.limits import DEFAULT_ENVIRONMENT_BUDGET

NEGATIVE_PROPS = (NotValueProp, NotMemberProp)


def has_negative_evidence(entries: Sequence[LogEntry]) -> bool:
    return any(isinstance(e.report.proposition, NEGATIVE_PROPS) for e in entries)


@dataclass(frozen=True)
class _Rep:
    id: str
    value: Value
    negative: bool


@dataclass(frozen=True, eq=False)
class PolarityJustification(Justification):
    """A base key with negative evidence. Same read interface as :class:`Justification` (``evidence`` holds the positive
    reports, which is what derived rules read; ``admitted_ids`` holds every admitted report, which is what a belief pins)."""

    all_ids: tuple[str, ...] = field(default=(), repr=False)
    _segment: PSegment | None = field(default=None, repr=False, compare=False)
    _fam: frozenset[frozenset[Value]] = field(default=frozenset(), repr=False, compare=False)
    _dist: dict[frozenset[Value], frozenset[Env]] = field(default_factory=dict, repr=False, compare=False)
    _err: tuple[frozenset[str], ...] = field(default=(frozenset(),), repr=False, compare=False)

    @property
    def admitted_ids(self) -> tuple[str, ...]:
        return self.all_ids

    def segments(self) -> tuple[PSegment, ...]:
        assert self._segment is not None
        return (self._segment,)

    def candidates_at(self, t: int) -> frozenset[frozenset[Value]]:
        return self._fam  # evidence is time-free: the candidate family does not depend on t

    def breakpoints(self) -> frozenset[int]:
        return frozenset()

    def world_envs_at(self, t: int, *, depth: int | None = None, budget: EnvBudget | None = None) -> Dist:
        return {w: frozenset(es) for w, es in self._dist.items()}

    def oracle_flat_ids(self, t: int) -> frozenset[str]:
        return frozenset(self.all_ids)  # there is no oracle for negative evidence: every admitted report

    def always_err_ids(self) -> frozenset[str]:
        return frozenset()

    @property
    def explanation_state(self) -> ExplanationState:
        return ExplanationState.COMPLETE

    def changed_truths(self, v1: Value, v2: Value) -> list[bool]:
        return [False]  # time-free evidence: nothing changed

    def erroneous_truths(self, report_id: str) -> list[bool]:
        """Per admissible interpretation: is the report labelled ERR?"""
        return [report_id in err for err in self._err]


def _sorted_cands(key: Key, forms: Sequence[ValueForm | SetForm | NotValueForm | NotMemberForm]) -> tuple[Candidate, ...]:
    cs = [Candidate(key=key, form=f) for f in forms]
    cs.sort(key=lambda c: canonical_json(c.form.to_dict()))
    return tuple(cs)


def _support(ids: Sequence[str]) -> tuple[Support, ...]:
    return (Support(environment=tuple(sorted(ids))),)


def _parse(admitted: Sequence[LogEntry], spec: AttrSpec) -> tuple[list[_Rep], list[_Rep]]:
    pos: list[_Rep] = []
    neg: list[_Rep] = []
    single = spec.cardinality == "single"
    problem = polarity_scope_problem(admitted)
    if problem is not None:
        raise KernelUnsupported(problem)
    for e in admitted:
        r = e.report
        assert r.id is not None
        if r.origin is not Origin.EXTERNAL_OBSERVATION:
            raise ValueError(f"report {r.id}: only admissible external observations reach the kernel")
        if r.cue is not Cue.ASSERT:
            raise KernelUnsupported(
                f"report {r.id}: cue {r.cue.value!r} beside negative evidence has no oracle yet (only 'assert' is in scope)"
            )
        if r.valid_from is not None or r.valid_to is not None:
            raise KernelUnsupported(f"report {r.id}: valid-time cues beside negative evidence are staged (S-09)")
        if r.precision is not Precision.DAY:
            raise KernelUnsupported(f"report {r.id}: precision {r.precision.value!r} (S-09: day only in 0.1)")
        p = r.proposition
        ok_pos, ok_neg = (ValueProp, NotValueProp) if single else (MemberProp, NotMemberProp)
        if isinstance(p, ok_pos):
            pos.append(_Rep(r.id, p.value, False))
        elif isinstance(p, ok_neg):
            neg.append(_Rep(r.id, p.value, True))
        else:
            raise KernelUnsupported(f"report {r.id}: proposition form {type(p).__name__} does not fit the key's class")
    return pos, neg


def _by_value(reps: Sequence[_Rep]) -> dict[Value, list[str]]:
    out: dict[Value, list[str]] = {}
    for r in reps:
        out.setdefault(r.value, []).append(r.id)
    return {k: sorted(v) for k, v in out.items()}


def justify_polarity(
    schema: KernelSchema,
    key: Key,
    admitted: Sequence[LogEntry],
    semantic: SemanticConfig,
    *,
    profile: Profile | None = None,
    budget: int = DEFAULT_ENVIRONMENT_BUDGET,
) -> PolarityJustification | ResourceLimitedResult:
    """Justify a base key whose admitted evidence includes denials (product profile only)."""
    spec = schema.spec(key.attr)
    prof = profile if profile is not None else semantic.profile
    if prof is Profile.REVISE_STREAM_V1:
        raise KernelUnsupported("negative evidence has no oracle: the compat profile rejects it (S-09)")
    n = len(admitted)
    if n > budget:
        return ResourceLimitedResult(
            key=key, reason=ResourceLimitedReason.ENVIRONMENT_BUDGET,
            detail=f"{n} admitted reports on the key exceed the environment budget {budget}", n=n, budget=budget,
        )
    pos, neg = _parse(admitted, spec)
    pos_by, neg_by = _by_value(pos), _by_value(neg)
    pos_entries = [e for e in admitted if e.report.id in {p.id for p in pos}]
    evidence: tuple[Ev, ...] = tuple(evidence_from_entries(pos_entries)) if pos_entries else ()
    all_ids = tuple(e.report.id for e in admitted if e.report.id is not None)

    status: KernelStatus
    est: Candidate | None = None
    alts: tuple[Candidate, ...] = ()
    sup: dict[str, tuple[Support, ...]] = {}
    dist: dict[frozenset[Value], frozenset[Env]] = {}
    fam: set[frozenset[Value]] = set()
    err: list[frozenset[str]] = [frozenset()]  # ERR ids per admissible interpretation

    def add(form: ValueForm | SetForm | NotValueForm | NotMemberForm, ids: Sequence[str]) -> Candidate:
        c = Candidate(key=key, form=form)
        sup[c.id] = _support(ids)
        return c

    if spec.cardinality == "single":
        if len(pos_by) > 1:
            raise KernelUnsupported(
                f"single-valued key {key.entity}/{key.attr}: competing positive values beside negative evidence have no oracle yet"
            )
        if not pos_by:  # only denials
            fam = {frozenset()}
            all_neg = sorted(i for ids in neg_by.values() for i in ids)
            dist[frozenset()] = frozenset({frozenset(all_neg)})
            ws = sorted(neg_by, key=lambda w: canonical_json(w))
            if len(ws) == 1:
                status, est = KernelStatus.ESTABLISHED_FALSE, add(NotValueForm(value=ws[0]), neg_by[ws[0]])
            else:
                status = KernelStatus.UNKNOWN
                alts = _sorted_cands(key, [NotValueForm(value=w) for w in ws])
                sup = {c.id: _support(neg_by[c.form.value]) for c in alts}  # type: ignore[union-attr]
        else:
            (v,) = pos_by
            p_ids = pos_by[v]
            denied = neg_by.get(v, [])
            fam = {frozenset({v})}
            dist[frozenset({v})] = frozenset({frozenset(p_ids)})
            if not denied:
                status, est = KernelStatus.ESTABLISHED, add(ValueForm(value=v), p_ids)
            else:
                status = KernelStatus.UNRESOLVED
                fam.add(frozenset())
                dist[frozenset()] = frozenset({frozenset(denied)})
                a, b = add(ValueForm(value=v), p_ids), add(NotValueForm(value=v), denied)
                alts = _sorted_cands(key, [a.form, b.form])  # type: ignore[list-item]
                err = [frozenset(denied), frozenset(p_ids)]  # the denials are ERR in the value world, the positives in the other
    else:  # set-valued
        disputed = sorted((m for m in pos_by if m in neg_by), key=lambda m: canonical_json(m))
        if len(disputed) > 1:
            raise KernelUnsupported(
                f"set-valued key {key.entity}/{key.attr}: {len(disputed)} members both affirmed and denied have no oracle yet"
            )
        base_members = sorted((m for m in pos_by if m not in neg_by), key=lambda m: canonical_json(m))
        base_ids = sorted(i for m in base_members for i in pos_by[m])
        if not disputed:
            if pos_by:
                fam = {frozenset(base_members)}
                dist[frozenset(base_members)] = frozenset({frozenset(base_ids)})
                status, est = KernelStatus.ESTABLISHED, add(SetForm(values=tuple(base_members)), base_ids)
            else:
                fam = {frozenset()}
                ws = sorted(neg_by, key=lambda w: canonical_json(w))
                dist[frozenset()] = frozenset({frozenset(sorted(i for w in ws for i in neg_by[w]))})
                if len(ws) == 1:
                    status, est = KernelStatus.ESTABLISHED_FALSE, add(NotMemberForm(value=ws[0]), neg_by[ws[0]])
                else:
                    status = KernelStatus.UNKNOWN
                    alts = _sorted_cands(key, [NotMemberForm(value=w) for w in ws])
                    sup = {c.id: _support(neg_by[c.form.value]) for c in alts}  # type: ignore[union-attr]
        else:
            (d,) = disputed
            with_d = sorted({*base_members, d}, key=lambda m: canonical_json(m))
            inc_ids = sorted([*base_ids, *pos_by[d]])
            exc_ids = sorted([*base_ids, *neg_by[d]])
            fam = {frozenset(with_d), frozenset(base_members)}
            dist[frozenset(with_d)] = frozenset({frozenset(inc_ids)})
            dist[frozenset(base_members)] = frozenset({frozenset(exc_ids)})
            status = KernelStatus.UNRESOLVED
            a = add(SetForm(values=tuple(with_d)), inc_ids)
            b = add(SetForm(values=tuple(base_members)), exc_ids) if base_members else add(NotMemberForm(value=d), neg_by[d])
            alts = _sorted_cands(key, [a.form, b.form])  # type: ignore[list-item]
            err = [frozenset(neg_by[d]), frozenset(pos_by[d])]

    seg = PSegment(valid_from=None, valid_to=None, kernel_status=status, established=est, alternatives=alts,
                   support={cid: s for cid, s in sup.items() if cid in {c.id for c in ([est] if est else []) + list(alts)}})
    return PolarityJustification(
        key=key, spec=spec, evidence=evidence, interpretations=frozenset(), relax_level=0, profile=prof,
        policy=policy_of(semantic), all_ids=all_ids, _segment=seg, _fam=frozenset(fam), _dist=dist,
        _err=tuple(err),
    )
