"""Recovery after interruption (target T4): kill an appending process with SIGKILL, reopen in a fresh interpreter.

Two child modes of this module (``python -m bench.perf.recovery <mode> ...``):

``load``   append a seeded W1 stream to a SQLite file; print ``READY <lsn>`` after the preload and ``ACK <lsn>`` after every
           append that returned (so the parent knows what was acknowledged before the kill).
``probe``  in a **fresh interpreter** (imports included, as after a real crash): open the file, build ``Memory``, run
           ``recover()``, answer one query about the last acknowledged key, and optionally run ``verify_log`` and
           ``verify_beliefs``. Prints one JSON line.

:func:`run_recovery` orchestrates: start ``load``, wait for READY, let the load run a random 50 to 600 ms more, SIGKILL,
collect the last acknowledged LSN, start ``probe`` and time it from process start. Integrity checked: no acknowledged
append lost (head >= last ACK), ``recover().ok``, ``verify_log`` ok and (optionally) ``verify_beliefs`` ok, i.e. no partial
revision.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from bench.perf import workloads as wl
from bench.perf.runner import REPORT_VERSION, db_bytes, env_info, make_memory
from palimem.store import LimitedRead
from palimem.types import Key, Profile, Query, Resolved


def _load(db: str, n: int, preload: int, seed: int, persons: int | None) -> None:
    mem, _backend = make_memory(n, db, persons)
    id_of: dict[int, str] = {}
    done = 0
    for op in wl.generate("w1", n, r=0.0, seed=seed, persons=persons):
        if not isinstance(op, wl.AppendOp):
            continue
        res = mem.append(wl.to_report(op, id_of), idempotency_key=f"rec:{seed}:{op.index}")
        assert res.entry is not None and res.entry.report.id is not None
        id_of[op.index] = res.entry.report.id
        done += 1
        if done == preload:
            print(f"READY {res.entry.lsn}", flush=True)
        elif done > preload:
            print(f"ACK {res.entry.lsn}", flush=True)
    print(f"DONE {done}", flush=True)


def _probe(db: str, n: int, seed: int, persons: int | None, probe_lsn: int, verify: bool, verify_beliefs: bool) -> dict[str, Any]:
    t0 = time.perf_counter()
    mem, backend = make_memory(n, db, persons)
    t_open = time.perf_counter() - t0
    t1 = time.perf_counter()
    rec = backend.recover()
    t_recover = time.perf_counter() - t1
    head = backend.head().lsn
    entry = next(iter(backend.scan(from_lsn=min(max(probe_lsn, 1), max(head, 1)), to_lsn=None)), None)
    key = entry.report.key if entry is not None and hasattr(entry, "report") else Key(entity="p0", attr="employer")
    t2 = time.perf_counter()
    ans = mem.query(Query(key=key, profile=Profile.OPEN_WORLD))
    got = mem.read(key)
    t_query = time.perf_counter() - t2
    out: dict[str, Any] = {
        "open_s": round(t_open, 4), "recover_s": round(t_recover, 4), "first_query_s": round(t_query, 4),
        "in_process_total_s": round(time.perf_counter() - t0, 4), "head_lsn": head, "recover_ok": rec.ok,
        "recover_problems": list(rec.problems), "answer_ok": isinstance(ans, Resolved) and got is not None and not isinstance(got, LimitedRead),
    }
    if verify:
        t3 = time.perf_counter()
        v = backend.verify_log()
        out["verify_log_s"], out["verify_log_ok"], out["verify_log_checked"] = round(time.perf_counter() - t3, 3), v.ok, v.checked
    if verify_beliefs:
        t4 = time.perf_counter()
        vb = backend.verify_beliefs(mem.reviser)
        out["verify_beliefs_s"], out["verify_beliefs_ok"] = round(time.perf_counter() - t4, 3), vb.ok
    backend.close()
    return out


def run_recovery(
    preload: int, *, extra: int = 400, seed: int = 1, workdir: str | Path, persons: int | None = None,
    verify_beliefs: bool = True, load_timeout_s: float = 1800.0,
) -> dict[str, Any]:
    n = preload + extra
    db = str(Path(workdir) / f"recovery-{preload}.db")
    for suffix in ("", "-wal", "-shm"):
        Path(db + suffix).unlink(missing_ok=True)
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(root / "src"), str(root), os.environ.get("PYTHONPATH", "")]))}
    cmd = [sys.executable, "-m", "bench.perf.recovery", "load", "--db", db, "--n", str(n), "--preload", str(preload), "--seed", str(seed)]
    if persons is not None:
        cmd += ["--persons", str(persons)]
    child = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, env=env)
    assert child.stdout is not None
    ready_lsn = 0
    acked = 0
    t_load = time.perf_counter()
    for line in child.stdout:
        if line.startswith("READY"):
            ready_lsn = int(line.split()[1])
            break
        if time.perf_counter() - t_load > load_timeout_s:
            child.kill()
            raise TimeoutError("preload did not finish")
    rng = random.Random(f"kill:{seed}:{preload}")
    delay = rng.uniform(0.05, 0.6)
    t_ready = time.perf_counter()
    last_ack = ready_lsn
    finished_early = False
    while time.perf_counter() - t_ready < delay:
        line = child.stdout.readline()
        if not line:
            finished_early = True
            break
        if line.startswith("ACK"):
            last_ack = int(line.split()[1])
        elif line.startswith("DONE"):
            finished_early = True
            break
    if not finished_early:
        child.send_signal(signal.SIGKILL)
    for line in child.stdout:  # drain what was acknowledged before the kill took effect
        if line.startswith("ACK"):
            last_ack = int(line.split()[1])
    child.wait()
    acked = last_ack
    probe_cmd = [
        sys.executable, "-m", "bench.perf.recovery", "probe", "--db", db, "--n", str(n), "--seed", str(seed), "--probe-lsn", str(acked), "--verify",
    ]
    if verify_beliefs:
        probe_cmd.append("--verify-beliefs")
    if persons is not None:
        probe_cmd += ["--persons", str(persons)]
    t0 = time.perf_counter()
    p = subprocess.run(probe_cmd, capture_output=True, text=True, env=env, check=False)
    wall = time.perf_counter() - t0
    if p.returncode != 0:
        raise RuntimeError(f"probe failed: {p.stderr[-800:]}")
    pr = json.loads(p.stdout.strip().splitlines()[-1])
    lost = max(0, acked - pr["head_lsn"])
    return {
        "report_version": REPORT_VERSION, "kind": "recovery", "env": env_info(),
        "params": {"preload": preload, "extra": extra, "seed": seed, "persons": persons, "verify_beliefs": verify_beliefs},
        "killed": not finished_early, "kill_delay_s": round(delay, 3), "last_acked_lsn": acked, "head_lsn_after": pr["head_lsn"],
        "acknowledged_appends_lost": lost, "committed_but_unacknowledged": max(0, pr["head_lsn"] - acked),
        "start_to_first_correct_query_s": round(wall, 3), "probe": pr, "db_bytes": db_bytes(db),
        "integrity_ok": bool(lost == 0 and pr["recover_ok"] and pr.get("verify_log_ok", True) and pr.get("verify_beliefs_ok", True) and pr["answer_ok"]),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.perf.recovery")
    ap.add_argument("mode", choices=["load", "probe"])
    ap.add_argument("--db", required=True)
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--preload", type=int, default=0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--persons", type=int, default=None)
    ap.add_argument("--probe-lsn", type=int, default=1)
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--verify-beliefs", action="store_true")
    a = ap.parse_args(argv)
    if a.mode == "load":
        _load(a.db, a.n, a.preload, a.seed, a.persons)
    else:
        print(json.dumps(_probe(a.db, a.n, a.seed, a.persons, a.probe_lsn, a.verify, a.verify_beliefs)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
