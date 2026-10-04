"""R4.1 experiments. Writes JSON to research/r41/results/. Usage: python -m research.r41.bench [name ...]"""
from __future__ import annotations

import json
import os
import random
import statistics as stats
import sys
import time

from . import oracle_bridge as ob
from .collapse import answers as _answers_fn
from .collapse import blocks, reduced
from .instances import agent_log
from .kernel import KeyKernel, Rep

OUT = os.path.join(os.path.dirname(__file__), "results")


def save(name: str, obj) -> None:
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name + ".json"), "w") as f:
        json.dump(obj, f, indent=1, default=str)
    print(f"[{name}] saved")


def distinct_anchor_instance(rng: random.Random, n: int, n_corr: int = 0, n_values: int = 4, n_origins: int = 3,
                             p_change: float = 0.3, p_from: float = 0.6) -> list[Rep]:
    """n reports at distinct anchors (shuffled arrival), change cues/from, exactly n_corr cross-origin corrections."""
    vals = [f"v{i}" for i in range(n_values)]
    origins = [f"g{i}" for i in range(n_origins)]
    anchors = rng.sample(range(4 * n), n)
    reps: list[Rep] = []
    for i in range(n):
        v, g = rng.choice(vals), rng.choice(origins)
        cue, frm = "none", None
        if rng.random() < p_change:
            cue = "change"
            frm = rng.choice([w for w in vals if w != v]) if rng.random() < p_from else None
        reps.append(Rep(i, v, anchors[i], g, cue, frm))
    for c in rng.sample(range(n), min(n_corr, n)):
        r = reps[c]
        tgts = [t for t in reps if t.origin != r.origin and t.id != r.id]
        if tgts:
            reps[c] = Rep(r.id, r.value, r.anchor, r.origin, "correction", None, rng.choice(tgts).id)
    return reps


def fast_full(reps: list[Rep], policy: str = "P0cSU") -> dict:
    t0 = time.perf_counter()
    k = KeyKernel(reps, policy)
    ts = sorted({r.anchor for r in reps} | {r.anchor + 1 for r in reps} | {min(r.anchor for r in reps) - 1})
    ok = k.any_admissible()
    cands = k.candidates_all(ts) if ok else {}
    t1 = time.perf_counter()
    if ok:
        for r in reps:
            k.erroneous(r.id)
    t2 = time.perf_counter()
    return {"admissible": ok, "sec_cand": t1 - t0, "sec_err": t2 - t1, "branches": k.n_branches,
            "n_cands_at_end": len(cands[ts[-1]]) if ok else 0}


def oracle_time(reps: list[Rep], policy: str) -> dict:
    ob._load()
    from revise_stream import oracle_v1
    s = ob.build_stream(reps)
    obs = s.admitted_by_key(len(reps) + 1)[ob.KEY]
    t0 = time.perf_counter()
    interps = oracle_v1._key_interps(s, ob.KEY, obs, policy, len(reps) + 1)
    return {"sec": time.perf_counter() - t0, "n_interps": len(interps)}


# ---------------------------------------------------------------------------------------------
def exp_scaling(seed: int = 5) -> dict:
    rng = random.Random(seed)
    rows = []
    for n in (8, 10, 12, 14, 16):
        reps = distinct_anchor_instance(rng, n, n_corr=0)
        o = oracle_time(reps, "P0cSU")
        f = fast_full(reps)
        rows.append({"n": n, "oracle_sec": o["sec"], "oracle_interps": o["n_interps"], "fast_sec": f["sec_cand"] + f["sec_err"],
                     "fast_branches": f["branches"]})
        print("scaling", rows[-1], flush=True)
    # extrapolate oracle: t ~ c * 2^n
    c = stats.mean(r["oracle_sec"] / 2 ** r["n"] for r in rows[-3:])
    big = []
    for n in (12, 16, 24, 50, 100, 200):
        res = []
        for _ in range(3):
            reps = distinct_anchor_instance(rng, n, n_corr=0)
            res.append(fast_full(reps))
        big.append({"n": n, "fast_sec_cand_median": stats.median(x["sec_cand"] for x in res),
                    "fast_sec_err_median": stats.median(x["sec_err"] for x in res),
                    "branches_max": max(x["branches"] for x in res),
                    "oracle_projected_sec": c * 2 ** n})
        print("fast", big[-1], flush=True)
    return {"measured_oracle_vs_fast": rows, "fast_at_scale": big, "oracle_sec_per_2n": c}


def exp_corrections(seed: int = 6) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    for c in (0, 2, 4, 6, 8, 10, 12, 14):
        res = []
        for _ in range(3):
            reps = distinct_anchor_instance(rng, 30, n_corr=c)
            res.append(fast_full(reps))
        rows.append({"corrections": c, "n": 30, "sec_median": stats.median(x["sec_cand"] + x["sec_err"] for x in res),
                     "branches_max": max(x["branches"] for x in res)})
        print("corr", rows[-1], flush=True)
    return rows


def exp_conflicts(seed: int = 7) -> list[dict]:
    """g anchors that each carry two different values (same-day contradictory reports)."""
    rng = random.Random(seed)
    rows = []
    for g in (0, 2, 4, 6, 8, 10, 12):
        res = []
        for _ in range(3):
            n = 30
            reps = distinct_anchor_instance(rng, n, 0)
            for j in range(g):                              # force a tie on g anchors
                a = reps[j].anchor
                b = reps[j + g] if j + g < n else reps[-1]
                reps[j + g] = Rep(b.id, f"v{(int(reps[j].value[1:]) + 1) % 4}", a, b.origin, b.cue, b.op_from, b.op_of)
            res.append(fast_full(reps))
        rows.append({"tied_anchors": g, "n": 30, "sec_median": stats.median(x["sec_cand"] + x["sec_err"] for x in res),
                     "branches_max": max(x["branches"] for x in res)})
        print("conflicts", rows[-1], flush=True)
    return rows


def exp_output_size() -> dict:
    """Inherent exponential output: n distinct values at distinct anchors, no cues."""
    ob._load()
    rows = []
    for n in range(2, 13):
        reps = [Rep(i, f"v{i}", i, f"g{i}") for i in range(n)]
        s, interps = ob.oracle_interps(reps, "P0c")
        cands = ob.oracle_cand(s, interps, n + 1)
        k = KeyKernel(reps, "P0c")
        fast_c = k.candidates_all([n + 1])[n + 1]
        rows.append({"n": n, "interpretations": len(interps), "expected_2n_minus_1": 2 ** n - 1,
                     "candidates_at_now": len(cands), "fast_matches": fast_c == cands})
        print("output", rows[-1], flush=True)
    return {"single_valued": rows}


def exp_multi() -> list[dict]:
    """Multi-valued changeable key: distinct values at distinct anchors; size of the alternatives list."""
    ob._load()
    from revise_stream import oracle_v1
    from revise_stream.timeline import observed_candidates
    rows = []
    for n in range(2, 11):
        reps = [Rep(i, f"v{i}", i, f"g{i}") for i in range(n)]
        s = ob.build_stream(reps, cardinality="multi")
        t0 = time.perf_counter()
        interps = oracle_v1.key_interpretations(s, n + 1, ob.KEY, "P0c")
        cands = set()
        for it in interps:
            cands |= observed_candidates(s, it[1], s.attributes["a"], n + 1)
        rows.append({"n": n, "interpretations": len(interps), "alternatives_at_now": len(cands),
                     "sec": time.perf_counter() - t0})
        print("multi", rows[-1], flush=True)
    return rows


# ---------------------------------------------------------------------------------------------
def exp_collapse_size(seed: int = 8) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    for sessions in (10, 30, 60, 120):
        for p_burst in (0.0, 0.35, 0.7):
            raw, bo, bv = [], [], []
            for _ in range(200):
                reps = agent_log(rng, sessions, p_dup_origin_burst=p_burst)
                if not reps:
                    continue
                raw.append(len(reps))
                bo.append(len(blocks(reps, True)))
                bv.append(len(blocks(reps, False)))
            rows.append({"sessions": sessions, "burst": p_burst, "mean_n_raw": stats.mean(raw),
                         "mean_n_blocks_same_origin": stats.mean(bo), "mean_n_blocks_same_value": stats.mean(bv),
                         "max_n_raw": max(raw), "max_n_blocks_same_origin": max(bo), "max_n_blocks_same_value": max(bv)})
            print("collapse-size", rows[-1], flush=True)
    return rows


def exp_collapse_semantics(seed: int = 9, n_instances: int = 400) -> dict:
    """Is collapsing consecutive same-origin same-value reports answer-preserving?
    tie  : keep all reports but only interpretations whose ERR set is a union of whole blocks
    first: keep only the first report of each block      last: keep only the last report of each block"""
    ob._load()
    rng = random.Random(seed)
    res = {"instances": 0, "with_blocks_gt1": 0, "tie_equal": 0, "first_equal": 0, "last_equal": 0,
           "tie_equal_at_now": 0, "first_equal_at_now": 0, "last_equal_at_now": 0, "tie_inadmissible": 0, "examples": []}
    for _ in range(n_instances):
        sessions = rng.randint(4, 9)
        reps = agent_log(rng, sessions, n_changes=rng.randint(0, 2), p_dup_origin_burst=0.5, origins=("user", "crm"))
        if not 3 <= len(reps) <= 11:
            continue
        pol = rng.choice(["P0c", "P0cSU"])
        bl = blocks(reps, True)
        res["instances"] += 1
        if all(len(b) == 1 for b in bl):
            continue
        res["with_blocks_gt1"] += 1
        ts = list(range(min(r.anchor for r in reps) - 1, max(r.anchor for r in reps) + 3))
        s, interps = ob.oracle_interps(reps, pol)
        base = _answers_fn(s, interps, ts)
        block_ids = [frozenset(f"o{r.id}" for r in b) for b in bl]
        tied = [it for it in interps if all(b <= it[0] or not (b & it[0]) for b in block_ids)]
        if not tied:
            res["tie_inadmissible"] += 1
        tie_ans = _answers_fn(s, tied, ts) if tied else None
        outcomes = {"tie": tie_ans}
        for name, pick in (("first", lambda b: b[0]), ("last", lambda b: b[-1])):
            s2, i2 = ob.oracle_interps(reduced(reps, pick), pol)
            outcomes[name] = _answers_fn(s2, i2, ts)
        for name, ans in outcomes.items():
            if ans is None:
                continue
            if ans == base:
                res[f"{name}_equal"] += 1
            if ans[ts[-1]] == base[ts[-1]]:
                res[f"{name}_equal_at_now"] += 1
        if outcomes["tie"] is not None and outcomes["tie"] != base and len(res["examples"]) < 3:
            diff_t = [t for t in ts if outcomes["tie"][t] != base[t]]
            res["examples"].append({"policy": pol, "reps": [r.__dict__ for r in reps], "differs_at_t": diff_t[:4],
                                    "oracle": [sorted(map(sorted, base[diff_t[0]]))], "tie": [sorted(map(sorted, outcomes["tie"][diff_t[0]]))]})
    print("collapse-semantics", {k: v for k, v in res.items() if k != "examples"}, flush=True)
    return res


EXPS = {"scaling": exp_scaling, "corrections": exp_corrections, "conflicts": exp_conflicts, "output": exp_output_size,
        "multi": exp_multi, "collapse_size": exp_collapse_size, "collapse_semantics": exp_collapse_semantics}

if __name__ == "__main__":
    names = sys.argv[1:] or list(EXPS)
    for n in names:
        save(n, EXPS[n]())
