"""Tests for the Setting 2 / Setting 3 replay (T-J5): the pinned-data module and the replay harness.

The pure parts (path filter, manifest shape, authority classification) run everywhere. The replay itself needs the
study checkout and the published deposit's cached files: skipped locally when they are absent, a failure in CI where
``HARNESS_REQUIRED=1`` (the harness job fetches them)."""

import json
import os
from types import SimpleNamespace

import pytest

from harness import frozen, replay_s23, study, studydata

# ------------------------------------------------------------------------------------------ pure tests


def test_the_path_filter_selects_exactly_the_files_the_replay_uses():
    assert studydata.wanted("data/s2/s2_1000/extracted.json")
    assert studydata.wanted("data/s2_strong/s2_1003/answers.json")
    assert studydata.wanted("data/s3/s3_Q1002250/frozen.json")
    assert studydata.wanted("data/setting2_100/s2_1000.json")
    assert studydata.wanted("data/setting3/s3_Q1002250.json")
    # not the gold/hidden siblings, the logs, the usage records or other settings
    assert not studydata.wanted("data/setting2_100/s2_1000.gold.json")
    assert not studydata.wanted("data/setting3/s3_Q1002250.hidden.json")
    assert not studydata.wanted("data/s2/s2_1000/usage_log_llm_adjudicator.json")
    assert not studydata.wanted("data/s2/_results.json")
    assert not studydata.wanted("data/setting1/s1_0000.json")


def test_the_pinned_manifest_has_the_expected_shape():
    m = studydata.load_manifest()
    assert m["source"]["zip_sha256"] == frozen.ZENODO_SHA256
    files = m["files"]
    assert len(files) == m["source"]["cross_checked_against_deposit_MANIFEST_sha256"] == 565
    assert all(studydata.wanted(rel) for rel in files)
    by = {ds: [r for r in files if r.startswith(studydata.DATASETS[ds][0])] for ds in studydata.DATASETS}
    assert len(by["s2"]) == 300 and len(by["s2_strong"]) == 45 and len(by["s3"]) == 90
    assert sum(1 for r in files if r.startswith("data/setting2_100/")) == 100
    assert sum(1 for r in files if r.startswith("data/setting3/")) == 30
    assert all(len(h) == 64 and set(h) <= set("0123456789abcdef") for h in files.values())


def _obs(oid, source, kind="assert", op_cue="none", op_of=None, target=None):
    return SimpleNamespace(id=oid, source=source, kind=kind, op_cue=op_cue, op_of=op_of, target=target)


def test_the_authority_check_classifies_corrections_and_retractions():
    sources = {"a": SimpleNamespace(origin="g1"), "b": SimpleNamespace(origin="g1"), "c": SimpleNamespace(origin="g2")}
    stream = SimpleNamespace(
        sources=sources,
        observations=[
            _obs("o1", "a"),
            _obs("o2", "a", op_cue="correction", op_of="o1"),        # same source
            _obs("o3", "b", op_cue="correction", op_of="o1"),        # different source, same origin
            _obs("o4", "c", op_cue="correction", op_of="o1"),        # different origin
            _obs("o5", "a", op_cue="correction", op_of="o99"),       # dangling target
            _obs("o6", "a", kind="retract", target="o1"),            # same source
            _obs("o7", "c", kind="retract", target="o1"),            # cross origin
            _obs("o8", "b", kind="retract", target="a"),             # source-level
            _obs("o9", "a", kind="retract", target="o404"),          # dangling
        ],
    )
    c = replay_s23.authority_check(stream)
    assert c["correction:same_source"] == 1
    assert c["correction:cross_source_same_origin"] == 1
    assert c["correction:cross_origin"] == 1
    assert c["correction:dangling_target"] == 1
    assert c["retract:same_source"] == 1 and c["retract:cross_origin"] == 1
    assert c["retract:source_level"] == 1 and c["retract:dangling_target"] == 1


# ------------------------------------------------------------------------------------------ data-backed tests


@pytest.fixture(scope="module")
def env():
    try:
        st = study.load()
        root = studydata.locate(st.dir)
        first_s3 = min(p.name for p in (root / "data" / "s3").iterdir() if p.is_dir())
        studydata.require_valid(root, only=studydata.needed_rel("s2", "s2_1000") + studydata.needed_rel("s3", first_s3))
    except (FileNotFoundError, frozen.FrozenError) as e:
        if os.environ.get("HARNESS_REQUIRED"):
            pytest.fail(f"harness inputs required but unavailable: {e}")
        pytest.skip(f"study checkout or Setting 2/3 data unavailable: {e}")
    return st, root


@pytest.mark.parametrize("dataset", ["s2", "s3"])
def test_the_replayed_claims_agree_with_the_study_store_on_both_backends(env, dataset):
    st, root = env
    r = replay_s23.run_dataset(root, st, dataset, limit=4)
    for kind in ("memory", "sqlite"):
        b = r["backends"][kind]
        t = b["totals"]
        assert t["queries"] > 60
        assert t.get("disagreements", 0) == 0, b["examples"]
        assert t.get("query_errors", 0) == 0
        assert not b["not_replayed"], b["not_replayed"]


def test_the_gate_can_fail(env):
    st, root = env
    r = replay_s23.run_dataset(root, st, "s2", limit=3, backends=("memory",), inject="mutate-answer")
    assert r["backends"]["memory"]["totals"]["disagreements"] == 1
    assert replay_s23.verdict({"datasets": {"s2": r}}) == 1


def test_extracted_claims_with_references_to_non_assertions_are_normalised_and_counted(env):
    """The extractor sometimes emits a retraction of a retraction or a correction of one. The study's semantics make
    those no-ops (``Stream.admitted`` only returns assertions); the replay must make that explicit and count it."""
    st, root = env
    r = replay_s23.run_dataset(root, st, "s2", limit=3, backends=("memory",))
    assert r["reference_normalisation"].get("retract_of_non_assertion_dropped", 0) >= 1


def test_the_report_is_json_serialisable(env):
    st, root = env
    r = replay_s23.run_dataset(root, st, "s3", limit=2, backends=("memory",))
    assert json.loads(json.dumps(r))["dataset"] == "s3"
