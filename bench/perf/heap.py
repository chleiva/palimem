"""Where the Python heap goes (T3 evidence): ``tracemalloc`` top allocation sites after a W1 run, plus the pipeline's caches.

Grows a store to ``n_reports`` appends under ``tracemalloc``, then reports the heap by source file and by line, the size of the
pipeline's admission-evaluation cache (``Pipeline._evals``) and the number of entries in each. Read-only: nothing is tuned.
"""

from __future__ import annotations

import gc
import tracemalloc
from pathlib import Path
from typing import Any

from bench.perf import workloads as wl
from bench.perf.runner import REPORT_VERSION, env_info, make_memory


def run_heap(workload: str, n_reports: int, *, seed: int = 1, persons: int | None = None, top: int = 12, db_path: str | Path = ":memory:") -> dict[str, Any]:
    mem, _backend = make_memory(n_reports, db_path, persons)
    tracemalloc.start(8)
    id_of: dict[int, str] = {}
    n = 0
    for op in wl.generate(workload, n_reports, r=0.0, seed=seed, persons=persons):
        if isinstance(op, wl.AppendOp):
            res = mem.append(wl.to_report(op, id_of), idempotency_key=f"heap:{seed}:{op.index}", complete=True)
            assert res.entry is not None and res.entry.report.id is not None
            id_of[op.index] = res.entry.report.id
            n += 1
    gc.collect()
    snap = tracemalloc.take_snapshot()
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    snap = snap.filter_traces((tracemalloc.Filter(False, tracemalloc.__file__), tracemalloc.Filter(False, "<frozen importlib._bootstrap>")))
    by_file = snap.statistics("filename")[:top]
    by_line = snap.statistics("lineno")[:top]

    def short(path: str) -> str:
        for marker in ("/src/palimem/", "/bench/", "/harness/"):
            if marker in path:
                return path[path.index(marker) + 1 :]
        return path.rsplit("/", 1)[-1]

    evals = getattr(mem.pipeline, "_evals", {})
    return {
        "report_version": REPORT_VERSION, "kind": "heap", "env": env_info(),
        "params": {"workload": workload, "n_reports": n, "seed": seed, "persons": persons},
        "traced_current_bytes": cur, "traced_peak_bytes": peak,
        "by_file": [{"file": short(s.traceback[0].filename), "size_bytes": s.size, "blocks": s.count} for s in by_file],
        "by_line": [{"where": f"{short(s.traceback[0].filename)}:{s.traceback[0].lineno}", "size_bytes": s.size, "blocks": s.count} for s in by_line],
        "pipeline_evals_cache_entries": len(evals),
    }
