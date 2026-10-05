"""Budget cross-check (Lane B10, decision S-06): is the enumeration kernel exact at 8 to 12 reports per key?

The default environment budget is 7, the envelope the study's generator produces and the differential CI
validated. S-06 allows raising it to 12 ONLY if the enumeration kernel's answers at 8 to 12 admitted reports
on a key agree with the brute-force GLOBAL oracle (``revise_stream.oracle_v2``) on FRESHLY GENERATED streams.

What this script does
---------------------
* generates fresh seeded streams (seeds far from the frozen set's) with the study's own generator logic
  (``revise_stream.generator.Gen``: delays, reversals, correlated copies, trusted-source errors, genuine
  low-trust corrections, self-corrections, report and source retraction), restricted to a few keys and
  topped up (re-assertions, competing erroneous reports, corrections) until the focus key reaches the
  target report count; ``max_obs_per_key`` is raised from the study's 7 by a per-key cap override;
* keeps the TOTAL admitted reports of a stream at <= 12, because ``oracle_v2`` labels every admitted
  assertion of the stream jointly (2^N labellings): "cheap at 4096 subsets";
* for every stream and every report count n in 8..12 reached by the focus key, picks the latest belief time
  at which exactly n reports are admitted on it, asks every slot type there (current, asof, belief_asof,
  reported, holds, changed, erroneous, and the derived downstream slot), and compares the palimem kernel
  (``justify_key`` with budget 12, through the same ``StreamEval`` / ``answer_query`` path as
  ``harness.kernel_diff``) with the gold computed from ``oracle_v2`` interpretations, for P0c and P0cSU;
* gold comes from the study's own ``gold_for`` with the oracle_v2 interpretations plugged in.

Limitation (stated up front): ``oracle_v2`` implements P0c (and P0cc/P1) but NOT P0cSU (A-SU lives only in
``oracle_v1``). For P0cSU this script adds A-SU to the brute-force oracle in the global-labelling style, from
the text of Addendum A of the pre-registration ("an admitted report o may not be labelled ERR when every
accepted competitor of o is a strictly-earlier report from o's own origin and no accepted correction targets
o", single-valued changeable keys only), not from the kernel. It is therefore a second implementation of A-SU
written from the spec, not an independent oracle of it.

Exit code: 0 no disagreement · 1 disagreement · 2 setup error.
"""

from __future__ import annotations

import argparse
import functools
import json
import multiprocessing
import random
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from harness import study

SEED_BASE = 7_700_000  # the frozen Setting 1 set uses seeds from 0; CI smoke uses 90_000
N_RANGE = range(8, 13)
POLICIES = ("P0c", "P0cSU")
SHAPES = ("employer", "residence", "birth_date", "nickname", "derived", "tax", "selfupdate")
MAX_TOTAL = 12  # oracle_v2 enumerates 2^N labellings of ALL admitted assertions of the stream


# --------------------------------------------------------------------------- generation


def _gen_classes() -> Any:
    from revise_stream import generator as g

    return g


def build_stream(seed: int, shape: str) -> tuple[Any, dict[str, Any]]:
    """A fresh stream whose focus key is topped up to the shape's cap. Returns (Stream, meta)."""
    g = _gen_classes()
    from revise_stream.model import stream_from_dict

    cfg = g.GenConfig(seed=seed, T=70, n_persons=2, n_orgs=3, n_cities=6, max_obs_per_key=999,
                      p_change_per_10d=0.5, reports_mean=2.5, p_retract_obs=0.10, p_retract_source=0.30)
    rnd = random.Random(seed * 7919 + 13)
    focus_cap = rnd.randint(11, 14)  # >= target so a few retractions/withdrawals still leave 8..12 admitted
    if shape == "derived":
        cfg.n_orgs = 2
        caps = {("p0", "employer"): 9, ("org0", "hq_city"): 2, ("org1", "hq_city"): 1}
        focus = ("p0", "employer")
    else:
        attr = {"tax": "residence", "selfupdate": "employer"}.get(shape, shape)
        caps = {("p0", attr): focus_cap}
        focus = ("p0", attr)
    # the single-source self-update regime A-SU is written for: one origin (press, and its copier wire)
    # reports its own value as it changes; a rare cross-origin report (registry) keeps it honest
    restricted = shape == "selfupdate"

    class FreshGen(g.Gen):
        def build_world(self) -> None:
            super().build_world()
            self.world = [f for f in self.world if (f["entity"], f["attr"]) in caps]
            for (e, a) in caps:
                if not any((f["entity"], f["attr"]) == (e, a) for f in self.world):
                    self.world.append({"entity": e, "attr": a, "value": self._rand_value(a), "t0": 1, "prev": None, "kind": "initial"})

        def _add_assert(self, e: str, a: str, v: Any, t_rep: int, source: str, valid: Any = None, op_cue: str = "none",
                        op_from: Any = None, op_of: Any = None, truth: Any = None) -> Any:
            if self._count(e, a) >= caps.get((e, a), 0):
                return None
            return super()._add_assert(e, a, v, t_rep, source, valid, op_cue, op_from, op_of, truth)

        def report_world(self) -> None:
            if not restricted:
                super().report_world()
            self._topup()

        def _topup(self) -> None:
            r = self.rng
            src_names = [s for s in g.SOURCES if s not in g.COPIERS.values()]
            if restricted:
                src_names = ["press", "wire"]
            for (e, a), cap in caps.items():
                facts = sorted((f for f in self.world if (f["entity"], f["attr"]) == (e, a)), key=lambda f: f["t0"])
                if not facts:
                    continue
                guard = 0
                while self._count(e, a) < cap and guard < 200:
                    guard += 1
                    f = r.choice(facts)
                    s = "registry" if (restricted and r.random() < 0.06) else r.choice(src_names)
                    err = r.random() < (0.04 if restricted else g.GenConfig().p_error[g.SOURCES[s]["class"]] + 0.10)
                    val = self._rand_value(a, exclude=f["value"]) if err else f["value"]
                    used = {o["t"] for o in self.obs if o["kind"] == "assert" and (o["entity"], o["attr"]) == (e, a)}
                    free = [d for d in range(f["t0"] + 1, self.cfg.T + 1) if d not in used]
                    if not free:
                        break
                    t_rep = r.choice(free)  # distinct days, so every report count n is reached at some belief time
                    valid = {"cue": "since", "t": f["t0"]} if (g.ATTRS[a]["changeable"] and r.random() < 0.5) else None
                    op_cue, op_from = "none", None
                    if not err and f["kind"] in ("change", "reversal") and r.random() < 0.3:
                        op_cue = "change"
                        if r.random() < 0.5:
                            op_from = f["prev"]
                    o = self._add_assert(e, a, val, t_rep, s, valid, op_cue, op_from, truth=(not err))
                    if o is None:
                        continue
                    if err and r.random() < 0.6:
                        if r.random() < 0.5:
                            self._add_assert(e, a, f["value"], t_rep + 1 + self._delay(), s, valid, "correction", None, op_of=o["id"], truth=True)
                        else:
                            pool = [x for x in (["registry"] if restricted else src_names)
                                    if g.SOURCES[x]["origin"] != g.SOURCES[s]["origin"]] or ["registry"]
                            self._add_assert(e, a, f["value"], t_rep + 1 + self._delay(), r.choice(pool), valid, "correction", None,
                                             op_of=o["id"], truth=True)
                    if r.random() < 0.08:
                        t_r = t_rep + 1 + self._delay()
                        if t_r <= self.cfg.T:
                            self.obs.append({"id": self._oid(), "t": int(t_r), "source": s, "kind": "retract", "target": o["id"], "_truth": None})
            self.obs.sort(key=lambda o: (o["t"], int(o["id"][1:])))

        def make_queries(self) -> list[dict[str, Any]]:
            return []

    gen = FreshGen(cfg)
    d = gen.stream_dict(f"x_{shape}_{seed}")
    for k in ("_world", "_truth", "_poison"):
        d.pop(k, None)
    d["queries"] = []
    stream = stream_from_dict(d)
    return stream, {"focus": focus, "shape": shape}


def _count_admitted(stream: Any, tau: int, key: tuple[str, str]) -> int:
    return sum(1 for o in stream.admitted(tau) if (o.entity, o.attr) == key)


def _total_admitted(stream: Any, tau: int) -> int:
    return len(stream.admitted(tau))


def make_queries(stream: Any, shape: str, focus: tuple[str, str], rnd: random.Random) -> tuple[list[Any], dict[int, int]]:
    """Queries at the latest belief time for each n in 8..12 reached by the focus key (and total <= MAX_TOTAL)."""
    from revise_stream.model import Query

    if not stream.observations:
        return [], {}
    horizon = max(o.t_rep for o in stream.observations)
    best: dict[int, int] = {}
    for tau in range(1, horizon + 1):
        n = _count_admitted(stream, tau, focus)
        if n in N_RANGE and _total_admitted(stream, tau) <= MAX_TOTAL:
            best[n] = tau
    e, a = focus
    qs: list[Query] = []
    k = 0

    def add(slot: str, tau: int, **kw: Any) -> None:
        nonlocal k
        k += 1
        qs.append(Query(id=f"q{k}", slot=slot, tau=tau, tags=(f"n{kw.pop('_n')}",), **kw))

    for n, tau in sorted(best.items()):
        obs = [o for o in stream.admitted(tau) if (o.entity, o.attr) == focus]
        vals = sorted({str(o.value) for o in obs})
        by_str = {str(o.value): o.value for o in obs}
        add("current", tau, entity=e, attr=a, _n=n)
        for _ in range(2):
            add("asof", tau, entity=e, attr=a, t=rnd.randint(1, tau), _n=n)
        add("belief_asof", tau, entity=e, attr=a, tau_prime=tau, _n=n)
        add("reported", tau, entity=e, attr=a, _n=n)
        for _ in range(2):
            v = by_str[rnd.choice(vals)]
            add("yesno", tau, prop={"kind": "holds", "entity": e, "attr": a, "value": v, "t": rnd.randint(1, tau)}, _n=n)
        if len(vals) >= 2:
            for _ in range(2):
                v1, v2 = rnd.sample(vals, 2)
                add("yesno", tau, prop={"kind": "changed", "entity": e, "attr": a, "from": by_str[v1], "to": by_str[v2]}, _n=n)
        for o in rnd.sample(obs, k=min(3, len(obs))):
            add("yesno", tau, prop={"kind": "erroneous", "obs": o.id}, _n=n)
        if shape == "derived":
            add("downstream", tau, entity="p0", attr="work_city", _n=n)
            add("current", tau, entity="p0", attr="work_city", _n=n)
        if shape == "tax":
            add("downstream", tau, entity="p0", attr="local_tax_city", _n=n)
    return qs, best


# --------------------------------------------------------------------------- the global oracle (gold)


_V2_CACHE: dict[tuple[int, int, str], Any] = {}


def _su_admissible(orig: Any) -> Any:
    def admissible(stream: Any, obs: list[Any], label: dict[str, str], policy: str = "P0c", tau: int = 0) -> bool:
        if not orig(stream, obs, label, "P0c", tau):
            return False
        true = [o for o in obs if label[o.id] == "TRUE"]
        for o in obs:
            if label[o.id] != "ERR":
                continue
            spec = stream.attributes[o.attr]
            if spec.cardinality != "single" or not spec.changeable:
                continue
            if any(p.op_cue == "correction" and p.op_of == o.id for p in true):
                continue
            comp = [p for p in true if p.key == o.key and p.value != o.value]
            if comp and all(stream.sources[p.source].origin == stream.sources[o.source].origin and p.anchor < o.anchor for p in comp):
                return False
        return True

    return admissible


def v2_interps(stream: Any, tau: int, policy: str = "P0c", keys: Any = None) -> Any:
    """oracle_v2 interpretations, cached per (stream, tau, policy); P0cSU adds A-SU to the global oracle."""
    from revise_stream import oracle_v2

    ck = (id(stream), tau, policy)
    full = _V2_CACHE.get(ck)
    if full is None:
        if policy == "P0cSU":
            orig = oracle_v2._admissible
            oracle_v2._admissible = _su_admissible(orig)
            try:
                full = oracle_v2._interpretations(stream, tau, "P0cSU")
            finally:
                oracle_v2._admissible = orig
        else:
            full = oracle_v2._interpretations(stream, tau, policy)
        _V2_CACHE[ck] = full
    if keys is None:
        return full
    keys = set(keys)
    ids = {o.id for o in stream.admitted(tau) if o.key in keys}
    return {(err & ids, tuple((k, tl) for k, tl in tls if k in keys)) for err, tls in full}


# --------------------------------------------------------------------------- the kernel side


def _kernel_eval(conv: Any, policy: str, budget: int, inject: str = "none") -> Any:
    from harness.kernel_diff import SEMANTIC, SEMANTIC_SU, StreamEval
    from palimem.kernel import Justification, ResourceLimitedResult, justify_key

    sem = SEMANTIC_SU if policy == "P0cSU" else SEMANTIC
    if inject == "self-update":  # self-test: the kernel runs the OTHER semantics than the gold
        sem = SEMANTIC if policy == "P0cSU" else SEMANTIC_SU

    class BudgetEval(StreamEval):
        def base(self, lsn: int, key: Any) -> Justification:
            hit = self._base.get((lsn, key))
            if hit is not None:
                return hit
            entries = self.adm.admitted_by_key(lsn).get(key, [])
            j = justify_key(self.conv.kschema, key, entries, sem, change_from=self.conv.change_from, budget=budget)
            if isinstance(j, ResourceLimitedResult):
                self.resource_limited += 1
                raise RuntimeError(f"resource limited: {j.detail}")  # noqa: TRY004
            self.relax[j.relax_level] += 1
            self._base[(lsn, key)] = j
            return j

    return BudgetEval(conv)


def run_seed(args: tuple[int, str, int, str]) -> dict[str, Any]:
    seed, shape, budget, inject = args
    st = study.load()
    from revise_stream.gold import gold_for

    from harness.convert import to_converted
    from harness.differential import signature
    from harness.kernel_diff import answer_query

    rnd = random.Random(seed ^ 0x5EED)
    stream, meta = build_stream(seed, shape)
    qs, best = make_queries(stream, shape, meta["focus"], rnd)
    out: dict[str, Any] = {"seed": seed, "shape": shape, "states": sorted(best), "cells": {}, "examples": [],
                           "oracle_seconds": 0.0, "relax": {}, "gold_status": {}, "features": {}}
    for n_, tau_ in best.items():
        adm = [o for o in stream.admitted(tau_) if (o.entity, o.attr) == meta["focus"]]
        feats = {"states": 1, "multi_valued": int(len({str(o.value) for o in adm}) >= 2),
                 "corrections": int(any(o.op_cue == "correction" for o in adm)),
                 "change_cues": int(any(o.op_cue == "change" for o in adm)),
                 "origins>=3": int(len({stream.sources[o.source].origin for o in adm}) >= 3)}
        for k_, v_ in feats.items():
            out["features"][f"{n_}|{k_}"] = out["features"].get(f"{n_}|{k_}", 0) + v_
    if not qs:
        return out
    stream.queries = qs
    conv = to_converted(stream, "sidetable")
    for policy in POLICIES:
        interp = functools.partial(v2_interps, policy=policy)
        ev = _kernel_eval(conv, policy, budget, inject)
        for q in qs:
            n = int(next(t for t in q.tags if t.startswith("n"))[1:])
            slot = q.slot + (":" + q.prop["kind"] if q.slot == "yesno" else "")
            if shape in ("derived", "tax") and q.slot in ("downstream", "current") and q.attr in ("work_city", "local_tax_city"):
                slot = "derived:" + q.slot
            cell = out["cells"].setdefault(f"{policy}|{n}|{slot}", {"queries": 0, "disagreements": 0})
            t0 = time.time()
            gold = gold_for(stream, q, interp)
            out["oracle_seconds"] += time.time() - t0
            ans = answer_query(ev, q, Counter())
            cell["queries"] += 1
            if q.slot == "current" and q.attr == meta["focus"][1]:
                gk = f"{policy}|{n}|{gold['status']}"
                out["gold_status"][gk] = out["gold_status"].get(gk, 0) + 1
            if signature(ans, st.norm) != signature(gold, st.norm):
                cell["disagreements"] += 1
                if len(out["examples"]) < 5:
                    out["examples"].append({"seed": seed, "shape": shape, "policy": policy, "n": n, "slot": slot, "query": q.id,
                                            "kernel": {k: ans.get(k) for k in ("status", "assertion", "alternatives")},
                                            "gold": {k: gold.get(k) for k in ("status", "assertion", "alternatives")}})
        for lvl, c in ev.relax.items():
            out["relax"][str(lvl)] = out["relax"].get(str(lvl), 0) + c
    _V2_CACHE.clear()
    return out


# --------------------------------------------------------------------------- driver


def summarise(results: list[dict[str, Any]]) -> dict[str, Any]:
    cells: dict[str, dict[str, int]] = {}
    keys_by: dict[str, set[tuple[str, int]]] = {}
    examples: list[dict[str, Any]] = []
    relax: Counter[str] = Counter()
    gold_status: Counter[str] = Counter()
    features: Counter[str] = Counter()
    secs = 0.0
    for r in results:
        gold_status.update(r.get("gold_status", {}))
        features.update(r.get("features", {}))
        secs += r["oracle_seconds"]
        examples += r["examples"]
        relax.update(r["relax"])
        for k, c in r["cells"].items():
            agg = cells.setdefault(k, {"queries": 0, "disagreements": 0})
            agg["queries"] += c["queries"]
            agg["disagreements"] += c["disagreements"]
        for n in r["states"]:
            for p in POLICIES:
                keys_by.setdefault(f"{p}|{n}", set()).add((r["shape"], r["seed"]))
    per_n: dict[str, dict[str, Any]] = {}
    for k, c in cells.items():
        p, n, _slot = k.split("|", 2)
        agg = per_n.setdefault(f"{p}|n={n}", {"queries": 0, "disagreements": 0})
        agg["queries"] += c["queries"]
        agg["disagreements"] += c["disagreements"]
    for k, agg in per_n.items():
        p, n = k.split("|n=")
        agg["keys"] = len(keys_by.get(f"{p}|{n}", ()))
    return {"per_n": dict(sorted(per_n.items())), "cells": dict(sorted(cells.items())), "examples": examples[:20],
            "relax_levels": dict(relax), "oracle_seconds": round(secs, 1),
            "gold_status_current": dict(sorted(gold_status.items())), "features": dict(sorted(features.items())),
            "total_queries": sum(c["queries"] for c in cells.values()),
            "total_disagreements": sum(c["disagreements"] for c in cells.values())}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--streams-per-shape", type=int, default=40)
    ap.add_argument("--shapes", default=",".join(SHAPES))
    ap.add_argument("--budget", type=int, default=12)
    ap.add_argument("--workers", type=int, default=max(1, (multiprocessing.cpu_count() or 2) - 1))
    ap.add_argument("--seed-base", type=int, default=SEED_BASE)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--progress", action="store_true")
    ap.add_argument("--inject-bug", choices=("none", "self-update"), default="none",
                    help="self-test: the kernel runs the other semantics than the gold; the run must then FAIL")
    ns = ap.parse_args(argv)
    try:
        study.load()
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 2
    shapes = [s for s in ns.shapes.split(",") if s]
    jobs = [(ns.seed_base + 1000 * i + j, s, ns.budget) for i, s in enumerate(shapes) for j in range(ns.streams_per_shape)]
    t0 = time.time()
    results: list[dict[str, Any]] = []
    jobs = [(a, b, c, ns.inject_bug) for (a, b, c) in jobs]
    if ns.workers > 1:
        with multiprocessing.Pool(ns.workers) as pool:
            for i, r in enumerate(pool.imap_unordered(run_seed, jobs, chunksize=1), 1):
                results.append(r)
                if ns.progress and i % 20 == 0:
                    print(f"  {i}/{len(jobs)} streams, {time.time() - t0:.0f}s", flush=True)
    else:
        results = [run_seed(j) for j in jobs]
    summary = summarise(results)
    summary.update({"streams": len(jobs), "shapes": shapes, "budget": ns.budget, "seed_base": ns.seed_base,
                    "wall_seconds": round(time.time() - t0, 1), "workers": ns.workers})
    print(f"streams {summary['streams']}, queries {summary['total_queries']}, "
          f"disagreements {summary['total_disagreements']}, wall {summary['wall_seconds']}s")
    for k, c in summary["per_n"].items():
        print(f"  {k:<14} keys {c['keys']:>4}  queries {c['queries']:>6}  disagreements {c['disagreements']}")
    if ns.out:
        ns.out.parent.mkdir(parents=True, exist_ok=True)
        ns.out.write_text(json.dumps(summary, indent=1, sort_keys=True))
    for ex in summary["examples"][:5]:
        print("  DISAGREEMENT", json.dumps(ex, default=str)[:400])
    return 1 if summary["total_disagreements"] else 0


if __name__ == "__main__":
    sys.exit(main())
