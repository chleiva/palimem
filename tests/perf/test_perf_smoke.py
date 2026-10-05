"""Smoke tests for the performance suite (T-E5): the harness runs and reports well-formed JSON.

These tests assert *shape, determinism and bookkeeping*, never timing thresholds, so CI stays quick and never flakes on a slow
runner. The targets themselves live in docs/PERFORMANCE.md and are judged by ``bench.perf.targets`` over real runs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.perf import report, targets
from bench.perf import workloads as wl
from bench.perf.crossover import run_crossover
from bench.perf.profile import run_profile
from bench.perf.recovery import run_recovery
from bench.perf.runner import percentile, run_workload, summarize_ns
from palimem.types import Cue

# ------------------------------------------------------------------------------------------ generators


@pytest.mark.parametrize("name", wl.WORKLOADS)
def test_generators_are_deterministic_and_seed_sensitive(name: str) -> None:
    assert wl.digest(name, 200, r=2.0, seed=7) == wl.digest(name, 200, r=2.0, seed=7)
    assert wl.digest(name, 200, r=2.0, seed=7) != wl.digest(name, 200, r=2.0, seed=8)
    assert wl.digest(name, 200, r=2.0, seed=7) != wl.digest(name, 200, r=2.0, seed=7, persons=30)


@pytest.mark.parametrize("name", wl.WORKLOADS)
def test_generators_emit_exactly_n_appends_and_r_queries_per_append(name: str) -> None:
    ops = list(wl.generate(name, 300, r=2.0, seed=3))
    appends = [o for o in ops if isinstance(o, wl.AppendOp)]
    queries = [o for o in ops if isinstance(o, wl.QueryOp)]
    assert len(appends) == 300
    assert [a.index for a in appends] == list(range(300))
    assert abs(len(queries) - 2 * 300) <= 4  # r is honoured up to the first appends (no key written yet)
    for op in appends:
        assert (op.cue is Cue.WITHDRAW) == (op.value is None)
        assert (op.cue in (Cue.WITHDRAW, Cue.CORRECT)) == (op.target is not None)
        if op.target is not None:
            assert op.target < op.index


@pytest.mark.parametrize("name", wl.WORKLOADS)
def test_no_key_exceeds_the_environment_budget(name: str) -> None:
    """The generators keep every key within the validated envelope (default budget 7, decision S-06)."""
    live: dict[tuple[str, str], set[int]] = {}
    owner: dict[int, tuple[str, str]] = {}
    for op in wl.generate(name, 600, r=0.0, seed=5):
        assert isinstance(op, wl.AppendOp)
        key = (op.entity, op.attr)
        if op.cue is Cue.WITHDRAW:
            assert op.target is not None
            live[owner[op.target]].discard(op.target)
        else:
            if op.cue is Cue.CORRECT:
                assert op.target is not None
                live[owner[op.target]].discard(op.target)
            live.setdefault(key, set()).add(op.index)
            owner[op.index] = key
        assert len(live.get(key, ())) <= wl.ENV_BUDGET, (key, len(live[key]))


@pytest.mark.parametrize("name", wl.WORKLOADS)
def test_corrections_are_never_correction_or_withdrawal_targets(name: str) -> None:
    """Withdrawing or correcting a correction restores its target (S-02), so the generators never do it: that is what
    keeps their live count equal to the number of reports the store counts against the environment budget."""
    corrections: set[int] = set()
    per_key: dict[tuple[str, str], int] = {}
    for op in wl.generate(name, 800, r=0.0, seed=11, persons=20):
        assert isinstance(op, wl.AppendOp)
        if op.target is not None:
            assert op.target not in corrections, op
        if op.cue is Cue.CORRECT:
            corrections.add(op.index)
            per_key[(op.entity, op.attr)] = per_key.get((op.entity, op.attr), 0) + 1
    assert all(n <= wl.MAX_CORRECTIONS_PER_KEY for n in per_key.values())


@pytest.mark.parametrize("name", wl.WORKLOADS)
def test_hot_population_stays_inside_the_validated_envelope_end_to_end(name: str) -> None:
    """The generators must never push a key over the environment budget in the *store* (the kernel's count, not the
    generator's own): no ResourceLimited answer and no completion job left pending, even with few people (hot keys)."""
    rep = run_workload(name, 260, r=1.0, seed=1, db_path=":memory:", persons=20)
    assert rep["integrity"]["resource_limited_answers"] == 0, rep["integrity"]
    assert rep["integrity"]["pending_completion_jobs_at_end"] == 0, rep["integrity"]


def test_query_mix_has_current_historical_and_derived_reads() -> None:
    m = wl.mix(list(wl.generate("w1", 400, r=3.0, seed=2)))
    assert m["query_current"] > 0 and m["query_historical"] > 0 and m["query_derived"] > 0
    assert m["append_assert"] > 0 and m["append_change"] > 0


# ------------------------------------------------------------------------------------------ measurement helpers


def test_percentile_is_nearest_rank() -> None:
    vals = list(range(1, 101))
    assert percentile(vals, 50) == 50.0
    assert percentile(vals, 95) == 95.0
    assert percentile(vals, 99) == 99.0
    assert percentile([], 99) == 0.0
    assert percentile([7], 99) == 7.0


def test_summarize_ns_is_ordered() -> None:
    s = summarize_ns([1_000_000 * i for i in range(1, 51)])
    assert s["count"] == 50 and s["p50_ms"] <= s["p95_ms"] <= s["p99_ms"] <= s["max_ms"]


# ------------------------------------------------------------------------------------------ runs


@pytest.mark.parametrize("name", wl.WORKLOADS)
def test_run_workload_reports_well_formed_json(name: str, tmp_path: Path) -> None:
    rep = run_workload(name, 60, r=1.0, seed=1, db_path=tmp_path / f"{name}.db")
    json.dumps(rep)  # serialisable
    assert rep["kind"] == "workload" and rep["workload"] == name
    assert rep["reached"]["reports"] == 60 and rep["reached"]["stopped_early"] is False
    a, q = rep["appends"], rep["queries"]
    assert a["count"] + a["warmup"]["count"] == 60
    assert a["p50_ms"] <= a["p95_ms"] <= a["p99_ms"] <= a["max_ms"]
    assert q["count"] > 0 and q["p50_ms"] <= q["p99_ms"]
    assert rep["integrity"]["resource_limited_answers"] == 0
    assert rep["integrity"]["not_visible_after_append"] == 0 and rep["integrity"]["visibility_checks"] >= 1
    assert rep["checkpoints"] and rep["checkpoints"][-1]["reports"] == 60
    assert rep["disk"]["bytes"] > 0
    assert {"python", "sqlite", "os", "cpu", "cpu_count", "git_commit"} <= set(rep["env"])
    assert rep["appends_by_attr_cue"]


def test_run_respects_the_wall_clock_budget_and_says_so(tmp_path: Path) -> None:
    rep = run_workload("w1", 400, r=0.0, seed=1, db_path=":memory:", max_seconds=0.0)
    assert rep["reached"]["stopped_early"] is True and rep["reached"]["reports"] < 400


def test_same_seed_gives_the_same_realised_stream(tmp_path: Path) -> None:
    a = run_workload("w1", 40, r=1.0, seed=4, db_path=":memory:")
    b = run_workload("w1", 40, r=1.0, seed=4, db_path=":memory:")
    assert a["digest"] == b["digest"] and a["queries"]["count"] == b["queries"]["count"]


def test_crossover_report_shape(tmp_path: Path) -> None:
    rep = run_crossover(60, r_store=2.0, seed=1, workdir=tmp_path, sample_keys=8)
    json.dumps(rep)
    c = rep["costs_ms"]
    assert c["store_append_mean"] > 0 and c["store_query_mean"] > 0 and c["replay_query_cold_mean"] > 0
    assert rep["log_only_error"] is None and c["replay_append_log_only_mean"] is not None
    assert rep["r_star_cold"] is None or rep["r_star_cold"] > 0


def test_profile_report_shape() -> None:
    rep = run_profile("w1", 60, window=20, top=5)
    json.dumps(rep)
    assert len(rep["appends"]["top_by_own_time"]) == 5 and len(rep["queries"]["top_by_cumulative_time"]) <= 5
    assert rep["appends"]["top_by_cumulative_time"][0]["cumulative_s"] >= rep["appends"]["top_by_cumulative_time"][-1]["cumulative_s"]


def test_recovery_kills_and_reopens_without_losing_acknowledged_appends(tmp_path: Path) -> None:
    rep = run_recovery(20, extra=150, seed=1, workdir=tmp_path)
    json.dumps(rep)
    assert rep["kind"] == "recovery"
    assert rep["acknowledged_appends_lost"] == 0, rep
    assert rep["integrity_ok"] is True, rep
    assert rep["head_lsn_after"] >= rep["last_acked_lsn"] >= 20, rep
    assert rep["start_to_first_correct_query_s"] > 0


# ------------------------------------------------------------------------------------------ judging


def _fake_run(reports: int, *, perfect: bool) -> dict[str, object]:
    ms = 1.0 if perfect else 500.0
    stats = {"count": 10, "mean_ms": ms, "p50_ms": ms, "p95_ms": ms, "p99_ms": ms, "max_ms": ms}
    return {
        "kind": "workload", "workload": "w1", "reached": {"reports": reports, "stopped_early": False, "elapsed_s": 1.0},
        "appends": {**stats, "warmup": stats, "sustained_per_s": 100.0}, "queries": {**stats, "by_kind": {}},
        "memory": {"rss_end_bytes": 10_000_000, "slope_bytes_per_report": 100.0}, "disk": {"bytes_per_report": 1000.0},
    }


def test_a_target_is_never_met_below_the_reference_size() -> None:
    rows = targets.evaluate([_fake_run(1000, perfect=True)], [], [])
    t1 = next(r for r in rows if r["id"] == "T1" and r["workload"] == "w1")
    assert t1["holds_at_reached_size"] is True
    assert t1["verdict"].startswith("missed")  # PERFORMANCE.md §5: not met, not extrapolated
    assert next(r for r in rows if r["id"] == "T4")["verdict"] == "not measured"


def test_a_target_is_met_at_the_reference_size_only_when_it_holds() -> None:
    good = {r["id"]: r["verdict"] for r in targets.evaluate([_fake_run(targets.REFERENCE_SIZE, perfect=True)], [], []) if r.get("workload") == "w1"}
    bad = {r["id"]: r["verdict"] for r in targets.evaluate([_fake_run(targets.REFERENCE_SIZE, perfect=False)], [], []) if r.get("workload") == "w1"}
    assert good["T1"] == good["T2"] == "met"
    assert bad["T1"] == bad["T2"] == "missed"


def test_report_renders_from_a_results_directory(tmp_path: Path) -> None:
    (tmp_path / "workload-w1-60.json").write_text(json.dumps(run_workload("w1", 60, r=1.0, seed=1, db_path=":memory:")))
    text = report.render(tmp_path)
    assert "### Runs" in text and "### Targets" in text and "T1" in text and "T6" in text


def test_warmup_is_a_fraction_of_the_reached_size_not_the_target() -> None:
    """An early-stopped run must still have measured appends (warm-up used to be 5% of the *target* and could swallow all)."""
    rep = run_workload("w1", 400, r=0.0, seed=1, db_path=":memory:", max_seconds=0.3)
    reached = rep["reached"]["reports"]
    assert rep["reached"]["stopped_early"] is True and 0 < reached < 400
    assert rep["appends"]["count"] + rep["appends"]["warmup"]["count"] == reached
    assert rep["appends"]["warmup"]["count"] == max(1, int(0.05 * reached))
    assert rep["appends"]["count"] > 0 or reached < 20


def test_old_all_warmup_results_fall_back_to_the_warmup_block() -> None:
    run = _fake_run(72, perfect=True)
    appends = run["appends"]
    assert isinstance(appends, dict)
    run["appends"] = {**appends, "count": 0, "p99_ms": 0.0, "warmup": {**appends["warmup"], "p99_ms": 321.0}}
    assert targets.append_stats(run)["p99_ms"] == 321.0


def test_tiny_runs_never_serve_as_the_smallest_scale_for_flatness() -> None:
    tiny, small, large = _fake_run(72, perfect=True), _fake_run(300, perfect=True), _fake_run(1000, perfect=True)
    for r in (tiny, small, large):
        r["params"] = {"persons": None}
    rows = targets.evaluate([tiny, small, large], [], [])
    t6 = next(r for r in rows if r["id"] == "T6" and r["workload"] == "w1")
    assert t6["measured_at_reports"] == [300, 1000]


def test_explicit_persons_runs_never_judge_a_target() -> None:
    primary, supplementary = _fake_run(500, perfect=True), _fake_run(9000, perfect=False)
    primary["params"], supplementary["params"] = {"persons": None}, {"persons": 250}
    rows = targets.evaluate([primary, supplementary], [], [])
    t2 = next(r for r in rows if r["id"] == "T2" and r["workload"] == "w1")
    assert t2["measured_at_reports"] == 500
