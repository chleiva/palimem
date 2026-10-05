"""Hand-built kernel tests: the Alex worked example, withdrawal cascade, correction, A-SU, budget,
change-from, schema mapping, static exactness (including the {A,B,empty} counter-example)."""

from __future__ import annotations

from itertools import pairwise

import pytest

from palimem.kernel import (
    AttrSpec,
    ExactnessViolation,
    JustificationProvider,
    KernelSchema,
    KernelUnsupported,
    ResourceLimitedResult,
    RuleSpec,
    check_schema,
    cross_key_corrections,
    find_overlaps,
    justify_derived,
    justify_key,
    rule_fn,
)
from palimem.kernel.derive import _Engine
from palimem.kernel.justify import Justification
from palimem.types import (
    Attr,
    AttrClass,
    KernelStatus,
    Key,
    Profile,
    ResourceLimitedReason,
    Rule,
    Schema,
    SemanticConfig,
    ValueType,
)
from tests._kernel_helpers import (
    derived,
    entry,
    multi_changeable,
    schema,
    single_changeable,
    single_stable,
)

P0C = SemanticConfig(self_update=False, profile=Profile.REVISE_STREAM_V1)
P0CSU = SemanticConfig(self_update=True, profile=Profile.REVISE_STREAM_V1)
ALEX = Key(entity="alex", attr="residence")


def _j(ks: KernelSchema, key: Key, entries: list, sem: SemanticConfig = P0C, **kw) -> Justification:  # type: ignore[no-untyped-def,type-arg]
    j = justify_key(ks, key, entries, sem, **kw)
    assert isinstance(j, Justification), j
    return j


def fam(*sets: tuple[str, ...]) -> frozenset[frozenset[str]]:
    return frozenset(frozenset(s) for s in sets)


# ---------------------------------------------------------------- the Alex worked example (SEMANTICS §10)


def test_alex_multi_valued_residence_three_alternatives() -> None:
    ks = schema(multi_changeable("residence"), entities=("alex",))
    es = [
        entry(1, "alex", "residence", "london", day=1, source="s1", origin_group="g1", multi=True),
        entry(2, "alex", "residence", "paris", day=2, source="s2", origin_group="g2", multi=True),
    ]
    j = _j(ks, ALEX, es)
    assert j.candidates_at(5) == fam(("london", "paris"), ("paris",), ("london",))
    seg = j.segment_at(5)
    assert seg.kernel_status is KernelStatus.UNRESOLVED
    assert len(seg.alternatives) == 3


def test_alex_single_valued_residence_two_alternatives() -> None:
    ks = schema(single_changeable("residence"), entities=("alex",))
    es = [
        entry(1, "alex", "residence", "london", day=1, source="s1", origin_group="g1"),
        entry(2, "alex", "residence", "paris", day=2, source="s2", origin_group="g2"),
    ]
    j = _j(ks, ALEX, es)
    assert j.candidates_at(5) == fam(("paris",), ("london",))
    assert j.holds_truths("paris", 5) in ([True, False], [False, True])  # mixed truth -> `possible` in v1
    assert j.erroneous_truths(es[0].report.id or "").count(True) == 1  # o1 is ERR in exactly one reading


def test_no_evidence_single_unknown_multi_empty_by_profile() -> None:
    ks = schema(single_changeable("employer"), multi_changeable("residence"), entities=("alex",))
    single = _j(ks, Key(entity="alex", attr="employer"), [])
    multi = _j(ks, ALEX, [])
    assert single.segment_at(3).kernel_status is KernelStatus.UNKNOWN
    assert multi.segment_at(3).kernel_status is KernelStatus.ESTABLISHED_EMPTY  # compat profile (S-04)
    open_world = _j(ks, ALEX, [], sem=SemanticConfig(self_update=False, profile=Profile.OPEN_WORLD))
    assert open_world.segment_at(3).kernel_status is KernelStatus.UNKNOWN  # product default: absence is not evidence


# ---------------------------------------------------------------- withdrawal cascade (README example)


def test_withdrawal_removes_the_derived_conclusion() -> None:
    rule = RuleSpec(id="r1", head=("work_city", "?e", "?c"), body=(("employer", "?e", "?x"), ("hq_city", "?x", "?c")))
    ks = schema(
        single_changeable("employer"), single_changeable("hq_city"), derived("work_city"),
        rules=(rule,), entities=("alex", "veltran"),
    )
    o1 = entry(1, "alex", "employer", "veltran", day=3, source="press")
    o2 = entry(2, "veltran", "hq_city", "tessaly", day=5, source="registry", origin_group="g2")
    work = Key(entity="alex", attr="work_city")

    def work_city(employer_entries: list) -> object:  # type: ignore[type-arg]
        base = {
            Key(entity="alex", attr="employer"): _j(ks, Key(entity="alex", attr="employer"), employer_entries),
            Key(entity="veltran", attr="hq_city"): _j(ks, Key(entity="veltran", attr="hq_city"), [o2]),
        }
        d = justify_derived(ks, work, JustificationProvider(base), P0C)
        return d.segment_at(12)

    before = work_city([o1])
    assert before.kernel_status is KernelStatus.ESTABLISHED  # type: ignore[attr-defined]
    assert before.established.form.value == "tessaly"  # type: ignore[attr-defined]
    after = work_city([])  # o1 withdrawn: the derived belief loses its only justification
    assert after.kernel_status is KernelStatus.UNKNOWN  # type: ignore[attr-defined]


# ---------------------------------------------------------------- corrections


def test_cross_origin_correction_does_not_resolve_by_itself() -> None:
    ks = schema(single_changeable("employer"))
    key = Key(entity="alex", attr="employer")
    o1 = entry(1, "alex", "employer", "acme", day=1, origin_group="gA", source="a")
    o2 = entry(2, "alex", "employer", "globex", day=2, origin_group="gB", source="b", cue="correct", target=1)
    j = _j(ks, key, [o1, o2])
    # a correction from another origin is a reliability contest (A-CORR), not a withdrawal
    assert j.candidates_at(5) == fam(("globex",), ("acme",))
    assert cross_key_corrections([o1, o2]) == []


def test_cross_key_correction_is_flagged() -> None:
    o1 = entry(1, "alex", "employer", "acme")
    o2 = entry(2, "alex", "hq_city", "paris", cue="correct", target=1)
    assert cross_key_corrections([o1, o2]) == [o2.report.id]


# ---------------------------------------------------------------- A-SU (P0cSU)


def test_same_origin_self_update_established_under_p0csu_unresolved_under_p0c() -> None:
    ks = schema(single_changeable("employer"))
    key = Key(entity="alex", attr="employer")
    es = [
        entry(1, "alex", "employer", "acme", day=1, origin_group="gA", source="a"),
        entry(2, "alex", "employer", "globex", day=2, origin_group="gA", source="a2"),
    ]
    assert _j(ks, key, es, P0C).candidates_at(5) == fam(("globex",), ("acme",))
    su = _j(ks, key, es, P0CSU)
    assert su.candidates_at(5) == fam(("globex",))
    assert su.segment_at(5).kernel_status is KernelStatus.ESTABLISHED


def test_change_cue_shields_the_earlier_value() -> None:
    ks = schema(single_changeable("employer"))
    key = Key(entity="alex", attr="employer")
    es = [
        entry(1, "alex", "employer", "acme", day=1, origin_group="gA", source="a"),
        entry(2, "alex", "employer", "globex", day=2, origin_group="gB", source="b", cue="change"),
    ]
    # P0c cue resolution: the change cue explains the earlier value, so the later report cannot be ERR
    j = _j(ks, key, es)
    assert j.candidates_at(5) == fam(("globex",))
    assert j.segment_at(5).kernel_status is KernelStatus.ESTABLISHED


def test_change_from_presupposes_the_previous_value() -> None:
    ks = schema(single_changeable("employer"))
    key = Key(entity="alex", attr="employer")
    es = [
        entry(1, "alex", "employer", "acme", day=1, origin_group="gA", source="a"),
        entry(2, "alex", "employer", "globex", day=3, origin_group="gB", source="b", cue="change"),
    ]
    with_from = _j(ks, key, es, change_from={es[1].report.id or "": "acme"})
    without = _j(ks, key, es)
    # with `from acme`, a reading in which acme was wrong (ERR) and globex TRUE is excluded
    assert len(with_from.interpretations) < len(without.interpretations)
    # the same presupposition stated on the report itself (Report.change_from)
    es_field = [
        es[0],
        entry(2, "alex", "employer", "globex", day=3, origin_group="gB", source="b", cue="change", change_from="acme"),
    ]
    by_field = _j(ks, key, es_field)
    assert by_field.interpretations == with_from.interpretations
    assert by_field.segments() == with_from.segments()
    # the field wins over an out-of-band override for the same report
    assert _j(ks, key, es_field, change_from={es_field[1].report.id or "": "zzz"}).interpretations == with_from.interpretations


# ---------------------------------------------------------------- budget


def test_above_budget_is_resource_limited_not_silently_degraded() -> None:
    ks = schema(single_changeable("employer"))
    key = Key(entity="alex", attr="employer")
    es = [entry(i, "alex", "employer", f"v{i}", day=i, origin_group=f"g{i}", source=f"s{i}") for i in range(1, 14)]
    r = justify_key(ks, key, es, P0C)  # default budget 12 (raised from 7 on docs/BUDGET_CROSSCHECK.md)
    assert isinstance(r, ResourceLimitedResult)
    assert r.reason is ResourceLimitedReason.ENVIRONMENT_BUDGET and r.n == 13 and r.budget == 12
    assert isinstance(justify_key(ks, key, es, P0C, budget=13), Justification)
    # an explicit budget of 7 (the previously validated envelope) is still honoured
    r7 = justify_key(ks, key, es[:8], P0C, budget=7)
    assert isinstance(r7, ResourceLimitedResult) and r7.n == 8 and r7.budget == 7
    assert isinstance(justify_key(ks, key, es[:7], P0C, budget=7), Justification)


def test_reports_on_another_key_are_refused() -> None:
    ks = schema(single_changeable("employer"), single_changeable("hq_city"))
    with pytest.raises(ValueError, match="not"):
        justify_key(ks, Key(entity="alex", attr="employer"), [entry(1, "alex", "hq_city", "x")], P0C)


# ---------------------------------------------------------------- segments


def test_segments_partition_valid_time_and_merge_equal_neighbours() -> None:
    ks = schema(single_stable("birth_date"))
    key = Key(entity="alex", attr="birth_date")
    j = _j(ks, key, [entry(1, "alex", "birth_date", "1989-07-15", day=1)])
    segs = j.segments()
    assert segs[0].valid_from is None and segs[-1].valid_to is None
    assert segs[0].kernel_status is KernelStatus.UNKNOWN  # before the anchor: nothing is known
    assert segs[-1].kernel_status is KernelStatus.ESTABLISHED  # law of inertia: holds from the anchor on
    for a, b in pairwise(segs):
        assert a.valid_to == b.valid_from and (a.kernel_status, a.established, a.alternatives) != (
            b.kernel_status, b.established, b.alternatives)


# ---------------------------------------------------------------- contract Schema mapping


def test_from_schema_maps_classes_and_refuses_what_it_cannot_express() -> None:
    sch = Schema(version=1, attrs=(
        Attr(name="employer", attr_class=AttrClass.SINGLE_CHANGEABLE, value_type=ValueType.ENTITY, inertia=True),
        Attr(name="birth_date", attr_class=AttrClass.SINGLE_STABLE, value_type=ValueType.DATE, inertia=True),
        Attr(name="nickname", attr_class=AttrClass.MULTI_SET, value_type=ValueType.STRING, inertia=True),
        Attr(name="work_city", attr_class=AttrClass.DERIVED, value_type=ValueType.ENTITY,
             rule=Rule(reads=("employer",), fn=rule_fn("single", [RuleSpec(
                 id="r", head=("work_city", "?e", "?c"), body=(("employer", "?e", "?c"),))]))),
    ))
    ks = KernelSchema.from_schema(sch, entities=("p0",))
    assert ks.spec("employer").changeable and not ks.spec("birth_date").changeable
    assert ks.spec("nickname").cardinality == "multi" and not ks.spec("nickname").competing_values
    assert ks.spec("work_city").derived and len(ks.rules) == 1
    check_schema(ks)
    bad = Schema(version=1, attrs=(
        Attr(name="employer", attr_class=AttrClass.SINGLE_CHANGEABLE, value_type=ValueType.ENTITY),  # inertia=False
    ))
    with pytest.raises(KernelUnsupported, match="inertia=False"):
        KernelSchema.from_schema(bad)


# ---------------------------------------------------------------- static exactness (T-B5)


def _overlapping_schema() -> KernelSchema:
    x = single_changeable("X")
    d1, d2, r = derived("D1"), derived("D2"), derived("R")
    rules = (
        RuleSpec(id="d1", head=("D1", "?e", "?v"), body=(("X", "?e", "?v"),)),
        RuleSpec(id="d2", head=("D2", "?e", "?v"), body=(("X", "?e", "?v"),)),
        RuleSpec(id="r", head=("R", "?e", "?v"), body=(("D1", "?e", "?v"), ("D2", "?e", "?v"))),
    )
    return schema(x, d1, d2, r, rules=rules)


def test_rule_reading_one_base_key_twice_is_refused() -> None:
    ks = _overlapping_schema()
    assert any("R" in str(o) for o in find_overlaps(ks))
    with pytest.raises(ExactnessViolation, match="reached twice"):
        check_schema(ks)


def test_disjoint_rules_are_accepted() -> None:
    rule = RuleSpec(id="r1", head=("work_city", "?e", "?c"), body=(("employer", "?e", "?x"), ("hq_city", "?x", "?c")),
                    exceptions=(("remote", "?e", True),))
    ks = schema(single_changeable("employer"), single_changeable("hq_city"), single_changeable("remote"),
                derived("work_city"), rules=(rule,))
    check_schema(ks)


def test_reviewer_counter_example_overgenerates_without_the_check() -> None:
    """Per-key unions over-generate on a schema the static check refuses: worlds {A}, {B} of X give
    {A, B, empty} instead of the correct {A, B}. Regression test for the exactness condition."""
    ks = _overlapping_schema()
    key = Key(entity="e", attr="X")
    es = [
        entry(1, "e", "X", "A", day=1, origin_group="g1", source="s1"),
        entry(2, "e", "X", "B", day=1, origin_group="g2", source="s2"),  # same anchor: a contradiction
    ]
    jx = _j(ks, key, es)
    assert jx.candidates_at(5) == fam(("A",), ("B",))
    r_key = Key(entity="e", attr="R")
    # what the kernel computes if the schema check is bypassed: union over per-key candidate families
    union = justify_derived(ks, r_key, JustificationProvider({key: jx}), P0C).candidates_at(5)
    # ground truth: evaluate the rules once per interpretation of X (the global oracle's branching)
    truth: set[frozenset[str]] = set()
    for _err, tl in jx.interpretations:
        one = Justification(key=key, spec=jx.spec, evidence=jx.evidence, interpretations=frozenset({(_err, tl)}),
                            relax_level=0, profile=jx.profile, policy=jx.policy)
        truth |= set(_Engine(ks, JustificationProvider({key: one})).candidates_at("e", "R", 5))
    assert truth == {frozenset({"A"}), frozenset({"B"})}
    assert union == truth | {frozenset()}, "per-key union adds the spurious empty world"


def test_attribute_spec_rejects_bad_cardinality() -> None:
    with pytest.raises(ValueError):
        AttrSpec(name="x", cardinality="many", changeable=True)  # type: ignore[arg-type]
