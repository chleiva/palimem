"""Decision policy (T-D3): commit / abstain / ask as a pure function of a justified belief view."""

from __future__ import annotations

import pytest

from palimem.admission import derive_ulid
from palimem.policy import (
    JUSTIFIED,
    LWW,
    PRESETS,
    RECENCY,
    DecisionContext,
    PolicyError,
    PolicyObject,
    Selector,
    decide,
    rests_on_single_origin_group,
    score,
)
from palimem.types import (
    BeliefView,
    Candidate,
    Decision,
    EmptyForm,
    Inference,
    KernelStatus,
    Key,
    NotValueForm,
    RuleFired,
    Segment,
    Support,
    ValidationError,
    ValueForm,
)

KEY = Key(entity="alice", attr="employer")


def rid(ref: str) -> str:
    return derive_ulid("policy-test", ref)


def cand(v: str) -> Candidate:
    return Candidate(key=KEY, form=ValueForm(value=v))


def view(segment: Segment, *, complete: bool = True) -> BeliefView:
    return BeliefView(
        key=KEY,
        version=1,
        required_generation=1,
        completed_generation=1 if complete else 0,
        inference=Inference(complete=True) if complete else Inference(complete=False, reason="pending"),
        segment=segment,
        ref="belief:alice/employer@1",
    )


def unresolved(supports: dict[str, tuple[str, ...]]) -> BeliefView:
    """Unresolved segment; ``supports`` maps candidate value -> report refs of one environment each."""
    cands = [cand(v) for v in supports]
    return view(
        Segment(
            valid_from=None,
            valid_to=None,
            kernel_status=KernelStatus.UNRESOLVED,
            alternatives=tuple(cands),
            support={c.id: tuple(Support(environment=(rid(r),)) for r in supports[c.form.value]) for c in cands},  # type: ignore[union-attr]
        )
    )


def ctx(lsn: dict[str, int], cls: dict[str, str] | None = None, group: dict[str, str] | None = None) -> DecisionContext:
    return DecisionContext(
        report_lsn={rid(k): v for k, v in lsn.items()},
        report_source_class={rid(k): v for k, v in (cls or {}).items()},
        report_origin_group={rid(k): v for k, v in (group or {}).items()},
    )


# ---------------------------------------------------------------- kernel statuses

def test_established_commits_without_any_policy_rule():
    est = cand("Acme")
    v = view(Segment(valid_from=None, valid_to=None, kernel_status=KernelStatus.ESTABLISHED, established=est))
    a = decide(v, JUSTIFIED)
    assert (a.decision, a.kernel_status, a.assertion, a.alternatives) == (Decision.COMMIT, KernelStatus.ESTABLISHED, est, ())
    assert a.policy.rule_fired is RuleFired.NONE and a.policy.version == 1
    assert a.confidence is None  # the score is uncalibrated and never exposed as confidence


def test_negative_and_empty_establishments_commit_as_they_are():
    for status, form in (
        (KernelStatus.ESTABLISHED_EMPTY, EmptyForm()),
        (KernelStatus.ESTABLISHED_FALSE, NotValueForm(value="Acme")),
    ):
        c = Candidate(key=KEY, form=form)
        v = view(Segment(valid_from=None, valid_to=None, kernel_status=status, established=c))
        a = decide(v, LWW)
        assert (a.decision, a.kernel_status, a.assertion) == (Decision.COMMIT, status, c)


def test_unknown_abstains():
    v = view(Segment(valid_from=None, valid_to=None, kernel_status=KernelStatus.UNKNOWN))
    for p in PRESETS.values():
        a = decide(v, p)
        assert (a.decision, a.kernel_status, a.assertion) == (Decision.ABSTAIN, KernelStatus.UNKNOWN, None)


def test_incomplete_inference_is_not_decidable():
    v = view(Segment(valid_from=None, valid_to=None, kernel_status=KernelStatus.UNKNOWN), complete=False)
    with pytest.raises(PolicyError):
        decide(v, JUSTIFIED)


# ---------------------------------------------------------------- unresolved: three presets

def two_way() -> tuple[BeliefView, DecisionContext]:
    # Acme reported earlier (lsn 1) by a trusted source; Globex reported later (lsn 5) by a low one
    v = unresolved({"Acme": ("r1",), "Globex": ("r2",)})
    return v, ctx({"r1": 1, "r2": 5}, {"r1": "trusted", "r2": "low"}, {"r1": "g1", "r2": "g2"})


def test_justified_never_selects_it_asks_and_keeps_the_kernel_status():
    v, c = two_way()
    a = decide(v, JUSTIFIED, c)
    assert (a.decision, a.kernel_status, a.assertion) == (Decision.ASK, KernelStatus.UNRESOLVED, None)
    assert a.policy.rule_fired is RuleFired.ASK
    assert a.inquiry is not None and {x.id for x in a.inquiry.competing} == {x.id for x in v.segment.alternatives}
    assert len(a.alternatives) == 2


def test_lww_commits_the_newest_but_the_kernel_stays_unresolved():
    v, c = two_way()
    a = decide(v, LWW, c)
    assert a.decision is Decision.COMMIT
    assert a.kernel_status is KernelStatus.UNRESOLVED  # a policy commitment never establishes a belief
    assert a.assertion == cand("Globex")
    assert [x.id for x in a.alternatives] == [cand("Acme").id]
    assert a.justified.segment.kernel_status is KernelStatus.UNRESOLVED and a.justified.segment.established is None
    assert a.policy.rule_fired is RuleFired.THRESHOLD


def test_recency_asks_when_the_newest_is_outweighed_and_commits_when_it_is_not():
    v, c = two_way()  # newest is low-class and alone against a trusted report: outweighed
    a = decide(v, RECENCY, c)
    assert (a.decision, a.policy.rule_fired) == (Decision.ASK, RuleFired.ASK)

    v2 = unresolved({"Acme": ("r1",), "Globex": ("r2",)})
    c2 = ctx({"r1": 1, "r2": 5}, {"r1": "low", "r2": "trusted"}, {"r1": "g1", "r2": "g2"})
    a2 = decide(v2, RECENCY, c2)
    assert (a2.decision, a2.assertion) == (Decision.COMMIT, cand("Globex"))


def test_confidence_selector_uses_priors_and_counts_an_origin_group_once():
    pol = PolicyObject(version=2, name="conf", priors=JUSTIFIED.priors, abstain_threshold=0.0, ask_threshold=0.5)
    # two reports from ONE origin group for Acme vs one trusted report from another group for Globex
    v = unresolved({"Acme": ("r1", "r2"), "Globex": ("r3",)})
    c = ctx({"r1": 1, "r2": 2, "r3": 3}, {"r1": "standard", "r2": "standard", "r3": "trusted"}, {"r1": "g", "r2": "g", "r3": "h"})
    a = decide(v, pol, c)
    assert a.decision is Decision.COMMIT and a.assertion == cand("Globex")
    assert a.policy.rule_fired is RuleFired.PRIOR and a.policy.version == 2
    # distinct groups would have made Acme win
    c2 = ctx({"r1": 1, "r2": 2, "r3": 3}, {"r1": "standard", "r2": "standard", "r3": "trusted"}, {"r1": "g1", "r2": "g2", "r3": "h"})
    a2 = decide(v, pol, c2)
    assert a2.assertion == cand("Acme")


def test_exact_ties_never_commit():
    pol = PolicyObject(version=1, priors=JUSTIFIED.priors, abstain_threshold=0.0, ask_threshold=0.0)
    v = unresolved({"Acme": ("r1",), "Globex": ("r2",)})
    c = ctx({"r1": 1, "r2": 2}, {"r1": "standard", "r2": "standard"}, {"r1": "g1", "r2": "g2"})
    a = decide(v, pol, c)  # symmetric evidence, thresholds that would commit: still no commit
    assert a.decision is Decision.ASK
    same_lsn = ctx({"r1": 3, "r2": 3}, {"r1": "standard", "r2": "standard"}, {"r1": "g1", "r2": "g2"})
    assert decide(v, LWW, same_lsn).decision is Decision.ASK  # equal recency is a tie as well


def test_abstain_region():
    pol = PolicyObject(version=1, priors=JUSTIFIED.priors, abstain_threshold=0.9, ask_threshold=0.95)
    v, c = two_way()
    a = decide(v, pol, c)  # the best candidate's score is below 0.9
    assert (a.decision, a.policy.rule_fired) == (Decision.ABSTAIN, RuleFired.THRESHOLD)
    # ruling 16: an abstain carries an inquiry too (what would settle it), not only an ask
    assert len(a.alternatives) == 2 and a.inquiry is not None
    assert {x.id for x in a.inquiry.competing} == {x.id for x in a.alternatives}
    assert a.inquiry.missing == (v.key,) and a.inquiry.resolvers


# ---------------------------------------------------------------- purity, validation, helpers

def test_decide_is_pure():
    v, c = two_way()
    assert decide(v, RECENCY, c).to_json() == decide(v, RECENCY, c).to_json()
    for p in PRESETS.values():
        assert decide(v, p, c) == decide(v, p, c)


def test_policy_object_validation():
    with pytest.raises(ValidationError):
        PolicyObject(version=1, priors={}, abstain_threshold=0.8, ask_threshold=0.2)
    with pytest.raises(ValidationError):
        PolicyObject(version=1, priors={}, abstain_threshold=0.0, ask_threshold=1.5)
    with pytest.raises(ValidationError):
        PolicyObject(version=0, priors={}, abstain_threshold=0.0, ask_threshold=0.5)
    with pytest.raises(ValidationError):
        PolicyObject(version=1, priors={"x": float("nan")}, abstain_threshold=0.0, ask_threshold=0.5)
    assert set(PRESETS) == {"justified", "recency", "lww", "abstain"}
    assert PRESETS["lww"].selector is Selector.RECENCY and PRESETS["justified"].selector is Selector.CONFIDENCE


def test_score_is_a_ranking_signal_inside_the_open_interval():
    v, c = two_way()
    alts = v.segment.alternatives
    s = [score(v, a, alts, JUSTIFIED, c) for a in alts]
    assert all(0.0 < x < 1.0 for x in s) and s[0] > s[1]  # trusted beats low


def test_single_origin_group_is_visible():
    v = unresolved({"Acme": ("r1", "r2"), "Globex": ("r3",)})
    c = ctx({"r1": 1, "r2": 2, "r3": 3}, group={"r1": "g", "r2": "g", "r3": "h"})
    acme, globex = v.segment.alternatives
    assert rests_on_single_origin_group(v, acme, c)  # two reports, one origin group
    c2 = ctx({"r1": 1, "r2": 2, "r3": 3}, group={"r1": "g", "r2": "g2", "r3": "h"})
    assert not rests_on_single_origin_group(v, acme, c2)
    assert rests_on_single_origin_group(v, globex, c2)
