"""Workload runner (T-E5): drives ``palimem.memory.Memory`` on SQLite and records latencies, memory and disk.

Everything goes through the public API (``Memory.append(complete=True)`` and ``Memory.query``), as docs/PIPELINE.md
describes it. Per-operation times are ``time.perf_counter_ns`` around the call. The first 5% of the appends that were actually reached is warm-up:
reported separately, excluded from the percentiles. A run stops early when its wall-clock budget (``max_seconds``) is spent;
the report then states the size actually reached, and a target is never judged "met" at a size that was not reached.
"""

from __future__ import annotations

import gc
import os
import platform
import resource
import sqlite3
import subprocess
import sys
import time
import tracemalloc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bench.perf import workloads as wl
from palimem.admission import AdmissionConfig
from palimem.memory import Memory
from palimem.policy import JUSTIFIED
from palimem.store import Backend, LimitedRead, SQLiteBackend
from palimem.types import Key, Profile, Query, ResourceLimited, SemanticConfig

REPORT_VERSION = 1
WARMUP_FRACTION = 0.05
VISIBILITY_CHECK_EVERY = 25
CHECKPOINTS = 20


# --------------------------------------------------------------------------------------------- environment


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5, cwd=Path(__file__).resolve().parent, check=False
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _cpu_brand() -> str:
    if sys.platform == "darwin":
        try:
            return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=5, check=False).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    return platform.processor() or platform.machine()


def env_info() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "sqlite": sqlite3.sqlite_version,
        "os": f"{platform.system()} {platform.release()}",
        "machine": platform.machine(),
        "cpu": _cpu_brand(),
        "cpu_count": os.cpu_count(),
        "git_commit": _git_commit(),
    }


# --------------------------------------------------------------------------------------------- measurement helpers


def rss_bytes() -> int:
    """Current resident set size of this process (not the peak)."""
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/self/statm") as f:
                return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        except OSError:
            pass
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True, timeout=5, check=False)
        return int(out.stdout.strip()) * 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def peak_rss_bytes() -> int:
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return ru if sys.platform == "darwin" else ru * 1024  # bytes on macOS, KiB on Linux


def percentile(sorted_vals: list[int], p: float) -> float:
    """Nearest-rank percentile of an ascending list (0 for an empty list)."""
    if not sorted_vals:
        return 0.0
    rank = max(1, -(-(int(p) * len(sorted_vals)) // 100))  # ceil(p/100 * n) in integers (p is a whole number)
    return float(sorted_vals[min(rank, len(sorted_vals)) - 1])


def summarize_ns(values: list[int]) -> dict[str, float | int]:
    s = sorted(values)
    ms = 1e-6
    return {
        "count": len(s),
        "mean_ms": round(sum(s) / len(s) * ms, 4) if s else 0.0,
        "p50_ms": round(percentile(s, 50) * ms, 4),
        "p95_ms": round(percentile(s, 95) * ms, 4),
        "p99_ms": round(percentile(s, 99) * ms, 4),
        "max_ms": round(s[-1] * ms, 4) if s else 0.0,
    }


def db_bytes_checkpointed(path: str | Path) -> int | None:
    """Size of the database file after a WAL checkpoint (TRUNCATE): what the data occupies once SQLite has folded its
    write-ahead log back in. ``db_bytes`` (the declared T7 metric) includes the uncheckpointed WAL, so a small database
    can look several times larger than its content; both are recorded."""
    if str(path) == ":memory:":
        return None
    import sqlite3

    try:
        con = sqlite3.connect(str(path), timeout=5)
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            con.close()
    except sqlite3.Error:
        return None
    p = Path(str(path))
    return p.stat().st_size if p.exists() else None


def _checkpointed_disk(path: str | Path, n_appended: int) -> dict[str, float | int | None]:
    cp = db_bytes_checkpointed(path)
    return {
        "bytes_checkpointed": cp,
        "bytes_per_report_checkpointed": round(cp / n_appended, 1) if cp is not None and n_appended else None,
    }


def db_bytes(path: str | Path) -> int:
    if str(path) == ":memory:":
        return 0
    total = 0
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(path) + suffix)
        if p.exists():
            total += p.stat().st_size
    return total


# --------------------------------------------------------------------------------------------- building a Memory


def make_memory(n_reports: int, db_path: str | Path = ":memory:", persons: int | None = None) -> tuple[Memory, Backend]:
    """A ``Memory`` over a fresh or existing SQLite file with the performance schema (open-world, ``justified``)."""
    schema, ks = wl.perf_schema(n_reports, persons)
    backend = SQLiteBackend(db_path)
    mem = Memory(
        backend, schema, kernel_schema=ks, entities=ks.entities,
        semantic=SemanticConfig(self_update=False, profile=Profile.OPEN_WORLD),
        admission=AdmissionConfig(profile=Profile.OPEN_WORLD), policy=JUSTIFIED,
    )
    return mem, backend


# --------------------------------------------------------------------------------------------- a run


@dataclass
class _Window:
    appends: list[int] = field(default_factory=list)
    queries: list[int] = field(default_factory=list)


def run_workload(
    workload: str, n_reports: int, *, r: float = 3.0, seed: int = 1, db_path: str | Path = ":memory:",
    max_seconds: float | None = None, trace_memory: bool = False, persons: int | None = None,
) -> dict[str, Any]:
    report, _mem = _run(workload, n_reports, r=r, seed=seed, db_path=db_path, max_seconds=max_seconds, trace_memory=trace_memory, persons=persons)
    return report


def _run(
    workload: str, n_reports: int, *, r: float, seed: int, db_path: str | Path, max_seconds: float | None, trace_memory: bool, persons: int | None = None,
) -> tuple[dict[str, Any], Memory]:
    mem, backend = make_memory(n_reports, db_path, persons)
    gc.collect()
    rss0 = rss_bytes()
    if trace_memory:
        tracemalloc.start()
    id_of: dict[int, str] = {}
    cp_every = max(1, min(n_reports // CHECKPOINTS, 250))  # at most 250 appends apart, so an early-stopped run still has a curve
    all_appends: list[int] = []  # every append in order; warm-up is split off at the end (5% of what was actually reached)
    queries: list[int] = []
    q_kind: dict[str, list[int]] = {"current": [], "historical": [], "derived": []}
    window = _Window()
    by_attr: dict[str, list[int]] = {}
    checkpoints: list[dict[str, Any]] = []
    limited = not_visible = visibility_checks = 0
    n_appended = 0
    t_start = time.perf_counter()
    stopped_early = False
    perf = time.perf_counter_ns

    for op in wl.generate(workload, n_reports, r=r, seed=seed, persons=persons):
        if isinstance(op, wl.AppendOp):
            report = wl.to_report(op, id_of)
            t0 = perf()
            res = mem.append(report, idempotency_key=f"perf:{seed}:{op.index}", complete=True)
            dt = perf() - t0
            assert res.entry is not None and res.entry.report.id is not None
            id_of[op.index] = res.entry.report.id
            n_appended += 1
            all_appends.append(dt)
            by_attr.setdefault(f"{op.attr}:{op.cue.value}", []).append(dt)
            window.appends.append(dt)
            if n_appended % VISIBILITY_CHECK_EVERY == 0:
                visibility_checks += 1
                got = mem.read(Key(entity=op.entity, attr=op.attr))
                if got is None or isinstance(got, LimitedRead):
                    not_visible += 1
            if n_appended % cp_every == 0 or n_appended == n_reports:
                checkpoints.append(_checkpoint(n_appended, t_start, window, db_path))
                window = _Window()
            if max_seconds is not None and time.perf_counter() - t_start > max_seconds:
                stopped_early = n_appended < n_reports
                break
        else:
            q = Query(key=Key(entity=op.entity, attr=op.attr), profile=Profile.OPEN_WORLD, belief_as_of=op.as_of_lsn)
            t0 = perf()
            ans = mem.query(q)
            dt = perf() - t0
            if isinstance(ans, ResourceLimited):
                limited += 1
            queries.append(dt)
            window.queries.append(dt)
            q_kind["historical" if op.as_of_lsn is not None else "current"].append(dt)
            if op.attr == "work_city":
                q_kind["derived"].append(dt)

    elapsed = time.perf_counter() - t_start
    pending_jobs = backend.complete_pending(mem.reviser).jobs_pending  # keys that can never finish (over the environment budget)
    if window.appends and (not checkpoints or checkpoints[-1]["reports"] != n_appended):
        checkpoints.append(_checkpoint(n_appended, t_start, window, db_path))
    heap: dict[str, int] | None = None
    if trace_memory:
        cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        heap = {"current_bytes": cur, "peak_bytes": peak}
    gc.collect()
    rss_end = rss_bytes()
    warm_n = max(1, int(WARMUP_FRACTION * len(all_appends))) if all_appends else 0
    warm, appends = all_appends[:warm_n], all_appends[warm_n:]
    total_append_s = sum(all_appends) * 1e-9
    report_out: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "kind": "workload",
        "workload": workload,
        "params": {"n_reports": n_reports, "r": r, "seed": seed, "persons": persons, "max_seconds": max_seconds, "db": "memory" if str(db_path) == ":memory:" else "file"},
        "env": env_info(),
        "reached": {"reports": n_appended, "stopped_early": stopped_early, "elapsed_s": round(elapsed, 2)},
        "digest": wl.digest(workload, n_reports, r=r, seed=seed, persons=persons) if n_reports <= 20000 else None,
        "appends": {**summarize_ns(appends), "warmup": summarize_ns(warm), "sustained_per_s": round(n_appended / total_append_s, 2) if total_append_s else 0.0},
        "appends_by_attr_cue": {k: summarize_ns(v) for k, v in sorted(by_attr.items())},
        "queries": {**summarize_ns(queries), "by_kind": {k: summarize_ns(v) for k, v in q_kind.items()}},
        "integrity": {
            "resource_limited_answers": limited, "visibility_checks": visibility_checks, "not_visible_after_append": not_visible,
            "pending_completion_jobs_at_end": pending_jobs,
        },
        "memory": {
            "rss_start_bytes": rss0, "rss_end_bytes": rss_end, "rss_peak_bytes": peak_rss_bytes(),
            "slope_bytes_per_report": _rss_slope(checkpoints), "heap": heap,
        },
        "disk": {
            "bytes": db_bytes(db_path), "bytes_per_report": round(db_bytes(db_path) / n_appended, 1) if n_appended else 0.0,
            **_checkpointed_disk(db_path, n_appended),
        },
        "checkpoints": checkpoints,
    }
    return report_out, mem


def _checkpoint(reports: int, t_start: float, w: _Window, db_path: str | Path) -> dict[str, Any]:
    return {
        "reports": reports,
        "elapsed_s": round(time.perf_counter() - t_start, 2),
        "rss_bytes": rss_bytes(),
        "db_bytes": db_bytes(db_path),
        "append": summarize_ns(w.appends),
        "query": summarize_ns(w.queries),
    }


def _rss_slope(cps: list[dict[str, Any]]) -> float | None:
    """Marginal RSS per report between the checkpoint nearest half the run and the last one (current RSS, not peak)."""
    if len(cps) < 4:
        return None
    last = cps[-1]
    half = min(cps, key=lambda c: abs(c["reports"] - last["reports"] / 2))
    if half["reports"] >= last["reports"]:
        return None
    return round((last["rss_bytes"] - half["rss_bytes"]) / (last["reports"] - half["reports"]), 1)
