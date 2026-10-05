"""Bottleneck evidence (T-E5): ``cProfile`` over a window of appends and a window of queries on a store of size N.

The store is grown to ``n_reports - window`` appends without profiling (so the profile is taken at the size of interest),
then the last ``window`` appends are profiled together, and separately the queries that follow. Output is the top functions
by own time and by cumulative time (as structured rows), restricted to nothing: whatever dominates is reported, including
``sqlite3`` and the standard library. No tuning is done here; the report is the input to a later optimisation task.
"""

from __future__ import annotations

import cProfile
import pstats
from pathlib import Path
from typing import Any

from bench.perf import workloads as wl
from bench.perf.runner import REPORT_VERSION, env_info, make_memory
from palimem.types import Key, Profile, Query


def _rows(pr: cProfile.Profile, top: int) -> dict[str, list[dict[str, Any]]]:
    st = pstats.Stats(pr)
    entries = []
    for (filename, lineno, func), (cc, nc, tt, ct, _callers) in st.stats.items():  # type: ignore[attr-defined]
        short = filename if filename.startswith(("~", "<")) else _short(filename)
        entries.append({"function": f"{short}:{lineno}({func})", "calls": nc, "own_s": round(tt, 4), "cumulative_s": round(ct, 4)})
    by_own = sorted(entries, key=lambda e: -e["own_s"])[:top]
    by_cum = sorted(entries, key=lambda e: -e["cumulative_s"])[:top]
    return {"top_by_own_time": by_own, "top_by_cumulative_time": by_cum}


def _short(path: str) -> str:
    for marker in ("/src/palimem/", "/bench/", "/harness/"):
        if marker in path:
            return path[path.index(marker) + 1 :]
    return Path(path).name if "site-packages" not in path else "site-packages/" + path.split("site-packages/")[-1]


def run_profile(
    workload: str, n_reports: int, *, window: int = 150, r: float = 3.0, seed: int = 1, db_path: str | Path = ":memory:",
    persons: int | None = None, top: int = 14,
) -> dict[str, Any]:
    mem, _backend = make_memory(n_reports, db_path, persons)
    id_of: dict[int, str] = {}
    pr_a = cProfile.Profile()
    pr_q = cProfile.Profile()
    n_app = 0
    grow_to = max(0, n_reports - window)
    for op in wl.generate(workload, n_reports, r=r, seed=seed, persons=persons):
        if isinstance(op, wl.AppendOp):
            rep = wl.to_report(op, id_of)
            profiling = n_app >= grow_to
            if profiling:
                pr_a.enable()
            res = mem.append(rep, idempotency_key=f"prof:{seed}:{op.index}", complete=True)
            if profiling:
                pr_a.disable()
            assert res.entry is not None and res.entry.report.id is not None
            id_of[op.index] = res.entry.report.id
            n_app += 1
        elif n_app >= grow_to:
            q = Query(key=Key(entity=op.entity, attr=op.attr), profile=Profile.OPEN_WORLD, belief_as_of=op.as_of_lsn)
            pr_q.enable()
            mem.query(q)
            pr_q.disable()
    return {
        "report_version": REPORT_VERSION, "kind": "profile", "env": env_info(),
        "params": {"workload": workload, "n_reports": n_reports, "window": window, "r": r, "seed": seed, "persons": persons},
        "appends": _rows(pr_a, top),
        "queries": _rows(pr_q, top),
    }
