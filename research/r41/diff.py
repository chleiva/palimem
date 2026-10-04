"""Differential comparison: fast kernel vs oracle_v1 on query answers (cand(t), erroneous, changed)."""
from __future__ import annotations

import random

from . import oracle_bridge as ob
from .instances import random_instance
from .kernel import Rep, with_ladder


def fast_answers(reps: list[Rep], policy: str, ts, spec) -> dict:
    k, level = with_ladder(reps, policy, spec.get("error_allowed", True), spec.get("competing", True))
    out: dict = {"level": level, "cand": {}, "err": {}, "chg": {}}
    if not reps:
        out["cand"] = {t: {frozenset()} for t in ts}
        return out
    vals = sorted({r.value for r in reps}, key=repr)
    if k is None:                                  # level 3: everything rejected, ERR = all, no timeline
        out["cand"] = {t: {frozenset()} for t in ts}
        out["err"] = {r.id: (False, True) for r in reps}
        out["chg"] = {(a, b): (False, True) for a in vals for b in vals if a != b}
        return out
    out["cand"] = k.candidates_all(ts)
    out["err"] = {r.id: k.erroneous(r.id) for r in reps}
    out["chg"] = {(a, b): k.changed(a, b) for a in vals for b in vals if a != b}
    return out


def oracle_answers(reps: list[Rep], policy: str, ts, spec) -> dict:
    s, interps = ob.oracle_interps(reps, policy, **{k: v for k, v in spec.items() if k in ("error_allowed", "competing")})
    out: dict = {"cand": {t: ob.oracle_cand(s, interps, t) for t in ts}, "err": {}, "chg": {}}
    vals = sorted({r.value for r in reps}, key=repr)
    out["err"] = {r.id: ob.oracle_erroneous(interps, r.id) for r in reps}
    out["chg"] = {(a, b): ob.oracle_changed(interps, a, b) for a in vals for b in vals if a != b}
    return out


def compare(reps: list[Rep], policy: str, spec: dict | None = None) -> list[str]:
    spec = spec or {}
    lo = min((r.anchor for r in reps), default=0) - 1
    hi = max((r.anchor for r in reps), default=0) + 2
    ts = list(range(lo, hi + 1))
    f, o = fast_answers(reps, policy, ts, spec), oracle_answers(reps, policy, ts, spec)
    bad = []
    for t in ts:
        if f["cand"][t] != o["cand"][t]:
            bad.append(f"cand t={t}: fast={sorted(map(sorted, f['cand'][t]))} oracle={sorted(map(sorted, o['cand'][t]))}")
    for rid in f["err"]:
        if f["err"][rid] != o["err"][rid]:
            bad.append(f"err o{rid}: fast={f['err'][rid]} oracle={o['err'][rid]}")
    for pair in f["chg"]:
        if f["chg"][pair] != o["chg"][pair]:
            bad.append(f"changed {pair}: fast={f['chg'][pair]} oracle={o['chg'][pair]}")
    return bad


def run_random(n_instances: int, seed: int, max_n: int = 9, policies=("P0", "P0c", "P0cc", "P0cSU")) -> dict:
    rng = random.Random(seed)
    stats = {"instances": 0, "mismatches": 0, "relaxed": 0, "examples": []}
    for i in range(n_instances):
        n = rng.randint(1, max_n)
        reps = random_instance(rng, n, n_values=rng.choice([2, 3, 4]), n_origins=rng.choice([1, 2, 3]),
                               anchor_span=rng.choice([4, 8, 14]), p_change=rng.choice([0.0, 0.3, 0.5]),
                               p_from=rng.choice([0.0, 0.6, 1.0]), p_corr=rng.choice([0.0, 0.15, 0.3]))
        spec = {}
        r = rng.random()
        if r < 0.08:
            spec = {"error_allowed": False}
        elif r < 0.16:
            spec = {"competing": False}
        policy = rng.choice(policies)
        bad = compare(reps, policy, spec)
        stats["instances"] += 1
        if bad:
            stats["mismatches"] += 1
            if len(stats["examples"]) < 5:
                stats["examples"].append({"policy": policy, "spec": spec, "reps": [r.__dict__ for r in reps], "diff": bad[:3]})
    return stats
