"""Decision policy (T-D3): ``decide(justified, policy, context) -> Resolved``, a pure function.

``kernel_status`` and ``decision`` are separate top-level fields of the answer. The kernel's status
is invariant under policy; the policy only decides whether to **commit**, **abstain** or **ask**.
Selecting one of several unresolved alternatives is a commitment *by the policy*: the answer keeps
``kernel_status = unresolved`` and the chosen candidate is its ``assertion``; it never becomes an
established kernel belief. Reliability (source-class priors) enters here, never inside the kernel.

The policy object is ``{version, priors, abstain_threshold, ask_threshold, utility}`` (design v0.3)
plus ``selector``, which says how one alternative is picked when several are unresolved; it has
no semantic switches (``self_update`` lives in the semantic configuration, not here).

Score and thresholds. ``score(c)`` is the study's support heuristic (``palimpsest/support.py``) reduced
to what a hand-case needs: each *origin group* contributes the largest prior weight among its reports
for the candidate (a group counts once), and ``p(c) = sigmoid(pro - con + bias)`` with a +0.5 bias when
nothing contradicts. **This score is an uncalibrated ranking signal**: the pre-registered calibration
hypothesis failed, so it is never exposed as ``Resolved.confidence`` (always ``None``) and must not be
read as a probability. For the best candidate ``p``:

* ``p >= ask_threshold``    -> commit (to that candidate),
* ``abstain_threshold <= p < ask_threshold`` -> ask,
* ``p < abstain_threshold`` -> abstain.

``utility`` is reserved for learned policies (R3.2) and is not used by the shipped presets.

Presets (``PRESETS``): ``justified`` never selects among unresolved alternatives and asks;
``recency`` picks the alternative with the newest report and commits unless it is outweighed
(``p < 0.5`` -> ask); ``lww`` always commits the newest. The semantic half of "recency" (the
same-origin self-update rule P0cSU) belongs to ``SemanticConfig`` and is selected there, not here.
Reproducing the study's regime table through the adapter is a later task.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType

from palimem.types import (
    BeliefOfForm,
    BeliefView,
    Candidate,
    Decision,
    ExplanationState,
    Inquiry,
    KernelStatus,
    LogEntry,
    PolicyInfo,
    Resolved,
    RuleFired,
    SegmentBounds,
    Support,
    ValidationError,
)
from palimem.types._codec import check_nat

DEFAULT_BIAS = 0.5
_P_MAX = 1.0 - 1e-9  # the score is never certainty, so ask_threshold = 1.0 means "never commit"
_TIE = 1e-12


class PolicyError(ValueError):
    """``decide`` was called with something the policy cannot answer (e.g. incomplete inference)."""


class Selector(str, Enum):
    CONFIDENCE = "confidence"  # pick the best-supported alternative
    RECENCY = "recency"  # pick the alternative whose newest report is newest


def _freeze(m: Mapping[str, float], ctx: str) -> Mapping[str, float]:
    for k, v in m.items():
        if not isinstance(k, str) or isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
            raise ValidationError(f"{ctx}: expected str -> finite number")
    return MappingProxyType(dict(m))


@dataclass(frozen=True, kw_only=True)
class PolicyObject:
    version: int
    priors: Mapping[str, float]  # source class -> log-odds weight; "*" is the weight of an unknown class
    abstain_threshold: float
    ask_threshold: float
    utility: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))
    selector: Selector = Selector.CONFIDENCE
    name: str = ""

    def __post_init__(self) -> None:
        check_nat(self.version, "policy.version", minimum=1)
        for n, v in (("abstain_threshold", self.abstain_threshold), ("ask_threshold", self.ask_threshold)):
            if isinstance(v, bool) or not isinstance(v, int | float) or not 0.0 <= v <= 1.0:
                raise ValidationError(f"policy.{n} must be within [0, 1]")
        if self.abstain_threshold > self.ask_threshold:
            raise ValidationError("policy: abstain_threshold must not exceed ask_threshold")
        if not isinstance(self.selector, Selector):
            raise ValidationError("policy.selector: not a Selector")
        object.__setattr__(self, "priors", _freeze(self.priors, "policy.priors"))
        object.__setattr__(self, "utility", _freeze(self.utility, "policy.utility"))

    def prior_of(self, source_class: str | None) -> float:
        if source_class is not None and source_class in self.priors:
            return self.priors[source_class]
        return self.priors.get("*", 0.5)


DEFAULT_PRIORS: Mapping[str, float] = MappingProxyType({"trusted": 2.2, "standard": 1.2, "low": 0.3, "*": 0.5})

JUSTIFIED = PolicyObject(
    version=1, name="justified", priors=DEFAULT_PRIORS, abstain_threshold=0.0, ask_threshold=1.0
)
RECENCY = PolicyObject(
    version=1, name="recency", priors=DEFAULT_PRIORS, abstain_threshold=0.0, ask_threshold=0.5, selector=Selector.RECENCY
)
LWW = PolicyObject(
    version=1, name="lww", priors=DEFAULT_PRIORS, abstain_threshold=0.0, ask_threshold=0.0, selector=Selector.RECENCY
)
PRESETS: Mapping[str, PolicyObject] = MappingProxyType({p.name: p for p in (JUSTIFIED, RECENCY, LWW)})


@dataclass(frozen=True)
class DecisionContext:
    """Per-report facts the policy needs and the kernel does not carry: log order (recency),
    source class (priors) and origin group (a group counts once)."""

    report_lsn: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    report_source_class: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    report_origin_group: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def from_entries(cls, entries: Iterable[LogEntry]) -> DecisionContext:
        lsn: dict[str, int] = {}
        cls_: dict[str, str] = {}
        grp: dict[str, str] = {}
        for e in entries:
            rid = e.report.id
            assert rid is not None
            lsn[rid] = e.lsn
            cls_[rid] = e.report.source.cls
            grp[rid] = e.report.origin_group
        return cls(MappingProxyType(lsn), MappingProxyType(cls_), MappingProxyType(grp))


# ---------------------------------------------------------------- scoring helpers

def _supports(view: BeliefView, c: Candidate) -> tuple[Support, ...]:
    return view.segment.support.get(c.id, ())


def _report_ids(supports: Sequence[Support]) -> set[str]:
    return {rid for s in supports for rid in s.environment}


def group_weights(view: BeliefView, c: Candidate, policy: PolicyObject, ctx: DecisionContext) -> dict[str, float]:
    """origin group -> the largest prior among that group's reports supporting ``c`` (a group counts once)."""
    out: dict[str, float] = {}
    for rid in sorted(_report_ids(_supports(view, c))):
        g = ctx.report_origin_group.get(rid, f"report:{rid}")  # unknown origin: treated as its own group
        w = policy.prior_of(ctx.report_source_class.get(rid))
        out[g] = max(out.get(g, w), w)
    return out


def score(view: BeliefView, c: Candidate, others: Sequence[Candidate], policy: PolicyObject, ctx: DecisionContext) -> float:
    """Uncalibrated ranking score in (0, 1): the study's origin-grouped log-odds support, simplified."""
    pro = sum(group_weights(view, c, policy, ctx).values())
    con = sum(sum(group_weights(view, o, policy, ctx).values()) for o in others if o.id != c.id)
    x = pro - con + (DEFAULT_BIAS if con == 0 else 0.0)
    return min(1.0 / (1.0 + math.exp(-x)), _P_MAX)


def newest_lsn(view: BeliefView, c: Candidate, ctx: DecisionContext) -> int:
    ids = _report_ids(_supports(view, c))
    return max((ctx.report_lsn.get(rid, -1) for rid in ids), default=-1)


def rests_on_single_origin_group(view: BeliefView, c: Candidate, ctx: DecisionContext) -> bool:
    """True when every environment supporting ``c`` rests on one origin group (SEC-39): a commit
    that rests on a single origin group must be visible as such, and only a second origin group's
    confirmation raises it above that."""
    groups = {ctx.report_origin_group.get(rid, f"report:{rid}") for rid in _report_ids(_supports(view, c))}
    return len(groups) <= 1


def _select(
    view: BeliefView, alts: Sequence[Candidate], policy: PolicyObject, ctx: DecisionContext
) -> tuple[Candidate | None, float]:
    """The alternative the selector picks (``None`` on an exact tie) and its score."""
    scores = {c.id: score(view, c, alts, policy, ctx) for c in alts}
    if policy.selector is Selector.CONFIDENCE:
        ranked = sorted(alts, key=lambda c: (-scores[c.id], c.id))
        best = ranked[0]
        tie = abs(scores[ranked[1].id] - scores[best.id]) <= _TIE
        return (None if tie else best), scores[best.id]
    ranked = sorted(alts, key=lambda c: (-newest_lsn(view, c, ctx), c.id))
    best = ranked[0]
    tie = newest_lsn(view, ranked[1], ctx) == newest_lsn(view, best, ctx)
    return (None if tie else best), (max(scores.values()) if tie else scores[best.id])


def decide(
    justified: BeliefView,
    policy: PolicyObject,
    context: DecisionContext | None = None,
    *,
    provenance: Sequence[Support] | None = None,
    explanation: ExplanationState = ExplanationState.COMPLETE,
) -> Resolved:
    """Turn a justified belief view into an answer (commit | abstain | ask). Pure: same inputs, same output."""
    if not justified.inference.complete:
        raise PolicyError("decide() needs a complete belief; an incomplete one is a ResourceLimited answer, not a decision")
    ctx = context or DecisionContext()
    seg = justified.segment
    status = seg.kernel_status
    prov: tuple[Support, ...] = (
        tuple(provenance)
        if provenance is not None
        else tuple(s for cid in sorted(seg.support) for s in seg.support[cid])
    )

    def answer(
        decision: Decision,
        rule: RuleFired,
        assertion: Candidate | None = None,
        alternatives: tuple[Candidate, ...] = (),
        inquiry: Inquiry | None = None,
    ) -> Resolved:
        return Resolved(
            segment=SegmentBounds(valid_from=seg.valid_from, valid_to=seg.valid_to),
            kernel_status=status,
            decision=decision,
            justified=justified,
            assertion=assertion,
            alternatives=alternatives,
            provenance=prov,
            explanation=explanation,
            policy=PolicyInfo(version=policy.version, rule_fired=rule),
            confidence=None,  # the score is uncalibrated; see the module docstring
            inquiry=inquiry,
        )

    # Attribution safety (design: an attribution establishes ``belief_of(holder, P)`` and never ``P``): when every
    # candidate of the segment is an attributed claim, the kernel status is about the *attribution*, and the content
    # the key is asked about is unknown. Committing to a ``belief_of`` candidate would hand a consumer that reads
    # only ``decision``/``assertion`` an attribution as if it were a value, so the policy never commits here: it
    # asks (the attributed candidates stay visible as ``alternatives`` and ``inquiry.competing``; the key's content
    # is the missing evidence). ``kernel_status`` is unchanged, as design v0.3 rows 15 and 19 require.
    attributed = ([seg.established] if seg.established is not None else []) + list(seg.alternatives)
    if attributed and all(isinstance(c.form, BeliefOfForm) for c in attributed):
        cands = tuple(attributed)
        return answer(
            Decision.ASK, RuleFired.ASK, alternatives=cands, inquiry=Inquiry(competing=cands, missing=(cands[0].key,))
        )

    if status in (KernelStatus.ESTABLISHED, KernelStatus.ESTABLISHED_EMPTY, KernelStatus.ESTABLISHED_FALSE):
        assert seg.established is not None
        return answer(Decision.COMMIT, RuleFired.NONE, assertion=seg.established)
    if status is KernelStatus.UNKNOWN:
        return answer(Decision.ABSTAIN, RuleFired.NONE)

    # unresolved: the kernel lists alternatives; reliability and recency enter only here
    alts = seg.alternatives
    chosen, p = _select(justified, alts, policy, ctx)
    if chosen is not None and p >= policy.ask_threshold:
        rule = RuleFired.PRIOR if policy.selector is Selector.CONFIDENCE else RuleFired.THRESHOLD
        rest = tuple(c for c in alts if c.id != chosen.id)
        return answer(Decision.COMMIT, rule, assertion=chosen, alternatives=rest)
    if p >= policy.abstain_threshold:
        return answer(Decision.ASK, RuleFired.ASK, alternatives=alts, inquiry=Inquiry(competing=alts))
    return answer(Decision.ABSTAIN, RuleFired.THRESHOLD, alternatives=alts)
