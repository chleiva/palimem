"""Aggregate result files into the markdown tables of docs/PERFORMANCE.md and judge them against the declared targets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bench.perf import targets


def load_results(directory: str | Path) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {"workload": [], "recovery": [], "crossover": [], "profile": []}
    for p in sorted(Path(directory).glob("*.json")):
        d = json.loads(p.read_text())
        if isinstance(d, dict) and d.get("kind") in out:
            d["_file"] = p.name
            out[d["kind"]].append(d)
    return out


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:,.2f}" if abs(v) < 1000 else f"{v:,.0f}"
    return str(v)


def _table(header: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(_fmt(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def runs_table(runs: list[dict[str, Any]]) -> str:
    rows = []
    for r in sorted(runs, key=lambda x: (x["workload"], x["reached"]["reports"])):
        a, q, m = r["appends"], r["queries"], r["memory"]
        rows.append([
            r["workload"].upper(), r["params"].get("persons") or "n/4", r["reached"]["reports"], "yes" if r["reached"]["stopped_early"] else "no",
            f"{r['reached']['elapsed_s']:.0f}", f"{a['p50_ms']:.1f} / {a['p95_ms']:.1f} / {a['p99_ms']:.1f}", f"{a['sustained_per_s']:.1f}",
            f"{q['p50_ms']:.2f} / {q['p95_ms']:.2f} / {q['p99_ms']:.2f}", f"{m['rss_end_bytes'] / 1e6:.0f}",
            "n/a" if m["slope_bytes_per_report"] is None else f"{m['slope_bytes_per_report'] / 1024:.1f}", f"{r['disk']['bytes_per_report'] / 1024:.1f}",
        ])
    return _table(
        ["workload", "persons", "reports reached", "stopped early", "elapsed s", "append p50 / p95 / p99 ms", "appends/s", "query p50 / p95 / p99 ms",
         "RSS MB (end)", "RSS slope KiB/report", "disk KiB/report"], rows,
    )


def curve_table(run: dict[str, Any]) -> str:
    rows = [[c["reports"], f"{c['append']['p50_ms']:.1f}", f"{c['append']['p99_ms']:.1f}", f"{c['query']['p50_ms']:.2f}", f"{c['query']['p99_ms']:.2f}",
             f"{c['rss_bytes'] / 1e6:.0f}", f"{c['db_bytes'] / 1e6:.1f}"] for c in run["checkpoints"]]
    return _table(["reports", "append p50 ms", "append p99 ms", "query p50 ms", "query p99 ms", "RSS MB", "DB MB"], rows)


def by_attr_table(run: dict[str, Any]) -> str:
    rows = [[k, v["count"], f"{v['mean_ms']:.1f}", f"{v['p50_ms']:.1f}", f"{v['p99_ms']:.1f}"] for k, v in run["appends_by_attr_cue"].items()]
    return _table(["attr:cue", "count", "mean ms", "p50 ms", "p99 ms"], rows)


def targets_table(rows: list[dict[str, Any]]) -> str:
    out = []
    for r in rows:
        measured = r.get("measured")
        if isinstance(measured, dict):
            ms = "; ".join(f"{k}={_fmt(v)}" for k, v in measured.items() if not isinstance(v, dict))
        else:
            ms = "" if measured is None else str(measured)
        out.append([r["id"], r.get("workload", ""), r.get("declared", ""), r.get("measured_at_reports", ""), ms, r["verdict"]])
    return _table(["target", "workload", "declared", "measured at (reports)", "measured", "verdict"], out)


def profile_tables(p: dict[str, Any], top: int = 8) -> str:
    parts = []
    for part in ("appends", "queries"):
        for how, title in (("top_by_own_time", "own time"), ("top_by_cumulative_time", "cumulative time")):
            rows = [[e["function"], e["calls"], f"{e['own_s']:.3f}", f"{e['cumulative_s']:.3f}"] for e in p[part][how][:top]]
            parts.append(f"**{part}, top by {title}** (window of {p['params']['window']} appends at {p['params']['n_reports']} reports)\n\n"
                         + _table(["function", "calls", "own s", "cumulative s"], rows))
    return "\n\n".join(parts)


def render(directory: str | Path) -> str:
    res = load_results(directory)
    rows = targets.evaluate(res["workload"], res["recovery"], res["crossover"])
    parts = ["### Runs\n", runs_table(res["workload"]), "\n### Targets\n", targets_table(rows)]
    return "\n".join(parts)
