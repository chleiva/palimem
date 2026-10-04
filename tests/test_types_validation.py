"""Negative tests: invalid records are rejected by the types AND (where expressible) by the JSON Schemas,
plus tests that pin each author decision applied to the types (S-01..S-13, CONTRACT_PENDING 1-6)."""

from __future__ import annotations

import copy
import dataclasses
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from palimem.types import (
    AGENT_ORIGINS,
    DEFAULT_ENVIRONMENT_BUDGET,
    DEFAULT_RULES,
    MAX_BELIEF_NESTING,
    REVISE_STREAM_V1_RULES,
    AdmissionOutcome,
    AdmissionReason,
    AdmissionRecord,
    Answer,
    Attr,
    AttrClass,
    AuthorityRule,
    AuthorityTable,
    Belief,
    BeliefOfProp,
    BeliefView,
    Candidate,
    Cue,
    Decision,
    EnumerationProp,
    ExplainQuery,
    Explanation,
    Inference,
    KernelStatus,
    Key,
    LogEntry,
    MemberProp,
    NotValueForm,
    Origin,
    Power,
    Profile,
    Query,
    Report,
    ResourceLimited,
    ResourceLimitedReason,
    Rule,
    Segment,
    Source,
    Support,
    Targets,
    ValidationError,
    ValueForm,
    ValueProp,
    ValueType,
    Versions,
    Who,
    WhoKind,
    answer_from_dict,
    check_proposition_for_attr,
    principal_kind,
    proposition_from_dict,
)
from palimem.types.attr import Completeness
from palimem.types.enums import PrincipalKind
from tests import _typegen as g

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"
EX = SCHEMA_DIR / "examples"
R1, R2, R3 = "01JA0000000000000000000001", "01JA0000000000000000000002", "01JA0000000000000000000003"
KEY = Key(entity="alice", attr="employer")
T0 = datetime(2026, 3, 1, tzinfo=UTC)


def example(name: str) -> dict[str, Any]:
    out: dict[str, Any] = json.loads((EX / f"{name}.json").read_text())
    return out


def validator(stem: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(json.loads((SCHEMA_DIR / f"{stem}.schema.json").read_text()))


def mutated(name: str, fn: Any) -> dict[str, Any]:
    d = copy.deepcopy(example(name))
    fn(d)
    return d


def report_dict(**kw: Any) -> dict[str, Any]:
    d = copy.deepcopy(example("report"))
    d.update(kw)
    return d


# (id, schema stem, mutation of a valid example) -- rejected by BOTH the decoder and the schema
BOTH_REJECT = [
    ("report/assert-with-target", "report", "report", lambda d: d.update(target=R2)),
    ("report/withdraw-without-target", "report", "report_withdraw", lambda d: d.update(target=None)),
    ("report/withdraw-with-proposition", "report", "report_withdraw", lambda d: d.update(proposition={"form": "value", "v": 1})),
    ("report/assert-without-proposition", "report", "report", lambda d: d.update(proposition=None)),
    ("report/untyped-actor", "report", "report", lambda d: d.update(actor="bob")),
    ("report/unknown-actor-kind", "report", "report", lambda d: d.update(actor="robot:r2")),
    ("report/confirm-cue-removed", "report", "report", lambda d: d.update(cue="confirm")),
    ("report/bad-ulid", "report", "report", lambda d: d.update(id="not-a-ulid")),
    ("report/unknown-field", "report", "report", lambda d: d.update(lsn=3)),
    ("report/chain-field-not-on-report", "report", "report", lambda d: d.update(entry_hash="a" * 64)),
    ("report/naive-timestamp", "report", "report", lambda d: d.update(valid_from="2026-03-01T00:00:00")),
    ("segment/established-with-alternatives", "segment", "segment", lambda d: d.update(kernel_status="established", established=d["alternatives"][0])),
    ("segment/unresolved-single", "segment", "segment", lambda d: d.update(alternatives=d["alternatives"][:1])),
    ("segment/empty-needs-empty-form", "segment", "segment", lambda d: d.update(kernel_status="established_empty", established=d["alternatives"][0], alternatives=[])),
    ("segment/possible-is-adapter-only", "segment", "segment", lambda d: d.update(kernel_status="possible")),
    ("segment/unknown-with-candidate", "segment", "segment", lambda d: d.update(kernel_status="unknown", established=d["alternatives"][0], alternatives=[])),
    ("answer/commit-without-assertion", "answer", "answer", lambda d: d.update(assertion=None)),
    ("answer/abstain-with-assertion", "answer", "answer", lambda d: d.update(decision="abstain")),
    ("answer/ask-without-inquiry", "answer", "answer_ask", lambda d: d.update(inquiry=None)),
    ("answer/commit-with-inquiry", "answer", "answer", lambda d: d.update(inquiry=example("answer_ask")["inquiry"])),
    ("answer/resource-limited-needs-key-for-env-budget", "answer", "answer_resource_limited", lambda d: d.update(reason_key=None)),
    ("answer/stale-dependency-needs-key", "answer", "answer_resource_limited", lambda d: d.update(reason="stale_dependency", reason_key=None)),
    ("answer/store-dirty-must-not-have-key", "answer", "answer_resource_limited", lambda d: d.update(reason="store_dirty")),
    ("answer/resource-limited-has-no-segment", "answer", "answer_resource_limited", lambda d: d.update(segment={})),
    ("answer/resource-limited-has-no-kernel-status", "answer", "answer_resource_limited", lambda d: d.update(kernel_status="unknown")),
    ("admission/reason-not-valid-for-outcome", "admission_record", "admission_record", lambda d: d.update(outcome="excluded")),
    ("admission/confirmed-needs-confirming-reports", "admission_record", "admission_record", lambda d: d.update(confirmed_by=[])),
    ("admission/admitted-must-not-be-confirmed", "admission_record", "admission_record", lambda d: d.update(reason="admitted")),
    ("admission/version-required", "admission_record", "admission_record", lambda d: d.pop("admission_version")),
    ("admission/version-zero", "admission_record", "admission_record", lambda d: d.update(admission_version=0)),
    ("who/principal-needs-typed-id", "authority_rule", "authority_rule", lambda d: d["who"].update(kind="principal", value="alice")),
    ("who/any-must-not-carry-value", "authority_rule", "authority_rule", lambda d: d["who"].update(kind="any", value="x")),
    ("attr/derived-needs-rule", "attr", "attr", lambda d: d.update({"class": "derived"})),
    ("completeness/declared-needs-scope", "attr", "attr", lambda d: d.update(completeness={"mode": "declared"})),
    ("completeness/open-has-no-scope", "attr", "attr", lambda d: d.update(completeness={"mode": "open", "scope": {}})),
    ("query/bool-belief-as-of", "query", "query", lambda d: d.update(belief_as_of=True)),
    ("query/negative-lsn", "query", "query", lambda d: d.update(belief_as_of=-1)),
    ("explain/depth-zero", "explain_query", "explain_query", lambda d: d.update(depth=0)),
    ("explain/one-with-two-environments", "explanation", "explanation", lambda d: d.update(mode="one", environments=d["environments"] * 2)),
]


@pytest.mark.parametrize("case", BOTH_REJECT, ids=lambda c: c[0])
def test_rejected_by_schema_and_by_decoder(case: tuple[str, str, str, Any]):
    cid, stem, ex, fn = case
    data = mutated(ex, fn)
    assert list(validator(stem).iter_errors(data)), f"schema accepted {cid}"
    decoder = {
        "report": Report.from_dict, "segment": Segment.from_dict, "answer": answer_from_dict,
        "admission_record": AdmissionRecord.from_dict, "authority_rule": AuthorityRule.from_dict, "attr": Attr.from_dict,
        "query": Query.from_dict, "explain_query": ExplainQuery.from_dict, "explanation": Explanation.from_dict,
    }[stem]
    with pytest.raises(ValidationError):
        decoder(data)


def test_valid_examples_used_above_are_actually_valid():
    for name, stem in [("report", "report"), ("segment", "segment"), ("answer", "answer"), ("answer_ask", "answer"),
                       ("answer_resource_limited", "answer"), ("admission_record", "admission_record"),
                       ("authority_rule", "authority_rule"), ("attr", "attr"), ("query", "query"),
                       ("explain_query", "explain_query"), ("explanation", "explanation"), ("report_withdraw", "report")]:
        assert not list(validator(stem).iter_errors(example(name))), name


def test_nested_belief_of_is_reserved_in_schema_and_types():
    nested = {"form": "belief_of", "holder": "a", "proposition": {"form": "belief_of", "holder": "b", "proposition": {"form": "value", "v": 1}}}
    assert list(validator("proposition").iter_errors(nested))
    with pytest.raises(ValidationError, match="reserved"):
        proposition_from_dict(nested)
    one = BeliefOfProp(holder="a", proposition=ValueProp(value=1))
    assert MAX_BELIEF_NESTING == 1
    with pytest.raises(ValidationError):
        BeliefOfProp(holder="b", proposition=one)
    marker = json.loads((SCHEMA_DIR / "proposition.schema.json").read_text())["$defs"]["BeliefOfProp"]["x-palimem-reserved"]
    assert "nested_belief_of" in marker
    # a candidate belief_of(holder, P) is depth 1 like the proposition it mirrors
    from palimem.types import BeliefOfForm
    with pytest.raises(ValidationError):
        BeliefOfForm(holder="a", proposition=one)


def test_derived_attr_rule_is_required_and_others_forbid_it():
    with pytest.raises(ValidationError):
        Attr(name="x", attr_class=AttrClass.DERIVED, value_type=ValueType.STRING)
    with pytest.raises(ValidationError):
        Attr(name="x", attr_class=AttrClass.SINGLE_STABLE, value_type=ValueType.STRING, rule=Rule(reads=("y",), fn="f"))
    with pytest.raises(ValidationError):
        Attr(name="x", attr_class=AttrClass.DERIVED, value_type=ValueType.STRING, rule=Rule(reads=("x",), fn="f"))
    bad = mutated("schema", lambda d: d["attrs"][0].update(rule={"reads": ["a"], "fn": "f"}))
    assert list(validator("schema").iter_errors(bad))


# ------------------------------------------------------------------ decisions pinned in the types

def test_no_confirm_cue_and_five_statuses():
    assert "confirm" not in {c.value for c in Cue}
    assert {s.value for s in KernelStatus} == {"established", "unresolved", "unknown", "established_empty", "established_false"}


def test_lsn_and_chain_are_log_row_properties_not_report_fields():
    rf = {f.name for f in dataclasses.fields(Report)}
    assert not rf & {"lsn", "prev_hash", "entry_hash", "recorded_at"}
    assert {"lsn", "recorded_at", "prev_hash", "entry_hash", "report"} == {f.name for f in dataclasses.fields(LogEntry)}
    # a backend without the chain is still conformant: both hash fields optional
    r = g.report(__import__("random").Random(3))
    r = dataclasses.replace(r, id=R1)
    e = LogEntry(lsn=1, recorded_at=T0, report=r)
    assert e.prev_hash is None and e.entry_hash is None
    with pytest.raises(ValidationError):
        LogEntry(lsn=1, recorded_at=T0, report=dataclasses.replace(r, id=None))  # id is log-assigned
    with pytest.raises(ValidationError):
        LogEntry(lsn=0, recorded_at=T0, report=r)
    with pytest.raises(ValidationError):
        LogEntry(lsn=1, recorded_at=T0, report=r, entry_hash="XYZ")


def test_belief_as_of_accepts_lsn_or_timestamp_but_not_bool():
    assert Query(key=KEY, belief_as_of=7).to_dict()["belief_as_of"] == 7
    assert Query(key=KEY, belief_as_of=T0).to_dict()["belief_as_of"] == "2026-03-01T00:00:00Z"
    for bad in (True, "2026-03-01T00:00:00Z"):
        with pytest.raises(ValidationError):
            Query(key=KEY, belief_as_of=bad)  # type: ignore[arg-type]
    assert Query.from_dict({"key": KEY.to_dict(), "belief_as_of": 12}).belief_as_of == 12
    assert Query.from_dict({"key": KEY.to_dict(), "belief_as_of": "2026-03-01T01:00:00+01:00"}).belief_as_of == T0


def test_inertia_is_a_boolean():
    with pytest.raises(ValidationError):
        Attr(name="e", attr_class=AttrClass.SINGLE_CHANGEABLE, value_type=ValueType.ENTITY, inertia="ttl")  # type: ignore[arg-type]
    assert json.loads(Attr(name="e", attr_class=AttrClass.SINGLE_CHANGEABLE, value_type=ValueType.ENTITY, inertia=True).to_json())["inertia"] is True


def test_environment_budget_reason_and_default_budget():
    assert DEFAULT_ENVIRONMENT_BUDGET == 7
    assert ResourceLimitedReason.ENVIRONMENT_BUDGET.value == "environment_budget"
    ok = ResourceLimited(reason=ResourceLimitedReason.ENVIRONMENT_BUDGET, reason_key=KEY, required_generation=2, completed_generation=2)
    assert "kernel_status" not in ok.to_dict() and "segment" not in ok.to_dict()
    with pytest.raises(ValidationError):
        ResourceLimited(reason=ResourceLimitedReason.ENVIRONMENT_BUDGET, required_generation=2, completed_generation=2)
    with pytest.raises(ValidationError):  # generation-bound reasons need completed < required
        ResourceLimited(reason=ResourceLimitedReason.INFERENCE_INCOMPLETE, required_generation=2, completed_generation=2)


def test_principal_kinds_come_from_the_id_prefix():
    assert principal_kind("agent:planner") is PrincipalKind.AGENT
    assert principal_kind("connector:registry") is PrincipalKind.CONNECTOR
    for bad in ("planner", "bot:x", "agent:", "agent: x", ":x"):
        with pytest.raises(ValidationError):
            principal_kind(bad)


def _rule(who: Who, may: tuple[Power, ...], **kw: Any) -> AuthorityRule:
    return AuthorityRule(who=who, may=may, **kw)


AGENT = Who(kind=WhoKind.PRINCIPAL, value="agent:planner")


@pytest.mark.parametrize("kw", [
    {"may": (Power.WITHDRAW,)},
    {"may": (Power.CORRECT,)},
    {"may": (Power.WITHDRAW, Power.DISPUTE), "over_origins": (Origin.EXTERNAL_OBSERVATION,)},
    {"may": (Power.CORRECT,), "over_origins": (Origin.ATTRIBUTED,)},
    {"may": (Power.WITHDRAW,), "over_origins": (Origin.PLAN, Origin.EXTERNAL_OBSERVATION)},
    {"may": (Power.WITHDRAW,), "targets": Targets.ANY, "over_origins": (Origin.PLAN,)},
    {"may": (Power.WITHDRAW,), "targets": Targets.SOURCE, "over_origins": (Origin.PLAN,)},
])
def test_loading_an_agent_grant_over_external_evidence_raises(kw: dict[str, Any]):
    with pytest.raises(ValidationError, match="agent"):
        _rule(AGENT, **kw)


def test_agent_grants_that_are_allowed():
    # dispute by explicit grant, scoped to key patterns
    r = _rule(AGENT, (Power.DISPUTE,), on=__import__("palimem.types", fromlist=["KeyScope"]).KeyScope(attr="employer*"))
    assert r.on.matches(Key(entity="x", attr="employer_city")) and not r.on.matches(Key(entity="x", attr="salary"))
    # withdraw/correct strictly over agent-class origins
    ok = _rule(AGENT, (Power.WITHDRAW, Power.CORRECT), over_origins=tuple(AGENT_ORIGINS))
    assert set(ok.over_origins or ()) == set(AGENT_ORIGINS)
    # a non-agent principal may be granted withdraw over anything
    _rule(Who(kind=WhoKind.PRINCIPAL, value="user:alice"), (Power.WITHDRAW,), targets=Targets.ANY)


def test_default_grant_is_own_source_plus_builtin_agent_rule():
    own, agent = DEFAULT_RULES
    assert own.who.kind is WhoKind.TARGET_SOURCE and set(own.may) == set(Power)
    assert agent.who.kind is WhoKind.TARGET_ACTOR and set(agent.may) == {Power.WITHDRAW, Power.CORRECT}
    assert Origin.EXTERNAL_OBSERVATION not in (agent.over_origins or ())
    assert all(o in {Origin.AGENT_HYPOTHESIS, Origin.AGENT_STATEMENT, Origin.PLAN, Origin.SIMULATION, Origin.COUNTERFACTUAL} for o in agent.over_origins or ())


def test_grant_table_is_a_versioned_admission_input():
    t1 = AuthorityTable(admission_version=3, rules=DEFAULT_RULES)
    extra = _rule(Who(kind=WhoKind.PRINCIPAL, value="user:alice"), (Power.WITHDRAW,))
    t2 = t1.successor(DEFAULT_RULES + (extra,))
    assert t2.admission_version == 4 and t1.admission_version == 3 and t1.rules == DEFAULT_RULES
    assert AuthorityTable.from_json(t2.to_json()) == t2


def test_origin_group_is_never_authority_in_the_product_profile():
    a = Attr(name="employer", attr_class=AttrClass.SINGLE_CHANGEABLE, value_type=ValueType.ENTITY,
             authority=(_rule(Who(kind=WhoKind.TARGET_ORIGIN_GROUP), (Power.CORRECT,)),))
    a.validate_for_profile(Profile.REVISE_STREAM_V1)
    with pytest.raises(ValidationError, match="corroboration"):
        a.validate_for_profile(Profile.OPEN_WORLD)
    from palimem.types import default_authority_rules, validate_rules_for_profile
    validate_rules_for_profile(default_authority_rules(Profile.OPEN_WORLD), Profile.OPEN_WORLD)
    assert default_authority_rules(Profile.REVISE_STREAM_V1) == REVISE_STREAM_V1_RULES
    with pytest.raises(ValidationError):
        validate_rules_for_profile(REVISE_STREAM_V1_RULES, Profile.OPEN_WORLD)


def test_attr_rule_scope_must_match_the_attribute():
    with pytest.raises(ValidationError, match="does not match"):
        Attr(name="employer", attr_class=AttrClass.SINGLE_STABLE, value_type=ValueType.STRING,
             authority=(_rule(Who(kind=WhoKind.ANY), (Power.WITHDRAW,), on=__import__("palimem.types", fromlist=["KeyScope"]).KeyScope(attr="salary")),))


def test_admission_records_always_carry_reason_and_version():
    rec = AdmissionRecord(id=R3, report_id=R1, outcome=AdmissionOutcome.EXCLUDED, reason=AdmissionReason.SOURCE_BLOCKED, admission_version=2)
    d = rec.to_dict()
    assert d["reason"] == "source_blocked" and d["admission_version"] == 2
    assert {o.value for o in AdmissionOutcome} == {"admissible", "quarantined", "excluded"}
    with pytest.raises(ValidationError):
        AdmissionRecord(id=R3, report_id=R1, outcome=AdmissionOutcome.QUARANTINED, reason=AdmissionReason.CONFIRMED, admission_version=1, confirmed_by=(R2,))
    with pytest.raises(ValidationError):  # confirmation by itself is meaningless
        AdmissionRecord(id=R3, report_id=R1, outcome=AdmissionOutcome.ADMISSIBLE, reason=AdmissionReason.CONFIRMED, admission_version=1, confirmed_by=(R1,))
    ok = AdmissionRecord(id=R3, report_id=R1, outcome=AdmissionOutcome.ADMISSIBLE, reason=AdmissionReason.CONFIRMED, admission_version=1, confirmed_by=(R2,))
    assert ok.confirmed_by == (R2,)


def test_explain_depth_defaults_to_full_closure():
    q = ExplainQuery(key=KEY)
    assert q.depth is None and q.to_dict()["depth"] is None
    assert ExplainQuery(key=KEY, depth=2).depth == 2


# ------------------------------------------------------------------ other invariants

def test_candidates_keep_negative_content_and_have_stable_ids():
    not_acme = Candidate(key=KEY, form=NotValueForm(value="Acme"))
    not_globex = Candidate(key=KEY, form=NotValueForm(value="Globex"))
    assert not_acme.id != not_globex.id
    assert Candidate(key=KEY, form=ValueForm(value="Acme")).id == Candidate.from_json(Candidate(key=KEY, form=ValueForm(value="Acme")).to_json()).id
    assert Candidate(key=KEY, form=ValueForm(value=1)).id != Candidate(key=KEY, form=ValueForm(value=True)).id
    d = not_acme.to_dict()
    d["id"] = "0" * 64
    with pytest.raises(ValidationError, match="mismatch"):
        Candidate.from_dict(d)


def test_enumeration_is_a_set_and_empty_is_distinct():
    assert EnumerationProp(values=("b", "a", "a")) == EnumerationProp(values=("a", "b"))
    assert EnumerationProp(values=()).to_dict()["values"] == []
    assert MemberProp(value="a") != ValueProp(value="a")


def test_timestamps_are_utc_normalised_and_must_be_aware():
    plus1 = timezone(timedelta(hours=1))
    s = Support(environment=(R1,), valid_from=datetime(2026, 3, 1, 1, 0, tzinfo=plus1))
    assert s.valid_from == T0 and s.to_dict()["valid_from"] == "2026-03-01T00:00:00Z"
    with pytest.raises(ValidationError):
        Support(environment=(R1,), valid_from=datetime(2026, 3, 1))  # noqa: DTZ001 - naive on purpose
    with pytest.raises(ValidationError):
        Support(environment=(R1,), valid_from=T0 + timedelta(days=1), valid_to=T0)


def test_segment_and_belief_structure_checks():
    a, b = g.increasing_ts(__import__("random").Random(1), 2)
    s1 = Segment(valid_from=None, valid_to=a, kernel_status=KernelStatus.UNKNOWN)
    s2 = Segment(valid_from=a, valid_to=None, kernel_status=KernelStatus.UNKNOWN)
    overlap = Segment(valid_from=a - timedelta(seconds=1), valid_to=None, kernel_status=KernelStatus.UNKNOWN)
    base: dict[str, Any] = {
        "key": KEY, "version": 1, "lsn": 1, "required_generation": 1, "completed_generation": 1,
        "pinned": (), "depends_on": (), "invalidated_by": None,
        "versions": Versions(schema=1, semantic=1, admission=1),
        "inference": Inference(complete=True), "recorded_at": T0,
    }
    Belief(segments=(s1, s2), **base)
    with pytest.raises(ValidationError, match="non-overlapping"):
        Belief(segments=(s1, overlap), **base)
    with pytest.raises(ValidationError, match="strictly"):
        Segment(valid_from=b, valid_to=b, kernel_status=KernelStatus.UNKNOWN)
    with pytest.raises(ValidationError, match="complete iff"):
        Belief(segments=(), **{**base, "inference": Inference(complete=False, reason="x")})
    with pytest.raises(ValidationError, match="exceeds"):
        Belief(segments=(), **{**base, "completed_generation": 5})
    other = Candidate(key=Key(entity="bob", attr="employer"), form=ValueForm(value="x"))
    wrong = Segment(valid_from=None, valid_to=None, kernel_status=KernelStatus.ESTABLISHED, established=other)
    with pytest.raises(ValidationError, match="different key"):
        Belief(segments=(wrong,), **base)


def test_answer_consistency_with_the_justified_view():
    ans = answer_from_dict(example("answer"))
    assert isinstance(ans, Answer.__args__[0])  # type: ignore[attr-defined]
    bad = copy.deepcopy(example("answer"))
    bad["kernel_status"] = "unresolved"
    with pytest.raises(ValidationError, match="kernel_status"):
        answer_from_dict(bad)
    bad = copy.deepcopy(example("answer"))
    bad["assertion"] = {"key": KEY.to_dict(), "form": {"form": "value", "v": "NotInSegment"}}
    with pytest.raises(ValidationError, match="candidate of the justified segment"):
        answer_from_dict(bad)
    view = BeliefView.from_dict(example("belief_view"))
    assert view.inference.complete and Inference(complete=True) == view.inference


def test_check_proposition_for_attr_matches_the_class_table():
    single = Attr(name="employer", attr_class=AttrClass.SINGLE_CHANGEABLE, value_type=ValueType.ENTITY)
    multi = Attr(name="aff", attr_class=AttrClass.MULTI_SET, value_type=ValueType.ENTITY)
    derived = Attr(name="wc", attr_class=AttrClass.DERIVED, value_type=ValueType.ENTITY, rule=Rule(reads=("employer",), fn="f"))
    check_proposition_for_attr(single, ValueProp(value="a"))
    check_proposition_for_attr(multi, MemberProp(value="a"))
    check_proposition_for_attr(multi, EnumerationProp(values=()))
    check_proposition_for_attr(single, BeliefOfProp(holder="x", proposition=ValueProp(value="a")))
    for attr_, prop in [(single, MemberProp(value="a")), (multi, ValueProp(value="a")), (derived, ValueProp(value="a"))]:
        with pytest.raises(ValidationError):
            check_proposition_for_attr(attr_, prop)


def test_origin_attributed_requires_belief_of():
    with pytest.raises(ValidationError, match="attributed"):
        Report(key=KEY, cue=Cue.ASSERT, proposition=ValueProp(value="x"), source=Source(id="s", cls="standard"),
               origin=Origin.ATTRIBUTED, origin_group="g", actor="connector:s")


def test_support_environments_are_base_report_ids():
    s = Support(environment=(R2, R1, R1))
    assert s.environment == (R1, R2)
    with pytest.raises(ValidationError):
        Support(environment=())
    assert Decision.COMMIT.value == "commit" and Completeness().mode.value == "open"


def test_trust_boundary_fixture_rules_use_the_typed_authority_rule():
    """Every authority rule in the trust-boundary fixtures is an AuthorityRule; the ones tb-19
    expects the host to refuse are exactly the ones the type refuses at load."""
    root = Path(__file__).parent / "fixtures" / "trust_boundary"
    tb18 = json.loads(next(root.glob("tb-18*.json")).read_text())
    rules = [AuthorityRule.from_dict(r) for r in tb18["setup"]["authority"]]
    assert rules[0].who == Who(kind=WhoKind.PRINCIPAL, value="agent:a1") and rules[0].may == (Power.DISPUTE,)
    tb19 = json.loads(next(root.glob("tb-19*.json")).read_text())
    outcomes = []
    for step in tb19["steps"]:
        try:
            [AuthorityRule.from_dict(r) for r in step["args"]["rules"]]
            outcomes.append("ok")
        except ValidationError:
            outcomes.append("refused")
    expected = ["error" in e for e in tb19["expect"]["steps"]]
    assert [o == "refused" for o in outcomes] == expected
