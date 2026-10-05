"""The declared targets (docs/PERFORMANCE.md §3) and how a set of measurements is judged against them.

The numbers here mirror the table in docs/PERFORMANCE.md, which was committed before the first benchmark run;
``tests/perf/test_targets_declared.py`` fails if the two drift. A target is judged only at the size it names:

* ``met``                  measured at (or above) the reference size and within the target;
* ``missed``               measured at (or above) the reference size and outside the target, **or** the reference size
                           could not be reached inside the run's budget (PERFORMANCE.md §5: "the target is then not met,
                           not extrapolated");
* ``not measured``         no run supplies the quantity.

Alongside the verdict every row records the value measured at the size actually reached and whether it holds *there*.
"""

from __future__ import annotations

from typing import Any

REFERENCE_SIZE = 100_000  # reports in the log
CROSSOVER_SIZE = 10_000  # T5 is declared at 10^4 reports

T1_QUERY_P50_MS, T1_QUERY_P99_MS = 5.0, 25.0
T2_APPEND_P50_MS, T2_APPEND_P99_MS = 25.0, 100.0
T3_BYTES_PER_REPORT, T3_RSS_AT_REFERENCE_MB = 1024, 300.0
T4_RECOVERY_S = 2.0
T5_CROSSOVER_R = 2.0
T6_FLATNESS_RATIO = 4.0
T7_DISK_BYTES_PER_REPORT = 5 * 1024


def _primary(runs: list[dict[str, Any]], workload: str) -> list[dict[str, Any]]:
    """Runs that count towards a verdict: the workload at the default population scaling (people = reports / 4). A run
    with an explicit ``--persons`` is supplementary evidence (it isolates log length from entity count) and is never
    used to judge a target."""
    return [r for r in runs if r.get("kind") == "workload" and r["workload"] == workload and r.get("params", {}).get("persons") is None]


MIN_REPORTS_FOR_PERCENTILES = 100  # a smaller run has too few appends for a p99 and never serves as a scale endpoint


def append_stats(run: dict[str, Any]) -> dict[str, Any]:
    """Append latency stats of a run. Older result files measured warm-up against the *target* size, so an early-stopped run
    can have every append in the warm-up block: fall back to that block rather than reporting zeros."""
    a = run["appends"]
    return a if a["count"] > 0 else {**a["warmup"], "sustained_per_s": a.get("sustained_per_s", 0.0)}


def _largest(runs: list[dict[str, Any]], workload: str) -> dict[str, Any] | None:
    cands = _primary(runs, workload)
    return max(cands, key=lambda r: r["reached"]["reports"]) if cands else None


def _smallest(runs: list[dict[str, Any]], workload: str) -> dict[str, Any] | None:
    cands = [r for r in _primary(runs, workload) if r["reached"]["reports"] >= MIN_REPORTS_FOR_PERCENTILES]
    return min(cands, key=lambda r: r["reached"]["reports"]) if cands else None


def _verdict(reached: int, size: int, holds_here: bool) -> str:
    if reached < size:
        return "missed (reference size not reached)"
    return "met" if holds_here else "missed"


def evaluate(runs: list[dict[str, Any]], recovery: list[dict[str, Any]], crossover: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for tid, workloads in (("T1", ("w1", "w2")), ("T2", ("w1", "w2"))):
        for w in workloads:
            run = _largest(runs, w)
            if run is None:
                rows.append({"id": tid, "workload": w, "verdict": "not measured"})
                continue
            reached = run["reached"]["reports"]
            if tid == "T1":
                m = run["queries"]
                p50_t, p99_t = T1_QUERY_P50_MS, T1_QUERY_P99_MS
            else:
                m = append_stats(run)
                p50_t, p99_t = T2_APPEND_P50_MS, T2_APPEND_P99_MS
            holds = m["p50_ms"] <= p50_t and m["p99_ms"] <= p99_t
            rows.append({
                "id": tid, "workload": w, "declared": f"p50 <= {p50_t} ms, p99 <= {p99_t} ms", "measured_at_reports": reached,
                "measured": {"p50_ms": m["p50_ms"], "p99_ms": m["p99_ms"]}, "holds_at_reached_size": holds,
                "verdict": _verdict(reached, REFERENCE_SIZE, holds),
            })

    run = _largest(runs, "w1")
    if run is None:
        rows.append({"id": "T3", "verdict": "not measured"})
        rows.append({"id": "T7", "verdict": "not measured"})
    else:
        reached = run["reached"]["reports"]
        slope = run["memory"]["slope_bytes_per_report"]
        rss_mb = run["memory"]["rss_end_bytes"] / 1e6
        holds = slope is not None and slope <= T3_BYTES_PER_REPORT
        extrap = None if slope is None else round(slope * 1_000_000 / 2**30, 2)
        rows.append({
            "id": "T3", "workload": "w1", "declared": f"<= {T3_BYTES_PER_REPORT} B/report marginal (<= 1 GiB per million) and RSS <= {T3_RSS_AT_REFERENCE_MB:.0f} MB at {REFERENCE_SIZE}",
            "measured_at_reports": reached, "measured": {"slope_bytes_per_report": slope, "rss_end_mb": round(rss_mb, 1), "extrapolated_gib_per_million": extrap},
            "extrapolation_note": "linear extrapolation of the measured slope; a labelled estimate, not a measurement",
            "holds_at_reached_size": holds, "verdict": _verdict(reached, REFERENCE_SIZE, holds),
        })
        per = run["disk"]["bytes_per_report"]
        rows.append({
            "id": "T7", "workload": "w1", "declared": f"<= {T7_DISK_BYTES_PER_REPORT} B/report on disk", "measured_at_reports": reached,
            "measured": {"bytes_per_report": per}, "holds_at_reached_size": per <= T7_DISK_BYTES_PER_REPORT and per > 0,
            "verdict": _verdict(reached, REFERENCE_SIZE, per <= T7_DISK_BYTES_PER_REPORT and per > 0),
        })

    if recovery:
        rec = max(recovery, key=lambda r: r["params"]["preload"])
        size = rec["last_acked_lsn"]
        t = rec["start_to_first_correct_query_s"]
        holds = t <= T4_RECOVERY_S and rec["integrity_ok"]
        rows.append({
            "id": "T4", "declared": f"<= {T4_RECOVERY_S} s to first correct query; 0 acknowledged appends lost; recover/verify ok", "measured_at_reports": size,
            "measured": {"start_to_first_correct_query_s": t, "acknowledged_appends_lost": rec["acknowledged_appends_lost"], "integrity_ok": rec["integrity_ok"],
                         "verify_log_s": rec["probe"].get("verify_log_s")},
            "holds_at_reached_size": holds, "verdict": _verdict(size, REFERENCE_SIZE, holds),
        })
    else:
        rows.append({"id": "T4", "verdict": "not measured"})

    if crossover:
        c = max(crossover, key=lambda r: r["reached_reports"])
        reached = c["reached_reports"]
        r_cold, r_warm = c["r_star_cold"], c["r_star_warm"]
        holds = r_cold is not None and r_cold <= T5_CROSSOVER_R
        rows.append({
            "id": "T5", "workload": "w1", "declared": f"r* <= {T5_CROSSOVER_R} at {CROSSOVER_SIZE} reports", "measured_at_reports": reached,
            "measured": {"r_star_cold": r_cold, "r_star_warm": r_warm, "costs_ms": c["costs_ms"]}, "holds_at_reached_size": holds,
            "verdict": _verdict(reached, CROSSOVER_SIZE, holds),
        })
    else:
        rows.append({"id": "T5", "verdict": "not measured"})

    for w in ("w1", "w2"):
        small, large = _smallest(runs, w), _largest(runs, w)
        if small is None or large is None or small is large:
            rows.append({"id": "T6", "workload": w, "verdict": "not measured"})
            continue
        ratio = append_stats(large)["p99_ms"] / append_stats(small)["p99_ms"] if append_stats(small)["p99_ms"] else float("inf")
        reached = large["reached"]["reports"]
        holds = ratio <= T6_FLATNESS_RATIO
        rows.append({
            "id": "T6", "workload": w, "declared": f"p99 append (largest scale) / p99 append (smallest scale) <= {T6_FLATNESS_RATIO}",
            "measured_at_reports": [small["reached"]["reports"], reached],
            "measured": {"p99_small_ms": append_stats(small)["p99_ms"], "p99_large_ms": append_stats(large)["p99_ms"], "ratio": round(ratio, 2)},
            "holds_at_reached_size": holds, "verdict": "met" if holds else "missed",
        })
    return rows
