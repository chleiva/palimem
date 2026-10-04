"""Tests for the differential harness. They need the study checkout and the frozen data; locally they
skip when those are absent, in CI (HARNESS_REQUIRED=1) absence is a failure."""

import os
import shutil

import pytest

from harness import differential, frozen, study


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


def test_store_agrees_with_replay_and_gold(env):
    st, root = env
    report = differential.run(root, st, limit=5)
    assert report["passed"], report["examples"]
    assert report["queries"] > 100
    assert report["store_vs_replay"] == report["store_vs_gold"] == 0
    assert report["frozen_files_verified"] == 10


def test_mutate_answer_injection_is_detected(env):
    st, root = env
    report = differential.run(root, st, limit=5, inject="mutate-answer")
    assert not report["passed"]
    assert report["store_vs_replay"] >= 1 and report["store_vs_gold"] >= 1


def test_drop_propagation_injection_is_detected(env):
    st, root = env
    # cross-key withdrawal bug class: retraction no longer reaches derived beliefs
    report = differential.run(root, st, limit=25, inject="drop-propagation")
    assert not report["passed"]
    assert report["store_vs_replay"] > 0


def test_cli_exit_codes(env, capsys):
    st, root = env
    base = ["--study-dir", str(st.dir), "--frozen-dir", str(root), "--limit", "3"]
    assert differential.main(base) == 0
    assert differential.main(base + ["--inject-bug", "mutate-answer"]) == 1
    capsys.readouterr()


def test_drifted_gold_file_stops_the_run_with_exit_2(env, tmp_path, capsys):
    st, root = env
    for name in ("s1_0000.json", "s1_0000.gold.json"):
        shutil.copy(root / name, tmp_path / name)
    gold = tmp_path / "s1_0000.gold.json"
    gold.write_text(gold.read_text().replace('"established"', '"unknown"', 1))
    code = differential.main(["--study-dir", str(st.dir), "--frozen-dir", str(tmp_path), "--limit", "1"])
    assert code == 2
    assert "frozen set check failed" in capsys.readouterr().err


def test_missing_study_checkout_is_a_setup_error(tmp_path, capsys):
    assert differential.main(["--study-dir", str(tmp_path)]) == 2
    assert "not a PALIMPSEST study checkout" in capsys.readouterr().err


def test_missing_frozen_data_is_a_setup_error(env, tmp_path, monkeypatch, capsys):
    st, _ = env
    if (st.dir / "data" / "setting1" / "s1_0000.json").is_file():
        pytest.skip("study checkout carries its own data/setting1, so data is never 'missing' here")
    monkeypatch.setattr(frozen, "CACHE_DIR", tmp_path / "empty-cache")
    monkeypatch.delenv("PALIMPSEST_FROZEN_DIR", raising=False)
    monkeypatch.setenv("PALIMPSEST_DEPOSIT_ZIP", str(tmp_path / "nope.zip"))
    monkeypatch.setenv("HOME", str(tmp_path))  # hide ~/Downloads/palimpsest-v1.0.zip
    monkeypatch.setattr(frozen.Path, "home", classmethod(lambda cls: tmp_path))
    code = differential.main(["--study-dir", str(st.dir), "--frozen-dir", str(tmp_path / "nowhere")])
    assert code == 2
    assert "frozen Setting 1 data not found" in capsys.readouterr().err
