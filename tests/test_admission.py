"""Admission (T-D1), authority (T-D2) and attribution (T-D5) on hand-built logs."""

from __future__ import annotations

import pytest

from palimem.admission import (
    AdmissionConfig,
    Admitter,
    SourceStatus,
    equivalent,
    origin_group_count,
    proposition_signature,
)
from palimem.types import (
    AdmissionOutcome as Out,
)
from palimem.types import (
    AdmissionReason as Why,
)
from palimem.types import (
    Attr,
    AttrClass,
    AuthorityRule,
    BeliefOfProp,
    Cue,
    EnumerationProp,
    Key,
    KeyScope,
    MemberProp,
    NotValueProp,
    Origin,
    Power,
    Profile,
    Schema,
    Targets,
    ValidationError,
    ValueProp,
    ValueType,
    Who,
    WhoKind,
)
from tests._adm import Log

KEY = Key(entity="alice", attr="employer")


def ev(log: Log, config: AdmissionConfig | None = None, *, as_of: int | None = None):
    a = Admitter(config or AdmissionConfig())
    return a, a.evaluate(log.list, as_of_lsn=as_of)


def dec(log: Log, ref: str, config: AdmissionConfig | None = None, *, as_of: int | None = None):
    _, e = ev(log, config, as_of=as_of)
    return e.decisions[log.id(ref)]


# ---------------------------------------------------------------- equivalence (S-01)

def test_equivalence_normalises_values():
    assert equivalent(ValueProp(value="  ACME   Corp "), ValueProp(value="acme corp"))
    assert equivalent(ValueProp(value=2.0), ValueProp(value=2))
    assert not equivalent(ValueProp(value=True), ValueProp(value=1))  # bool stays distinct from int
    assert not equivalent(ValueProp(value="Acme"), ValueProp(value="Globex"))


def test_member_is_not_equivalent_to_enumeration_of_it():
    assert not equivalent(MemberProp(value="a"), EnumerationProp(values=("a",)))
    assert equivalent(EnumerationProp(values=("b", "A")), EnumerationProp(values=("a", "B")))
    assert not equivalent(MemberProp(value="a"), ValueProp(value="a"))  # same value, different form
    assert not equivalent(NotValueProp(value="a"), ValueProp(value="a"))


def test_attribution_is_not_equivalent_to_its_content():
    inner = ValueProp(value="Acme")
    assert not equivalent(BeliefOfProp(holder="alice", proposition=inner), inner)
    assert equivalent(
        BeliefOfProp(holder="Alice", proposition=inner), BeliefOfProp(holder=" alice ", proposition=ValueProp(value="acme"))
    )
    assert proposition_signature(inner) != proposition_signature(MemberProp(value="Acme"))


# ---------------------------------------------------------------- basic outcomes (S-03)

def test_external_report_is_admitted_with_reason_and_version():
    log = Log()
    log.add("r1")
    d = dec(log, "r1", AdmissionConfig(admission_version=3))
    assert (d.record.outcome, d.record.reason, d.record.admission_version) == (Out.ADMISSIBLE, Why.ADMITTED, 3)


@pytest.mark.parametrize(
    "origin",
    [Origin.AGENT_HYPOTHESIS, Origin.AGENT_STATEMENT, Origin.PLAN, Origin.SIMULATION, Origin.COUNTERFACTUAL],
)
def test_agent_class_origins_are_never_admissible(origin):
    log = Log()
    log.add("r1", origin=origin, actor="agent:planner", source="agent:planner")
    d = dec(log, "r1")
    assert (d.record.outcome, d.record.reason) == (Out.EXCLUDED, Why.ORIGIN_NOT_ADMISSIBLE)
    es = Admitter(AdmissionConfig()).evidence_set(log.list, KEY)
    assert es.direct == ()


def test_blocked_class_maps_to_excluded_and_is_never_confirmable():
    log = Log()
    log.add("r1", source="spam", cls="blocked")
    d = dec(log, "r1")
    assert (d.record.outcome, d.record.reason) == (Out.EXCLUDED, Why.SOURCE_BLOCKED)
    assert d.record.admission_version == 1

    log2 = Log()
    log2.add("q", source="q1", cls="quarantined")
    log2.add("b", source="bad", cls="blocked")  # equivalent, but blocked: cannot confirm
    assert dec(log2, "q").record.outcome is Out.QUARANTINED


def test_source_override_beats_class():
    log = Log()
    log.add("r1", source="wire", cls="standard")
    cfg = AdmissionConfig(source_status={"wire": SourceStatus.BLOCKED})
    assert dec(log, "r1", cfg).record.reason is Why.SOURCE_BLOCKED


def test_decision_ids_are_deterministic():
    log = Log()
    log.add("r1")
    assert dec(log, "r1").record.id == dec(log, "r1").record.id
    assert dec(log, "r1", AdmissionConfig(admission_version=2)).record.id != dec(log, "r1").record.id


# ---------------------------------------------------------------- derived confirmation (S-01)

def test_quarantined_report_is_confirmed_by_equivalent_report_from_another_origin_group():
    log = Log()
    log.add("q", source="q1", cls="quarantined", group="g_q")
    assert dec(log, "q").record.outcome is Out.QUARANTINED
    log.add("c", value="ACME ", source="reuters", group="g_reuters")
    d = dec(log, "q")
    assert (d.record.outcome, d.record.reason) == (Out.ADMISSIBLE, Why.CONFIRMED)
    assert d.record.confirmed_by == (log.id("c"),)
    es = Admitter(AdmissionConfig()).evidence_set(log.list, KEY)
    assert {e.report.id for e in es.direct} == {log.id("q"), log.id("c")}


def test_confirmation_arrives_with_the_confirmer_not_before():
    log = Log()
    log.add("q", source="q1", cls="quarantined")
    log.add("c", source="reuters")
    assert dec(log, "q", as_of=1).record.outcome is Out.QUARANTINED
    assert dec(log, "q", as_of=2).record.reason is Why.CONFIRMED


def test_same_origin_group_does_not_confirm():
    log = Log()
    log.add("q", source="q1", cls="quarantined", group="g_same")
    log.add("c", source="mirror", group="g_same")
    assert dec(log, "q").record.outcome is Out.QUARANTINED


def test_agent_origin_never_confirms():
    log = Log()
    log.add("q", source="q1", cls="quarantined")
    log.add("h", origin=Origin.AGENT_HYPOTHESIS, actor="agent:a1", source="agent:a1", group="agent:a1")
    log.add("h2", origin=Origin.AGENT_STATEMENT, actor="agent:a1", source="agent:a1", group="agent:a1b")
    assert dec(log, "q").record.outcome is Out.QUARANTINED


def test_two_quarantined_sources_do_not_confirm_each_other():
    log = Log()
    log.add("q1", source="qa", cls="quarantined")
    log.add("q2", source="qb", cls="quarantined")
    assert dec(log, "q1").record.outcome is Out.QUARANTINED
    assert dec(log, "q2").record.outcome is Out.QUARANTINED
    log.add("t", source="third")  # a third, already admissible origin group confirms both
    assert dec(log, "q1").record.reason is Why.CONFIRMED
    assert dec(log, "q2").record.reason is Why.CONFIRMED


def test_confirmed_report_does_not_confirm_a_third():
    log = Log()
    log.add("q1", source="qa", cls="quarantined")
    log.add("q2", source="qb", cls="quarantined")
    log.add("t", source="third", value="Globex")  # equivalent to neither
    assert dec(log, "q1").record.outcome is Out.QUARANTINED
    log.add("c", source="fourth", value="Acme")  # confirms q1 and q2
    # q2 confirmed by c; q1 confirmed by c. Neither confirmed report is itself a confirmer:
    _, e = ev(log)
    assert e.decisions[log.id("q1")].record.confirmed_by == (log.id("c"),)
    assert e.decisions[log.id("q2")].record.confirmed_by == (log.id("c"),)


def test_member_report_cannot_confirm_an_enumeration():
    log = Log()
    log.add("q", source="q1", cls="quarantined", attr="tags", proposition=EnumerationProp(values=("a",)))
    log.add("m", source="reuters", attr="tags", proposition=MemberProp(value="a"))
    assert dec(log, "q").record.outcome is Out.QUARANTINED
    log.add("e", source="ap", attr="tags", proposition=EnumerationProp(values=("A",)))
    assert dec(log, "q").record.reason is Why.CONFIRMED


def test_confirmation_lapses_when_the_confirmer_is_withdrawn():
    log = Log()
    log.add("q", source="q1", cls="quarantined")
    log.add("c", source="reuters")
    assert dec(log, "q").record.reason is Why.CONFIRMED
    log.add("w", Cue.WITHDRAW, source="reuters", target="c")  # the confirmer's own source withdraws it
    assert dec(log, "q").record.outcome is Out.QUARANTINED


def test_origin_group_counts_once():
    log = Log()
    log.add("a", source="s1", group="g")
    log.add("b", source="s2", group="g")
    log.add("c", source="s3", group="h")
    assert origin_group_count(list(log.entries.values())) == 2


# ---------------------------------------------------------------- authority (S-02, S-07)

def test_default_authority_is_own_source_only():
    log = Log()
    log.add("a", source="press")
    log.add("w_other", Cue.WITHDRAW, source="wire", target="a")
    d = dec(log, "w_other")
    assert (d.record.outcome, d.record.reason, d.effective_cue) == (Out.EXCLUDED, Why.AUTHORITY_FAILED, Cue.ALLEGE)
    es = Admitter(AdmissionConfig()).evidence_set(log.list, KEY)
    assert [e.report.id for e in es.direct] == [log.id("a")]  # evidence intact
    assert [e.report.id for e in es.allegations] == [log.id("w_other")]

    log.add("w_own", Cue.WITHDRAW, source="press", target="a")
    d = dec(log, "w_own")
    assert (d.record.outcome, d.effective_cue, d.withdraws) == (Out.ADMISSIBLE, Cue.WITHDRAW, (log.id("a"),))
    es = Admitter(AdmissionConfig()).evidence_set(log.list, KEY)
    assert es.direct == () and log.id("a") in es.withdrawn


def test_origin_group_sharing_is_not_authority_in_the_product():
    log = Log()
    log.add("a", source="press", group="g_shared")
    log.add("w", Cue.WITHDRAW, source="wire", group="g_shared", target="a")
    assert dec(log, "w").effective_cue is Cue.ALLEGE


def test_unauthorised_correct_lands_as_allege_with_no_effect_in_the_product_profile():
    # design v0.3 (§Write API): a correction that fails the authority check is recorded as `allege`, exactly like a
    # failed withdraw or dispute: no effect on admissibility or the kernel (S-02 implementation note)
    log = Log()
    log.add("a", source="press", value="Acme")
    log.add("c", Cue.CORRECT, source="registry", value="Globex", target="a")
    d = dec(log, "c")
    assert d.effective_cue is Cue.ALLEGE and d.withdraws == ()
    assert d.record.outcome is Out.EXCLUDED
    es = Admitter(AdmissionConfig()).evidence_set(log.list, KEY)
    assert [e.report.id for e in es.direct] == [log.id("a")]  # the failed correction is no evidence, not even a rival value
    assert {e.report.id for e in es.allegations} == {log.id("c")}  # but it stays visible to audits and the inquiry


def test_unauthorised_correct_stays_an_ordinary_assert_in_the_compat_profile():
    # the paper's behaviour (A-CORR: a cross-origin correction is a competing assertion carrying a correction cue)
    # is unchanged under `revise-stream-v1`
    log = Log()
    log.add("a", source="press", value="Acme")
    log.add("c", Cue.CORRECT, source="registry", value="Globex", target="a")
    cfg = AdmissionConfig(profile=Profile.REVISE_STREAM_V1)
    d = dec(log, "c", cfg)
    assert d.effective_cue is Cue.CORRECT and d.withdraws == ()
    assert d.record.outcome is Out.ADMISSIBLE
    es = Admitter(cfg).evidence_set(log.list, KEY)
    assert {e.report.id for e in es.direct} == {log.id("a"), log.id("c")}


def test_authorised_correct_withdraws_its_target_and_asserts_the_new_value():
    log = Log()
    log.add("a", source="press", value="Acme")
    log.add("c", Cue.CORRECT, source="press", value="Globex", target="a")
    es = Admitter(AdmissionConfig()).evidence_set(log.list, KEY)
    assert [e.report.id for e in es.direct] == [log.id("c")]
    assert es.withdrawn[log.id("a")].kind == "self_correction"


def test_target_must_exist_and_precede():
    log = Log()
    log.add("w", Cue.WITHDRAW, source="press", target="01ARZ3NDEKTSV4RRFFQ69G5FAV")
    assert dec(log, "w").record.reason is Why.TARGET_MISSING


def test_agent_withdraws_its_own_agent_statement_from_an_earlier_session():
    log = Log()
    log.add("h", origin=Origin.AGENT_STATEMENT, actor="agent:planner", source="agent:planner", group="agent:planner")
    log.add("w", Cue.WITHDRAW, origin=Origin.AGENT_STATEMENT, actor="agent:planner", source="agent:planner", target="h")
    _, e = ev(log)
    assert e.decisions[log.id("w")].withdraws == (log.id("h"),)
    assert log.id("h") in e.withdrawn


def test_a_different_agent_cannot_withdraw_it():
    log = Log()
    log.add("h", origin=Origin.AGENT_STATEMENT, actor="agent:planner", source="agent:planner", group="agent:planner")
    log.add("w", Cue.WITHDRAW, origin=Origin.AGENT_STATEMENT, actor="agent:other", source="agent:other", target="h")
    assert dec(log, "w").effective_cue is Cue.ALLEGE


def test_agent_can_never_withdraw_or_correct_external_evidence():
    log = Log()
    log.add("a", source="press", actor="connector:press")
    # even though agent and target share a source id (a forged one), the invariant holds
    log.add("w", Cue.WITHDRAW, origin=Origin.AGENT_STATEMENT, actor="agent:planner", source="press", target="a")
    log.add("c", Cue.CORRECT, origin=Origin.AGENT_STATEMENT, actor="agent:planner", source="press", value="X", target="a")
    _, e = ev(log)
    assert e.decisions[log.id("w")].effective_cue is Cue.ALLEGE
    assert e.decisions[log.id("c")].withdraws == ()
    assert log.id("a") not in e.withdrawn


def test_agent_withdraw_is_refused_even_under_wildcard_rules():
    cfg = AdmissionConfig(
        rules=(
            AuthorityRule(who=Who(kind=WhoKind.ANY), may=(Power.WITHDRAW, Power.CORRECT, Power.DISPUTE), targets=Targets.REPORT),
            AuthorityRule(who=Who(kind=WhoKind.TARGET_SOURCE), may=(Power.WITHDRAW, Power.CORRECT)),
        )
    )
    log = Log()
    log.add("a", source="press")
    log.add("w", Cue.WITHDRAW, origin=Origin.AGENT_STATEMENT, actor="agent:planner", source="press", target="a")
    assert dec(log, "w", cfg).effective_cue is Cue.ALLEGE


def test_agent_dispute_needs_an_explicit_named_grant():
    log = Log()
    log.add("a", source="press", attr="employer")
    log.add("d", Cue.DISPUTE, origin=Origin.AGENT_STATEMENT, actor="agent:a1", source="agent:a1", target="a")
    assert dec(log, "d").effective_cue is Cue.ALLEGE  # no grant

    wildcard = AdmissionConfig(rules=(AuthorityRule(who=Who(kind=WhoKind.ANY), may=(Power.DISPUTE,)),))
    assert dec(log, "d", wildcard).effective_cue is Cue.ALLEGE  # wildcards never grant an agent a dispute

    grant = AdmissionConfig(
        rules=(
            AuthorityRule(
                who=Who(kind=WhoKind.PRINCIPAL, value="agent:a1"), may=(Power.DISPUTE,), on=KeyScope(attr="employer*")
            ),
        )
    )
    d = dec(log, "d", grant)
    assert (d.effective_cue, d.record.outcome) == (Cue.DISPUTE, Out.ADMISSIBLE)
    es = Admitter(grant).evidence_set(log.list, KEY)
    assert [e.report.id for e in es.disputes] == [log.id("d")]
    assert [e.report.id for e in es.direct] == [log.id("a")]  # a dispute does not remove evidence or quarantine the source

    log.add("a2", source="press", attr="salary")
    log.add("d2", Cue.DISPUTE, origin=Origin.AGENT_STATEMENT, actor="agent:a1", source="agent:a1", target="a2")
    assert dec(log, "d2", grant).effective_cue is Cue.ALLEGE  # outside the granted key scope


def test_rules_that_grant_an_agent_authority_over_external_evidence_cannot_even_be_built():
    with pytest.raises(ValidationError):
        AuthorityRule(who=Who(kind=WhoKind.PRINCIPAL, value="agent:a1"), may=(Power.WITHDRAW,), over_origins=(Origin.EXTERNAL_OBSERVATION,))


def test_grant_table_changes_are_admission_versions():
    log = Log()
    log.add("a", source="press")
    log.add("w", Cue.WITHDRAW, source="registry", target="a")
    v1 = AdmissionConfig()
    v2 = v1.successor(rules=(AuthorityRule(who=Who(kind=WhoKind.PRINCIPAL, value="connector:registry"), may=(Power.WITHDRAW,)),))
    assert v2.admission_version == 2 and v2.table().admission_version == 2
    assert dec(log, "w", v1).effective_cue is Cue.ALLEGE  # old version still answers as before
    d2 = dec(log, "w", v2)
    assert d2.effective_cue is Cue.WITHDRAW and d2.record.admission_version == 2
    with pytest.raises(ValueError):
        v1.successor(admission_version=9)


def test_per_attribute_authority_scopes_a_grant():
    schema = Schema(
        version=1,
        attrs=(
            Attr(
                name="employer",
                attr_class=AttrClass.SINGLE_CHANGEABLE,
                value_type=ValueType.STRING,
                authority=(AuthorityRule(who=Who(kind=WhoKind.PRINCIPAL, value="connector:registry"), may=(Power.WITHDRAW,)),),
            ),
            Attr(name="salary", attr_class=AttrClass.SINGLE_CHANGEABLE, value_type=ValueType.INT),
        ),
    )
    log = Log()
    log.add("a", source="press", attr="employer")
    log.add("s", source="press", attr="salary", value="10")
    log.add("w1", Cue.WITHDRAW, source="registry", attr="employer", target="a")
    log.add("w2", Cue.WITHDRAW, source="registry", attr="salary", target="s")
    e = Admitter(AdmissionConfig(), schema).evaluate(log.list)
    assert e.decisions[log.id("w1")].effective_cue is Cue.WITHDRAW
    assert e.decisions[log.id("w2")].effective_cue is Cue.ALLEGE


def test_source_level_extent_withdraws_every_report_of_the_source_including_later_ones():
    cfg = AdmissionConfig(
        rules=(AuthorityRule(who=Who(kind=WhoKind.PRINCIPAL, value="connector:registry"), may=(Power.WITHDRAW,), targets=Targets.SOURCE),)
    )
    log = Log()
    log.add("a", source="blog", cls="low")
    log.add("b", source="blog", cls="low", attr="salary", value="1")
    log.add("w", Cue.WITHDRAW, source="registry", cls="trusted", target="a")
    log.add("late", source="blog", cls="low")
    log.add("other", source="press")
    _, e = ev(log, cfg)
    assert set(e.withdrawn) == {log.id("a"), log.id("b"), log.id("late")}
    assert e.withdrawn[log.id("b")].kind == "source_withdraw"


# ---------------------------------------------------------------- compat profile (S-02)

COMPAT = AdmissionConfig(profile=Profile.REVISE_STREAM_V1)


def test_origin_group_authority_is_rejected_outside_the_compat_profile():
    rule = AuthorityRule(who=Who(kind=WhoKind.TARGET_ORIGIN_GROUP), may=(Power.CORRECT,))
    with pytest.raises(ValidationError):
        AdmissionConfig(profile=Profile.OPEN_WORLD, rules=(rule,))
    AdmissionConfig(profile=Profile.REVISE_STREAM_V1, rules=(rule,))


def test_compat_same_origin_different_source_correction_withdraws():
    log = Log()
    log.add("a", source="press", group="g_news")
    log.add("c", Cue.CORRECT, source="wire", group="g_news", value="Globex", target="a")
    _, e = ev(log, COMPAT)
    assert e.withdrawn[log.id("a")].kind == "self_correction"


def test_compat_cross_origin_correction_is_not_a_withdrawal_but_stays_evidence():
    log = Log()
    log.add("a", source="press", group="g_news")
    log.add("c", Cue.CORRECT, source="registry", group="g_reg", value="Globex", target="a")
    a, e = ev(log, COMPAT)
    assert e.withdrawn == {}
    assert {x.report.id for x in a.evidence_set_of(e, KEY).direct} == {log.id("a"), log.id("c")}


def test_compat_anyone_may_withdraw_a_report():
    log = Log()
    log.add("a", source="blog")
    log.add("w", Cue.WITHDRAW, source="registry", target="a")
    _, e = ev(log, COMPAT)
    assert log.id("a") in e.withdrawn


def _retracted_correction_log() -> Log:
    log = Log()
    log.add("a", source="press", value="Acme")
    log.add("c", Cue.CORRECT, source="press", value="Globex", target="a")
    log.add("w", Cue.WITHDRAW, source="press", target="c")  # the correction is itself withdrawn
    return log


def test_product_restores_the_target_of_a_withdrawn_correction():
    _, e = ev(_retracted_correction_log())
    assert log_id_in(e.withdrawn, "c") and not log_id_in(e.withdrawn, "a")


def test_compat_keeps_the_papers_behaviour_for_a_withdrawn_correction():
    log = _retracted_correction_log()
    _, e = ev(log, COMPAT)
    assert log.id("a") in e.withdrawn and log.id("c") in e.withdrawn  # a withdrawn actor keeps acting
    assert COMPAT.must_be_live is False
    assert AdmissionConfig().must_be_live is True


def log_id_in(withdrawn, ref):
    # helper used above: _retracted_correction_log builds ids deterministically via Log
    from palimem.admission import derive_ulid

    return derive_ulid("test-report", ref) in withdrawn


# ---------------------------------------------------------------- attribution (T-D5, S-11)

def test_two_independent_attributions_establish_the_attribution_and_never_the_content():
    log = Log()
    log.attributed("a1", "alice", "Acme", source="gossip1")
    log.attributed("a2", "alice", "acme", source="gossip2")
    es = Admitter(AdmissionConfig()).evidence_set(log.list, KEY)
    assert es.direct == ()  # P (alice's employer is Acme) is not evidence: unknown
    assert len(es.attributions) == 1
    att = es.attributions[0]
    assert att.proposition == BeliefOfProp(holder="alice", proposition=ValueProp(value="Acme"))
    assert len(att.entries) == 2 and len(att.origin_groups) == 2


def test_attributions_from_one_origin_group_count_once():
    log = Log()
    log.attributed("a1", "alice", "Acme", source="s1", group="g")
    log.attributed("a2", "alice", "Acme", source="s2", group="g")
    es = Admitter(AdmissionConfig()).evidence_set(log.list, KEY)
    assert es.attributions[0].origin_groups == ("g",)


def test_a_belief_of_proposition_never_leaks_into_direct_evidence_whatever_its_origin():
    log = Log()
    log.add("x", origin=Origin.EXTERNAL_OBSERVATION, proposition=BeliefOfProp(holder="bob", proposition=ValueProp(value="Acme")))
    es = Admitter(AdmissionConfig()).evidence_set(log.list, KEY)
    assert es.direct == () and len(es.attributions) == 1


def test_attributed_reports_cannot_act_and_do_not_confirm_their_content():
    log = Log()
    log.add("a", source="press")
    log.add("w", Cue.WITHDRAW, origin=Origin.ATTRIBUTED, source="press", target="a")
    assert dec(log, "w").record.reason is Why.ORIGIN_NOT_ADMISSIBLE

    log2 = Log()
    log2.add("q", source="q1", cls="quarantined")
    log2.attributed("att", "alice", "Acme", source="gossip")
    assert dec(log2, "q").record.outcome is Out.QUARANTINED


def test_belief_of_nesting_depth_is_limited_to_one():
    inner = BeliefOfProp(holder="b", proposition=ValueProp(value="x"))
    with pytest.raises(ValidationError):
        BeliefOfProp(holder="a", proposition=inner)


# ---------------------------------------------------------------- evidence sets and admit()

def test_admit_agrees_with_evaluate():
    log = Log()
    log.add("q", source="q1", cls="quarantined")
    log.add("c", source="reuters")
    a = Admitter(AdmissionConfig())
    for ref in ("q", "c"):
        entry = log.entries[ref]
        assert a.admit(entry, log.list).record == a.evaluate(log.list, as_of_lsn=entry.lsn).decisions[log.id(ref)].record
    assert a.admit(log.entries["q"], log.list).record.outcome is Out.QUARANTINED  # prefix only: no future confirmer


def test_evidence_set_is_per_key_and_lists_quarantined_separately():
    log = Log()
    log.add("a", attr="employer")
    log.add("b", attr="city", value="Paris")
    log.add("q", attr="employer", source="q1", cls="quarantined", value="Zed")
    es = Admitter(AdmissionConfig()).evidence_set(log.list, KEY)
    assert [e.report.id for e in es.direct] == [log.id("a")]
    assert [e.report.id for e in es.quarantined] == [log.id("q")]
    assert es.admission_version == 1
