"""Provenance parity with the study's oracle (T-B4, S-12): needs the study checkout and the frozen data;
skipped locally when absent, a failure in CI where HARNESS_REQUIRED=1."""

import os

import pytest

from harness import frozen, kernel_diff, study


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


def test_profile_provenance_equals_the_oracle_set_on_every_query_of_every_slot(env):
    st, root = env
    report = kernel_diff.run(root, st, limit=15, source_retract="sidetable", provenance=True)
    pv = report["provenance"]
    assert report["strict_passed"], pv["examples"]
    assert pv["queries"] == report["queries"] > 400
    assert pv["disagreements"] == 0 and pv["support_inconsistent"] == 0
    assert {"current", "asof", "belief_asof", "downstream", "reported", "yesno:holds", "yesno:changed",
            "yesno:erroneous"} <= set(pv["per_slot"])


def test_every_difference_between_the_product_rule_and_the_oracle_is_classified(env):
    st, root = env
    pv = kernel_diff.run(root, st, limit=40, source_retract="sidetable", provenance=True)["provenance"]
    causes = pv["principled_difference_causes"]
    assert not [c for c in causes if "UNEXPLAINED" in c], pv["principled_unexplained_examples"]
    assert any(c.startswith("equal|equal") for c in causes)
    assert any("oracle-lists-redundant-supporters" in c for c in causes)  # minimality is the main difference


def test_the_environments_of_the_product_rule_are_subset_minimal_supports_not_the_oracles_flat_set(env):
    st, root = env
    pv = kernel_diff.run(root, st, limit=40, source_retract="sidetable", provenance=True)["provenance"]
    rel: dict[str, int] = {}
    for k, n in pv["principled_vs_oracle"].items():
        rel[k.split("/")[0]] = rel.get(k.split("/")[0], 0) + n
    assert rel.get("equal", 0) > rel.get("subset", 0) * 1.5  # most segment queries agree exactly
    assert rel.get("subset", 0) > 0  # and the principled rule really is smaller than the oracle's flat set


def test_injected_provenance_bug_is_detected(env):
    st, root = env
    report = kernel_diff.run(root, st, limit=20, source_retract="sidetable", provenance=True, inject="drop-provenance")
    assert not report["strict_passed"] and report["provenance"]["disagreements"] >= 1


def test_cli_provenance_flag_and_exit_codes(env, capsys):
    st, root = env
    base = ["--study-dir", str(st.dir), "--frozen-dir", str(root), "--limit", "3", "--source-retract", "sidetable",
            "--provenance", "strict", "--strict"]
    assert kernel_diff.main(base) == 0
    assert kernel_diff.main([*base, "--inject-bug", "drop-provenance"]) == 1
    out = capsys.readouterr().out
    assert "provenance (profile vs study oracle set)" in out
