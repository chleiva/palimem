"""CLI: ``python -m bench.perf <command>`` (see docs/PERFORMANCE.md).

  run        a workload (w1|w2|w3) at a size; writes a JSON report
  recovery   SIGKILL an appending process, reopen in a fresh interpreter, time to the first correct query
  crossover  store-versus-replay crossover r* on W1
  profile    cProfile over a window of appends and queries on a store of size N
  report     render the markdown tables and the target verdicts from a results directory
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from bench.perf import report
from bench.perf.crossover import run_crossover
from bench.perf.profile import run_profile
from bench.perf.recovery import run_recovery
from bench.perf.runner import run_workload
from bench.perf.workloads import WORKLOADS

RESULTS = Path(__file__).resolve().parent / "results"


def _emit(data: dict[str, Any], out: str | None, default_name: str) -> None:
    path = Path(out) if out else RESULTS / default_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1))
    print(f"wrote {path}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.perf")
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run")
    run.add_argument("workload", choices=WORKLOADS)
    run.add_argument("--reports", type=int, required=True)
    run.add_argument("--r", type=float, default=3.0)
    run.add_argument("--seed", type=int, default=1)
    run.add_argument("--persons", type=int, default=None, help="fix the number of people (default reports/4)")
    run.add_argument("--db", default=None, help="SQLite file (default: a temporary file next to the results)")
    run.add_argument("--max-seconds", type=float, default=None)
    run.add_argument("--tracemalloc", action="store_true")
    run.add_argument("--out", default=None)

    rec = sub.add_parser("recovery")
    rec.add_argument("--preload", type=int, required=True)
    rec.add_argument("--extra", type=int, default=400)
    rec.add_argument("--seed", type=int, default=1)
    rec.add_argument("--persons", type=int, default=None)
    rec.add_argument("--no-verify-beliefs", action="store_true")
    rec.add_argument("--workdir", default=".bench")
    rec.add_argument("--out", default=None)

    cx = sub.add_parser("crossover")
    cx.add_argument("--reports", type=int, required=True)
    cx.add_argument("--r", type=float, default=3.0)
    cx.add_argument("--seed", type=int, default=1)
    cx.add_argument("--persons", type=int, default=None)
    cx.add_argument("--max-seconds", type=float, default=None)
    cx.add_argument("--workdir", default=".bench")
    cx.add_argument("--out", default=None)

    pf = sub.add_parser("profile")
    pf.add_argument("workload", choices=WORKLOADS)
    pf.add_argument("--reports", type=int, required=True)
    pf.add_argument("--window", type=int, default=150)
    pf.add_argument("--seed", type=int, default=1)
    pf.add_argument("--persons", type=int, default=None)
    pf.add_argument("--out", default=None)

    rp = sub.add_parser("report")
    rp.add_argument("--dir", default=str(RESULTS))

    a = ap.parse_args(argv)
    if a.cmd == "run":
        db = a.db or str(Path(".bench") / f"{a.workload}-{a.reports}.db")
        if db != ":memory:":
            Path(db).parent.mkdir(parents=True, exist_ok=True)
            for suffix in ("", "-wal", "-shm"):
                Path(db + suffix).unlink(missing_ok=True)
        data = run_workload(
            a.workload, a.reports, r=a.r, seed=a.seed, db_path=db, max_seconds=a.max_seconds, trace_memory=a.tracemalloc, persons=a.persons,
        )
        _emit(data, a.out, f"workload-{a.workload}-{a.reports}{'-p' + str(a.persons) if a.persons else ''}.json")
    elif a.cmd == "recovery":
        Path(a.workdir).mkdir(parents=True, exist_ok=True)
        data = run_recovery(a.preload, extra=a.extra, seed=a.seed, workdir=a.workdir, persons=a.persons, verify_beliefs=not a.no_verify_beliefs)
        _emit(data, a.out, f"recovery-{a.preload}.json")
    elif a.cmd == "crossover":
        Path(a.workdir).mkdir(parents=True, exist_ok=True)
        data = run_crossover(a.reports, r_store=a.r, seed=a.seed, workdir=a.workdir, persons=a.persons, max_seconds=a.max_seconds)
        _emit(data, a.out, f"crossover-{a.reports}.json")
    elif a.cmd == "profile":
        data = run_profile(a.workload, a.reports, window=a.window, seed=a.seed, persons=a.persons)
        _emit(data, a.out, f"profile-{a.workload}-{a.reports}.json")
    else:
        print(report.render(a.dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
