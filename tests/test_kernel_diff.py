"""Tests for the kernel differential harness (needs the study checkout and the frozen data; skipped locally
when absent, a failure in CI where HARNESS_REQUIRED=1)."""

import os

import pytest

from harness import frozen, kernel_diff, study
from harness.convert import CompatAdmission, make_ulid, to_converted
from palimem.types._codec import ULID_RE


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


def test_kernel_agrees_exactly_with_the_gold_under_the_paper_source_retraction(env):
    st, root = env
    report = kernel_diff.run(root, st, limit=10, source_retract="sidetable")
    assert report["strict_passed"], report["examples"]
    assert report["queries"] > 300
    assert report["disagreements"] == report["segment_inconsistent"] == report["resource_limited"] == 0
    slots = set(report["per_slot"])
    assert {"current", "asof", "belief_asof", "downstream", "reported",
            "yesno:holds", "yesno:changed", "yesno:erroneous"} <= slots


def test_default_expand_mode_has_no_unexplained_disagreements(env):
    st, root = env
    report = kernel_diff.run(root, st, limit=60)  # default: source retraction expanded into withdraws
    assert report["passed"] and report["unexplained"] == 0, report["examples"]
    # every difference is the one known gap: a retracted source asserting again later
    assert report["disagreements"] == report["explained_source_retract_gap"]


def test_live_corrections_flag_changes_answers_so_the_compat_profile_needs_it_off(env):
    st, root = env
    report = kernel_diff.run(root, st, limit=60, source_retract="sidetable", live_corrections=True)
    assert not report["passed"] and report["unexplained"] > 0


@pytest.mark.parametrize("bug", ["self-update", "mutate-answer"])
def test_injected_bugs_are_detected(env, bug):
    st, root = env
    report = kernel_diff.run(root, st, limit=25, inject=bug)
    assert not report["passed"] and report["disagreements"] >= 1


def test_ignored_corrections_are_detected_across_enough_streams(env):
    st, root = env
    # the frozen gold exercises kernel-level (cross-origin) corrections rarely, so the bug shows only
    # over more streams: this documents how weak the gold is on A-CORR, not a weakness of the harness
    report = kernel_diff.run(root, st, limit=100, inject="ignore-corrections")
    assert not report["passed"]


def test_cli_exit_codes(env, capsys):
    st, root = env
    base = ["--study-dir", str(st.dir), "--frozen-dir", str(root), "--limit", "3"]
    assert kernel_diff.main(base) == 0
    assert kernel_diff.main(base + ["--inject-bug", "mutate-answer"]) == 1
    capsys.readouterr()


def test_conversion_is_lossless_where_representable(env):
    st, root = env
    stream = st.load_stream(str(root / "s1_0000.json"))
    conv = to_converted(stream)
    assert all(ULID_RE.match(e.report.id or "") for e in conv.entries)
    assert [e.lsn for e in conv.entries] == sorted(e.lsn for e in conv.entries)
    # every study observation is a log entry, except source retractions, which expand into one withdraw per
    # already-ingested assertion; LSN is the arrival index of the expanded log
    n_src = conv.stats["source_retractions"]
    assert len(conv.entries) == len(stream.observations) - n_src + conv.stats["source_retract_expanded_withdraws"]
    side = to_converted(stream, "sidetable")
    assert len(side.entries) + len(side.source_retractions) == len(stream.observations)
    assert conv.stats["cross_key_corrections"] == 0
    # the stub admission equals the study's admitted set at the last belief point
    adm = CompatAdmission(side).admitted_by_key(len(stream.observations))  # exact under the side table
    got = sorted(side.study_id[e.report.id or ""] for es in adm.values() for e in es)
    assert got == sorted(o.id for o in stream.admitted(10**9))


def test_lsn_mapping_of_timestamps_follows_s05(env):
    st, root = env
    conv = to_converted(st.load_stream(str(root / "s1_0000.json")))
    assert conv.lsn_at_day(-1000) == 0
    assert conv.lsn_at_day(10**6) == len(conv.arrival_ts)
    assert conv.lsn_at(conv.arrival_ts[3]) >= 4  # ties map to the last LSN at or before the timestamp


def test_make_ulid_is_valid_and_ordered():
    a, b = make_ulid(1_700_000_000_000, 1), make_ulid(1_700_000_000_000, 2)
    assert ULID_RE.match(a) and ULID_RE.match(b) and a < b
