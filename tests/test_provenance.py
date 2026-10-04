"""Provenance (T-B4, S-12): subset-minimal environments per candidate and per interval, derived keys over base
reports, ``explain`` depth / budget / mode, and a brute-force cross-check of minimality.

No study checkout needed; the oracle-parity tests are in ``test_provenance_oracle.py``.
"""

from __future__ import annotations

import random
from typing import Any

import pytest

from palimem.kernel import (
    EnvBudget,
    JustificationProvider,
    RuleSpec,
    canonical_environment,
    explain_at,
    flatten,
    justify_derived,
    justify_key,
    minimize,
)
from palimem.kernel.justify import Justification
from palimem.kernel.provenance import run_members
from palimem.kernel.timeline import observed_candidates
from palimem.types import (
    ExplainMode,
    ExplanationState,
    KernelStatus,
    Key,
    Profile,
    SemanticConfig,
)
from tests._kernel_helpers import (
    derived,
    entry,
    multi_changeable,
    schema,
    single_changeable,
    single_stable,
    ulid,
)

P0C = SemanticConfig(self_update=False, profile=Profile.REVISE_STREAM_V1)
P0CSU = SemanticConfig(self_update=True, profile=Profile.REVISE_STREAM_V1)


def jk(ks, key, entries, sem=P0C, **kw) -> Justification:  # type: ignore[no-untyped-def]
    j = justify_key(ks, key, entries, sem, **kw)
    assert isinstance(j, Justification), j
    return j


def envs_of(seg) -> dict[str, set[tuple[str, ...]]]:  # type: ignore[no-untyped-def]
    return {cid: {s.environment for s in sl} for cid, sl in seg.support.items()}


def all_env_ids(seg) -> set[str]:  # type: ignore[no-untyped-def]
    return {r for sl in seg.support.values() for s in sl for r in s.environment}


# --------------------------------------------------------------------------- minimize / flatten


def test_minimize_keeps_only_subset_minimal_environments_deterministically() -> None:
    a, b, c = "a", "b", "c"
    out = minimize([frozenset({a, b}), frozenset({a}), frozenset({b, c}), frozenset({a, b, c}), frozenset({a})], EnvBudget())
    assert out == {frozenset({a}), frozenset({b, c})}


def test_minimize_cap_truncates_in_a_deterministic_order_and_flags_it() -> None:
    envs = [frozenset({x}) for x in "dcba"]
    bud = EnvBudget(cap=2)
    assert minimize(envs, bud) == {frozenset({"a"}), frozenset({"b"})}
    assert bud.truncated
    bud2 = EnvBudget(cap=10)
    assert len(minimize(envs, bud2)) == 4 and not bud2.truncated


def test_diagnostic_non_minimal_mode_keeps_every_environment() -> None:
    bud = EnvBudget(minimal=False)
    assert minimize([frozenset({"a"}), frozenset({"a", "b"})], bud) == {frozenset({"a"}), frozenset({"a", "b"})}


def test_flatten_is_the_union_of_ids() -> None:
    assert flatten([("a", "b"), ("b", "c"), ()]) == {"a", "b", "c"}


def test_canonical_environment_is_lexicographically_least_by_ids() -> None:
    assert canonical_environment([frozenset({"b"}), frozenset({"a", "z"}), frozenset({"c"})]) == frozenset({"a", "z"})
    assert canonical_environment([]) is None


# --------------------------------------------------------------------------- base keys


def test_two_agreeing_reports_are_one_joint_environment_and_the_survivor_remains_after_a_withdrawal() -> None:
    """Design row 'two independent supports; one withdrawn'. Under the kernel's semantics neither agreeing
    report can be labelled ERR (A-ERR: an error needs a dispute), so they are *jointly* the only
    environment; withdrawing one (admission removes it) recomputes the key and the survivor's environment
    is what remains."""
    ks = schema(single_changeable("emp"))
    key = Key(entity="e", attr="emp")
    r1 = entry(1, "e", "emp", "acme", day=1, source="s1", origin_group="g1")
    r2 = entry(2, "e", "emp", "acme", day=1, source="s2", origin_group="g2")
    seg = jk(ks, key, [r1, r2]).segment_at(5)
    assert seg.kernel_status is KernelStatus.ESTABLISHED
    assert envs_of(seg) == {seg.established.id: {(ulid(1), ulid(2))}}  # type: ignore[union-attr]
    after = jk(ks, key, [r2]).segment_at(5)  # r1 withdrawn
    assert after.kernel_status is KernelStatus.ESTABLISHED
    assert envs_of(after) == {after.established.id: {(ulid(2),)}}  # type: ignore[union-attr]
    gone = jk(ks, key, []).segment_at(5)  # last support withdrawn
    assert gone.kernel_status is KernelStatus.UNKNOWN and not gone.support


def test_support_carries_the_segments_valid_interval() -> None:
    ks = schema(single_changeable("emp"))
    key = Key(entity="e", attr="emp")
    j = jk(ks, key, [entry(1, "e", "emp", "acme", day=1, since=10)])
    for seg in j.segments():
        for sl in seg.support.values():
            for s in sl:
                assert (s.valid_from, s.valid_to) == (seg.valid_from, seg.valid_to)


def test_disjoint_interval_supports_are_per_interval() -> None:
    """Design row 'same candidate supported in disjoint intervals (R1 January, R2 February); R1 withdrawn':
    January never lists R2; withdrawing R1 makes January unknown while February keeps R2."""
    ks = schema(single_changeable("city"))
    key = Key(entity="e", attr="city")
    r1 = entry(1, "e", "city", "paris", day=1, since=1)
    r3 = entry(3, "e", "city", "london", day=20, since=20)
    r2 = entry(2, "e", "city", "paris", day=40, since=40)
    j = jk(ks, key, [r1, r3, r2])
    jan, feb = j.segment_at(10), j.segment_at(60)
    assert ulid(2) not in all_env_ids(jan)  # January provenance never lists R2
    assert ulid(1) in all_env_ids(jan)
    assert ulid(2) in all_env_ids(feb)
    after = jk(ks, key, [r3, r2])  # R1 withdrawn
    assert after.segment_at(10).kernel_status is KernelStatus.UNKNOWN
    assert not after.segment_at(10).support
    assert ulid(2) in all_env_ids(after.segment_at(60))  # February keeps R2


def test_unresolved_segment_explains_every_alternative() -> None:
    ks = schema(single_changeable("city"))
    key = Key(entity="e", attr="city")
    r1 = entry(1, "e", "city", "paris", day=1, source="a", origin_group="ga")
    r2 = entry(2, "e", "city", "london", day=1, source="b", origin_group="gb")  # same anchor: two competing reports
    seg = jk(ks, key, [r1, r2]).segment_at(5)
    assert seg.kernel_status is KernelStatus.UNRESOLVED
    cand_envs = envs_of(seg)
    assert {frozenset(v) for v in cand_envs.values()} == {frozenset({(ulid(1),)}), frozenset({(ulid(2),)})}
    exp = explain_at(jk(ks, key, [r1, r2]), 5)
    assert {s.environment for s in exp.environments} == {(ulid(1),), (ulid(2),)}
    assert exp.state is ExplanationState.COMPLETE


def test_mode_one_is_a_single_canonical_environment() -> None:
    ks = schema(single_changeable("city"))
    key = Key(entity="e", attr="city")
    j = jk(ks, key, [entry(1, "e", "city", "paris", day=1, origin_group="ga"), entry(2, "e", "city", "london", day=1, origin_group="gb")])
    one = explain_at(j, 5, mode=ExplainMode.ONE)
    assert [s.environment for s in one.environments] == [(ulid(1),)]
    assert one == explain_at(j, 5, mode=ExplainMode.ONE)  # deterministic


def test_explanation_budget_truncates_the_explanation_never_the_answer() -> None:
    """Three reports of one value, two of them interchangeable: several incomparable minimal environments."""
    ks = schema(single_changeable("city"))
    key = Key(entity="e", attr="city")
    j = jk(ks, key, [
        entry(1, "e", "city", "city1", day=1, origin_group="ga"),
        entry(2, "e", "city", "city1", day=1, origin_group="gb"),
        entry(3, "e", "city", "city2", day=5, origin_group="gc"),
    ])
    full = explain_at(j, 3)
    assert {s.environment for s in full.environments} >= {(ulid(1),), (ulid(2),)}
    assert full.state is ExplanationState.COMPLETE
    cut = explain_at(j, 3, env_cap=1)
    assert cut.state is ExplanationState.TRUNCATED
    assert len(cut.environments) < len(full.environments)
    # status and candidates are untouched by the explanation budget
    seg = j.segment_at(3)
    assert seg.kernel_status is KernelStatus.UNRESOLVED
    assert j.segment_at(3) == seg


def test_depth_must_be_positive() -> None:
    ks = schema(single_changeable("emp"))
    j = jk(ks, Key(entity="e", attr="emp"), [entry(1, "e", "emp", "acme", day=1)])
    with pytest.raises(ValueError):
        explain_at(j, 5, depth=0)


# --------------------------------------------------------------------------- derived keys


WORK = RuleSpec(id="r1", head=("work_city", "?e", "?c"), body=(("employer", "?e", "?x"), ("hq_city", "?x", "?c")))
TAX = RuleSpec(id="r2", head=("local_tax_city", "?e", "?c"), body=(("work_city", "?e", "?c"),))


def _chain(employer_entries: list[Any], hq_entries: list[Any]):  # type: ignore[no-untyped-def]
    ks = schema(
        single_changeable("employer"), single_changeable("hq_city"), derived("work_city"), derived("local_tax_city"),
        rules=(WORK, TAX), entities=("alex", "veltran"),
    )
    base = {
        Key(entity="alex", attr="employer"): jk(ks, Key(entity="alex", attr="employer"), employer_entries),
        Key(entity="veltran", attr="hq_city"): jk(ks, Key(entity="veltran", attr="hq_city"), hq_entries),
    }
    prov = JustificationProvider(base)
    return (
        justify_derived(ks, Key(entity="alex", attr="work_city"), prov, P0C),
        justify_derived(ks, Key(entity="alex", attr="local_tax_city"), prov, P0C),
    )


def _emp(lsn: int, source: str, group: str, day: int = 3):  # type: ignore[no-untyped-def]
    return entry(lsn, "alex", "employer", "veltran", day=day, source=source, origin_group=group)


HQ = entry(9, "veltran", "hq_city", "tessaly", day=5, source="registry", origin_group="g9")


def test_derived_environment_is_over_base_reports_along_the_derivation() -> None:
    work, _tax = _chain([_emp(1, "press", "g1")], [HQ])
    seg = work.segment_at(12)
    assert seg.kernel_status is KernelStatus.ESTABLISHED
    assert envs_of(seg) == {seg.established.id: {(ulid(1), ulid(9))}}  # type: ignore[union-attr]


def test_derived_conclusion_survives_the_loss_of_one_of_two_supports_and_vanishes_with_the_last() -> None:
    both, _ = _chain([_emp(1, "press", "g1"), _emp(2, "wiki", "g2", day=4)], [HQ])
    assert envs_of(both.segment_at(12)) == {both.segment_at(12).established.id: {(ulid(1), ulid(2), ulid(9))}}  # type: ignore[union-attr]
    one, _ = _chain([_emp(2, "wiki", "g2", day=4)], [HQ])
    assert one.segment_at(12).kernel_status is KernelStatus.ESTABLISHED
    assert all_env_ids(one.segment_at(12)) == {ulid(2), ulid(9)}
    none, _ = _chain([], [HQ])
    assert none.segment_at(12).kernel_status is KernelStatus.UNKNOWN
    assert not none.segment_at(12).support


def test_explain_depth_counts_derivation_levels() -> None:
    work, tax = _chain([_emp(1, "press", "g1")], [HQ])
    # work_city is level 1, the base keys it reads are level 2
    d1 = explain_at(work, 12, depth=1)
    assert d1.state is ExplanationState.TRUNCATED and not d1.environments
    d2 = explain_at(work, 12, depth=2)
    assert d2.state is ExplanationState.COMPLETE and [s.environment for s in d2.environments] == [(ulid(1), ulid(9))]
    # local_tax_city -> work_city -> base keys: three levels
    assert explain_at(tax, 12, depth=2).state is ExplanationState.TRUNCATED
    full = explain_at(tax, 12)
    assert full.state is ExplanationState.COMPLETE and full.depth is None
    assert [s.environment for s in full.environments] == [(ulid(1), ulid(9))]
    assert explain_at(tax, 12, depth=3) == explain_at(tax, 12, depth=3)  # deterministic
    assert [s.environment for s in explain_at(tax, 12, depth=3).environments] == [(ulid(1), ulid(9))]


def test_leaf_withdrawal_in_a_depth_three_derivation_removes_the_conclusion() -> None:
    """S-12 fixture fx-S12-depth-3-derivation-withdraw-at-leaf: withdrawing the leaf report removes the
    conclusion; explain(depth=None) lists the leaf before."""
    _work, tax = _chain([_emp(1, "press", "g1")], [HQ])
    assert ulid(1) in all_env_ids(tax.segment_at(12))
    _w2, tax2 = _chain([], [HQ])
    assert tax2.segment_at(12).kernel_status is KernelStatus.UNKNOWN


def test_explanation_of_a_derived_key_needs_a_support_provider() -> None:
    from palimem.kernel.derive import Provider

    class Plain:
        def candidates(self, key: Key, t: int):  # type: ignore[no-untyped-def]
            return frozenset({frozenset()})

        def breakpoints(self, key: Key) -> frozenset[int]:
            return frozenset()

    ks = schema(single_changeable("employer"), single_changeable("hq_city"), derived("work_city"), rules=(WORK,), entities=("alex",))
    prov: Provider = Plain()
    d = justify_derived(ks, Key(entity="alex", attr="work_city"), prov, P0C)
    assert d.segment_at(3).kernel_status is KernelStatus.UNKNOWN  # still answers without supports
    with pytest.raises(TypeError):
        d.world_envs_at(3)


# --------------------------------------------------------------------------- oracle flat rule (profile)


def test_oracle_flat_ids_is_every_report_asserting_a_candidate_value() -> None:
    ks = schema(single_changeable("city"))
    key = Key(entity="e", attr="city")
    r1 = entry(1, "e", "city", "paris", day=1, origin_group="ga")
    r2 = entry(2, "e", "city", "london", day=1, origin_group="gb")
    r3 = entry(3, "e", "city", "rome", day=50, origin_group="gc")
    j = jk(ks, key, [r1, r2, r3])
    # day 5 lies before rome's report: the change point is unknown, so rome is a candidate too and the oracle
    # cites the reports of every alternative
    assert j.oracle_flat_ids(5) == {ulid(1), ulid(2), ulid(3)}
    assert j.oracle_flat_ids(-5) == frozenset()  # no candidate value before any report: nothing to cite
    only = jk(ks, key, [r1, r2])
    assert only.oracle_flat_ids(5) == {ulid(1), ulid(2)}  # unresolved: both alternatives' reports


def test_oracle_flat_ids_for_derived_keys_follow_the_derivation_paths() -> None:
    work, tax = _chain([_emp(1, "press", "g1")], [HQ])
    assert work.oracle_flat_ids(12) == {ulid(1), ulid(9)}
    assert tax.oracle_flat_ids(12) == {ulid(1), ulid(9)}
    gone, _ = _chain([], [HQ])
    assert gone.oracle_flat_ids(12) == frozenset()


# --------------------------------------------------------------------------- brute-force cross-check


def _brute_world_envs(j: Justification, t: int) -> dict[frozenset[str], set[frozenset[str]]]:
    """An independent re-derivation of the environments: rebuild the value blocks of each interpretation's
    TRUE reports by sorting, match each block to its run, and keep the antichain by pairwise comparison."""
    spec = j.spec
    out: dict[frozenset[Any], set[frozenset[str]]] = {}
    for err, tl in j.interpretations:
        true_ev = sorted((e for e in j.evidence if e.id not in err), key=lambda e: (e.anchor, str(e.value)))
        blocks: list[tuple[Any, int, set[str]]] = []
        for e in true_ev:
            if blocks and blocks[-1][0] == e.value:
                blocks[-1][2].add(e.id)
            else:
                blocks.append((e.value, e.anchor, {e.id}))
        for w in observed_candidates(tl, spec, t):
            env: set[str] = set()
            for v, first, ids in blocks:
                run = next(r for r in tl if r.value == v and r.start == first)
                if v in w and run.covers(t) is not False:
                    env |= ids
            out.setdefault(w, set()).add(frozenset(env))
    return {w: {e for e in es if not any(o < e for o in es)} for w, es in out.items()}


def _random_key_instance(rng: random.Random):  # type: ignore[no-untyped-def]
    kind = rng.choice(["single_changeable", "single_stable", "multi_changeable"])
    spec = {"single_changeable": single_changeable, "single_stable": single_stable, "multi_changeable": multi_changeable}[kind]("k")
    ks = schema(spec)
    n = rng.randint(1, 6)
    entries = []
    change_from: dict[str, str] = {}
    for lsn in range(1, n + 1):
        value = rng.choice(["a", "b", "c"])
        cue = rng.choice(["assert", "assert", "assert", "change", "correct"])
        target = None
        if cue == "correct":
            if lsn == 1:
                cue = "assert"
            else:
                target = rng.randint(1, lsn - 1)
        day = rng.randint(1, 8)
        e = entry(lsn, "e", "k", value, day=day, since=day, cue=cue, target=target, multi=(kind == "multi_changeable"),
                  origin_group=rng.choice(["g1", "g2"]), source=rng.choice(["s1", "s2"]))
        entries.append(e)
        if cue == "change" and rng.random() < 0.5:
            change_from[ulid(lsn)] = rng.choice(["a", "b", "c"])
    return ks, entries, change_from


@pytest.mark.parametrize("sem", [P0C, P0CSU])
def test_environments_equal_a_brute_force_minimal_enumeration_and_are_antichains(sem: SemanticConfig) -> None:
    rng = random.Random(20261005)
    key = Key(entity="e", attr="k")
    checked = 0
    for _ in range(250):
        ks, entries, change_from = _random_key_instance(rng)
        j = justify_key(ks, key, entries, sem, change_from=change_from)
        assert isinstance(j, Justification)
        for t in (-1, 1, 3, 5, 8, 12):
            got = j.world_envs_at(t)
            want = _brute_world_envs(j, t)
            assert {w: set(es) for w, es in got.items()} == want, (entries, t)
            assert set(got) == set(j.candidates_at(t))  # the worlds are exactly the candidate family
            for es in got.values():  # antichain: no environment strictly contains another
                assert not any(a < b for a in es for b in es)
            checked += 1
    assert checked == 250 * 6


def test_every_environment_is_realised_by_an_interpretation_and_run_members_partition_true_reports() -> None:
    rng = random.Random(7)
    key = Key(entity="e", attr="k")
    for _ in range(100):
        ks, entries, change_from = _random_key_instance(rng)
        j = justify_key(ks, key, entries, P0C, change_from=change_from)
        assert isinstance(j, Justification)
        for err, tl in j.interpretations:
            true_ev = [e for e in j.evidence if e.id not in err]
            members = run_members(true_ev, tl)
            seen = [i for m in members.values() for i in m]
            assert sorted(seen) == sorted(e.id for e in true_ev)  # every TRUE report is in exactly one run
        for t in (1, 4, 9):
            for w, es in j.world_envs_at(t).items():
                for env in es:
                    # realised: some interpretation yielding w at t has exactly this contributing set
                    assert any(
                        w in observed_candidates(tl, j.spec, t)
                        and env == frozenset(
                            i for r, m in run_members([e for e in j.evidence if e.id not in err], tl).items()
                            if r.value in w and r.covers(t) is not False for i in m
                        )
                        for err, tl in j.interpretations
                    )
