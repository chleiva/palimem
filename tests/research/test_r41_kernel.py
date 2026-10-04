"""R4.1 spike: fast existence-query kernel vs the deposited oracle (oracle_v1), plus findings pinned as tests."""
import random

from conftest import needs_study

from research.r41 import diff
from research.r41.bench import distinct_anchor_instance
from research.r41.instances import random_instance
from research.r41.kernel import KeyKernel, Rep


def cs(*vals):
    return frozenset(frozenset([v]) if v is not None else frozenset() for v in vals)


# ---------------------------------------------------------------- hand cases (no oracle needed)
def test_two_conflicting_reports_keep_both_alternatives():
    k = KeyKernel([Rep(0, "v0", 1, "a"), Rep(1, "v1", 2, "b")], "P0c")
    c = k.candidates_all([0, 1, 3])
    assert c[0] == {frozenset()}                      # before any report
    assert c[1] == cs("v0", None)                     # v1-only interpretation has nothing yet
    assert c[3] == cs("v0", "v1")                     # unresolved: either may be the erroneous one


def test_change_cue_shields_the_earlier_value():
    reps = [Rep(0, "v0", 1, "a"), Rep(1, "v1", 2, "b", cue="change")]
    k = KeyKernel(reps, "P0c")
    assert k.erroneous(1) == (True, False)                   # nothing later can explain an error: v1 is TRUE
    assert k.erroneous(0) == (True, True)                    # v0 may be a stale value or an error
    assert k.candidates_all([3])[3] == cs("v1")              # either way the current value is v1


def test_self_update_protects_the_later_same_origin_report():
    reps = [Rep(0, "v0", 1, "a"), Rep(1, "v1", 2, "a")]
    assert KeyKernel(reps, "P0c").erroneous(1) == (True, True)
    assert KeyKernel(reps, "P0cSU").erroneous(1) == (True, False)   # A-SU: a source's later value supersedes its own
    assert KeyKernel(reps, "P0cSU").erroneous(0) == (True, True)    # the earlier one is still disputable


def test_correction_branching_is_counted():
    reps = [Rep(0, "v0", 1, "a"), Rep(1, "v1", 2, "b", cue="correction", op_of=0)]
    k = KeyKernel(reps, "P0c")
    assert k.erroneous(0) == (True, True)
    assert k.n_branches > 0


def test_polynomial_on_distinct_anchor_logs():
    rng = random.Random(0)
    reps = distinct_anchor_instance(rng, 100, n_corr=0)
    k = KeyKernel(reps, "P0cSU")
    assert k.any_admissible()
    k.candidates_all(sorted({r.anchor for r in reps}))
    assert k.n_branches < 50_000                 # no 2^n behaviour: n=100 would be 1e30 subsets


# ---------------------------------------------------------------- differential tests vs oracle_v1
@needs_study
def test_differential_random_small():
    stats = diff.run_random(400, seed=101, max_n=8)
    assert stats["mismatches"] == 0, stats["examples"]


@needs_study
def test_differential_n11_n12():
    rng = random.Random(7)
    for n in (11, 12):
        for _ in range(3):
            reps = random_instance(rng, n, n_values=3, n_origins=2, anchor_span=20, p_change=0.3, p_from=0.6, p_corr=0.1)
            for pol in ("P0c", "P0cSU"):
                assert diff.compare(reps, pol) == [], (n, pol)


@needs_study
def test_differential_on_study_streams():
    from research.r41 import run_study_streams
    res = run_study_streams.run(n_streams=15)
    assert res["key_instances_compared"] > 500
    assert res["mismatches"] == 0, res["examples"]


# ---------------------------------------------------------------- findings pinned as tests
@needs_study
def test_interpretation_set_is_inherently_exponential_but_answers_are_not():
    from research.r41 import oracle_bridge as ob
    n = 9
    reps = [Rep(i, f"v{i}", i, f"g{i}") for i in range(n)]
    s, interps = ob.oracle_interps(reps, "P0c")
    assert len(interps) == 2 ** n - 1                          # every non-empty T is admissible
    cands = ob.oracle_cand(s, interps, n + 1)
    assert len(cands) == n                                     # ...yet the answer lists n alternatives
    assert KeyKernel(reps, "P0c").candidates_all([n + 1])[n + 1] == cands


@needs_study
def test_multi_valued_alternatives_list_is_itself_exponential():
    from revise_stream import oracle_v1
    from revise_stream.timeline import observed_candidates

    from research.r41 import oracle_bridge as ob
    n = 7
    reps = [Rep(i, f"v{i}", i, f"g{i}") for i in range(n)]
    s = ob.build_stream(reps, cardinality="multi")
    interps = oracle_v1.key_interpretations(s, n + 1, ob.KEY, "P0c")
    cands = set()
    for it in interps:
        cands |= observed_candidates(s, it[1], s.attributes["a"], n + 1)
    assert len(cands) == 2 ** n - 1


@needs_study
def test_naive_collapse_is_not_answer_preserving_for_historical_times():
    """Tying the labels of a block of same-origin same-value reports loses interpretations that matter
    at times inside the block (P0cSU instance found by research/r41/bench.py)."""
    from research.r41.collapse import raw_vs_tied
    spec = [("v1", 0, "user"), ("v0", 0, "user"), ("v0", 1, "user"), ("v0", 1, "crm"), ("v0", 1, "crm"),
            ("v0", 2, "user"), ("v0", 2, "user"), ("v0", 3, "user")]
    reps = [Rep(i, v, a, g) for i, (v, a, g) in enumerate(spec)]
    ts, raw, tied = raw_vs_tied(reps, "P0cSU")
    assert tied is not None
    assert raw[0] == cs("v0", "v1")
    assert tied[0] == cs("v0")
    assert raw[ts[-1]] == tied[ts[-1]]                          # the current answer is unaffected here
