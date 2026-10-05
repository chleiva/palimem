"""Store versus replay crossover r* (target T5), measured from per-operation costs on workload W1.

Per appended report, with ``r`` queries per observation:

* **store**  pays one full append (admission, revision, storage) and ``r`` stored-belief reads;
* **replay** pays one *log-only* append (the report, the chain and the transaction, with a no-op admission and revision) and,
  per query, rebuilds the key's belief from the log (``KernelReviser.recompute``) with a **cold** admission evaluation
  (``Pipeline.invalidate()`` first), because admission is a pure function of the whole log prefix and replay has no stored
  version to reuse. A **warm** variant keeps the cached evaluation (a lower bound for replay: it assumes the evaluation of the
  current head is free).

Total cost per observation is ``a + r * q`` for each system, so the lines cross at
``r* = (a_store - a_replay) / (q_replay - q_store)`` (``None`` when replay is never more expensive per query). Costs are
**means** over the measured operations, not percentiles: this is a total-work comparison, as in the study (r ~ 1.1).
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from bench.perf import workloads as wl
from bench.perf.runner import REPORT_VERSION, _run, env_info, make_memory
from palimem.store import AdmissionContext, RevisionContext, StoreError
from palimem.types import (
    AdmissionOutcome,
    AdmissionReason,
    AdmissionRecord,
    Belief,
    Key,
)


class NullAdmitter:
    """Admits everything and records it: the log-only append of the replay system."""

    def admit(self, ctx: AdmissionContext) -> Sequence[AdmissionRecord]:
        assert ctx.entry.report.id is not None
        return [AdmissionRecord(
            id=ctx.new_id(), report_id=ctx.entry.report.id, outcome=AdmissionOutcome.ADMISSIBLE,
            reason=AdmissionReason.ADMITTED, admission_version=1,
        )]


class NullReviser:
    def revise(self, ctx: RevisionContext) -> Sequence[Belief]:
        return []

    def recompute(self, key: Key, view: Any) -> Belief | None:
        return None


def _log_only_cost_ms(n_reports: int, r: float, seed: int, persons: int | None, workdir: Path) -> tuple[float | None, str | None]:
    """Mean ms per append with no admission or revision work (the same report stream, a fresh SQLite file)."""
    db = workdir / f"logonly-{n_reports}.db"
    for suffix in ("", "-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    _mem, backend = make_memory(n_reports, db, persons)
    id_of: dict[int, str] = {}
    total = 0
    n = 0
    try:
        for op in wl.generate("w1", n_reports, r=0.0, seed=seed, persons=persons):
            if not isinstance(op, wl.AppendOp):
                continue
            rep = wl.to_report(op, id_of)
            t0 = time.perf_counter_ns()
            res = backend.append(rep, idempotency_key=f"lo:{op.index}", admitter=NullAdmitter(), reviser=NullReviser())
            total += time.perf_counter_ns() - t0
            assert res.entry is not None and res.entry.report.id is not None
            id_of[op.index] = res.entry.report.id
            n += 1
    except (StoreError, ValueError, AssertionError) as exc:  # the store may refuse a revision-free append; report it rather than guess
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        backend.close()
    return total / n * 1e-6 if n else None, None


def run_crossover(
    n_reports: int, *, r_store: float = 3.0, seed: int = 1, workdir: str | Path = ".", persons: int | None = None,
    sample_keys: int = 40, max_seconds: float | None = None,
) -> dict[str, Any]:
    workdir = Path(workdir)
    db = workdir / f"crossover-{n_reports}.db"
    for suffix in ("", "-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    rep, mem = _run("w1", n_reports, r=r_store, seed=seed, db_path=db, max_seconds=max_seconds, trace_memory=False, persons=persons)
    reached = rep["reached"]["reports"]
    a_store = rep["appends"]["mean_ms"]
    q_store = rep["queries"]["mean_ms"]

    keys: list[Key] = []
    seen: set[tuple[str, str]] = set()
    for op in wl.generate("w1", n_reports, r=0.0, seed=seed, persons=persons):
        if isinstance(op, wl.AppendOp) and op.index < reached and op.cue.value != "withdraw" and (op.entity, op.attr) not in seen:
            seen.add((op.entity, op.attr))
            keys.append(Key(entity=op.entity, attr=op.attr))
    step = max(1, len(keys) // sample_keys)
    sample = keys[::step][:sample_keys]

    cold: list[int] = []
    for k in sample:
        mem.pipeline.invalidate()
        t0 = time.perf_counter_ns()
        mem.reviser.recompute(k, mem.backend)
        cold.append(time.perf_counter_ns() - t0)
    warm: list[int] = []
    mem.pipeline.invalidate()
    mem.reviser.recompute(sample[0], mem.backend)  # populate the evaluation cache for the head once
    for k in sample:
        t0 = time.perf_counter_ns()
        mem.reviser.recompute(k, mem.backend)
        warm.append(time.perf_counter_ns() - t0)
    q_cold = sum(cold) / len(cold) * 1e-6
    q_warm = sum(warm) / len(warm) * 1e-6

    a_replay, log_err = _log_only_cost_ms(reached, r_store, seed, persons, workdir)

    def rstar(q_replay: float) -> float | None:
        if a_replay is None or q_replay <= q_store:
            return None
        return round((a_store - a_replay) / (q_replay - q_store), 3)

    return {
        "report_version": REPORT_VERSION, "kind": "crossover", "env": env_info(),
        "params": {"n_reports": n_reports, "r_store": r_store, "seed": seed, "persons": persons, "sample_keys": len(sample)},
        "reached_reports": reached,
        "costs_ms": {
            "store_append_mean": a_store, "store_query_mean": q_store, "replay_append_log_only_mean": a_replay,
            "replay_query_cold_mean": round(q_cold, 4), "replay_query_warm_mean": round(q_warm, 4),
        },
        "log_only_error": log_err,
        "r_star_cold": rstar(q_cold),
        "r_star_warm": rstar(q_warm),
        "note": "r* = (a_store - a_replay) / (q_replay - q_store); None = replay never costs more per query, or the log-only append was not measurable",
    }
