"""Tests for the pipeline differential harness (needs the study checkout and the frozen data; skipped locally when
absent, a failure in CI where HARNESS_REQUIRED=1)."""

import os

import pytest

from harness import frozen, kernel_diff, pipeline_diff, study


@pytest.fixture(scope="module")
def env():
    try:
        st = study.load()
        root = frozen.locate_frozen(st.dir)
    except (FileNotFoundError, frozen.FrozenError) as e:
        if os.environ.get("HARNESS_REQUIRED"):
            pytest.fail(f"harness inputs required but unavailable: {e}")
        pytest.skip(f"study checkout or frozen data unavailable: {e}")
    return st, root


def test_full_pipeline_agrees_exactly_with_the_gold_on_both_backends(env):
    st, root = env
    report = pipeline_diff.run(root, st, limit=8)
    assert report["passed"], [b["examples"] for b in report["backends"].values()]
    for kind in ("memory", "sqlite"):
        b = report["backends"][kind]
        assert b["queries"] > 400 and b["disagreements"] == 0 and b["resource_limited"] == 0
        assert {"current", "asof", "belief_asof", "downstream", "reported",
                "yesno:holds", "yesno:changed", "yesno:erroneous"} <= set(b["per_slot"])


def test_both_backends_see_the_same_log_and_answers(env):
    st, root = env
    report = pipeline_diff.run(root, st, limit=6)
    a, b = report["backends"]["memory"], report["backends"]["sqlite"]
    assert (a["queries"], a["appends"], a["disagreements"]) == (b["queries"], b["appends"], b["disagreements"])
    assert a["per_slot"] == b["per_slot"]


def test_contract_expressible_source_retraction_has_the_same_gap_as_the_kernel_alone(env):
    """With the paper's source-level retraction expanded into per-report withdraws (the only form the contract can
    express), the full pipeline disagrees with the gold on exactly the queries the kernel alone does (the known
    ``source-retract:late-assert`` gap): admission, store and revision add no disagreement of their own."""
    st, root = env
    k = kernel_diff.run(root, st, limit=40)
    p = pipeline_diff.run(root, st, limit=40, source_retract="expand", backends=("memory",))
    assert k["disagreements"] > 0 and k["unexplained"] == 0
    assert p["backends"]["memory"]["disagreements"] == k["disagreements"]


@pytest.mark.parametrize("bug", ["mutate-answer", "no-source-retraction", "self-update"])
def test_injected_bugs_are_detected(env, bug):
    st, root = env
    report = pipeline_diff.run(root, st, limit=20, inject=bug, backends=("memory",))
    assert not report["passed"] and report["backends"]["memory"]["disagreements"] >= 1


def test_interim_provenance_is_reported_but_never_gates(env):
    st, root = env
    report = pipeline_diff.run(root, st, limit=8, backends=("memory",))
    b = report["backends"]["memory"]
    assert b["provenance_compared"] > 20
    assert report["passed"]  # provenance may differ without failing the run (strict once T-B4 lands)
