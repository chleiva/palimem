"""Fast-kernel differential (Lane B10, T-B10): the candidate fast kernel vs the frozen gold AND vs the enumeration kernel.

For every query of every frozen Setting 1 stream the harness answers twice through the same product path as
``harness.kernel_diff`` (convert, compat admission, justify, read the segment, project onto the v1 shape): once
with the enumeration kernel (the production kernel and audit oracle) and once with the candidate fast kernel
(``palimem.kernel.fast.dispatch_key``: the fast path inside its proven class, the enumeration outside it). Any
difference in status, assertion or alternatives, between fast and the gold or between fast and enumeration,
fails the run (strict; exit 1). With ``--provenance`` the profile projection (``oracle_flat_ids``) must equal the
study oracle's ``supporting_ids`` and the principled flatten must equal the enumeration kernel's, per query.

It also reports the routing (how many keys the fast path took, and why the others went to enumeration) and the
wall time of each path. ``--speed`` adds a micro-benchmark of fast vs enumeration against the number of reports
on a key (random adversarial instances), including sizes the enumeration cannot run.

``--inject-bug self-update`` runs the fast path with the other semantics than the gold: the run must then fail.

Exit codes: 0 all agree · 1 disagreement · 2 setup error.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from harness import frozen, study
from harness import kernel_diff as _kd
from harness.convert import to_converted
from harness.differential import signature
from harness.kernel_diff import (
    SEMANTIC,
    SEMANTIC_SU,
    StreamEval,
    answer_query,
    provenance_query,
    stream_names,
)
from palimem.kernel import (
    Justification,
    ResourceLimitedResult,
    check_schema,
    justify_key,
)
from palimem.kernel.fast import FastJustification, dispatch_key

INJECTIONS = ("none", "self-update")


class _EitherJustification(type):
    """``harness.kernel_diff.answer_query`` asserts ``isinstance(j, Justification)`` for ``changed`` slots. The
    fast justification has the same read interface but no common base class (a gap to close at promotion: a
    shared Protocol); this harness makes that one assertion accept both, without editing ``kernel_diff``."""

    def __instancecheck__(cls, inst: object) -> bool:
        return isinstance(inst, Justification | FastJustification)


class _Either(metaclass=_EitherJustification):
    pass


_kd.Justification = _Either  # type: ignore[misc,assignment]


class FastEval(StreamEval):
    """``StreamEval`` whose base keys go through the fast kernel's dispatch."""

    def __init__(self, conv: Any, inject: str = "none") -> None:
        super().__init__(conv)
        self.sem = SEMANTIC_SU if inject == "self-update" else SEMANTIC
        self.routes: Counter[str] = Counter()
        self.reasons: Counter[str] = Counter()
        self.seconds = 0.0

    def base(self, lsn: int, key: Any) -> Any:
        hit = self._base.get((lsn, key))
        if hit is not None:
            return hit
        entries = self.adm.admitted_by_key(lsn).get(key, [])
        t0 = time.perf_counter()
        d = dispatch_key(self.conv.kschema, key, entries, self.sem, change_from=self.conv.change_from)
        self.seconds += time.perf_counter() - t0
        if isinstance(d.result, ResourceLimitedResult):
            self.resource_limited += 1
            raise RuntimeError(f"resource limited: {d.result.detail}")  # noqa: TRY004
        self.routes[d.route] += 1
        if d.route != "fast":
            self.reasons[d.reason.split(" (")[0].split(" exceed")[0]] += 1
        self.relax[d.result.relax_level] += 1
        self._base[(lsn, key)] = d.result  # type: ignore[assignment]
        return d.result


class EnumEval(StreamEval):
    """The enumeration kernel, timed (the reference path)."""

    def __init__(self, conv: Any) -> None:
        super().__init__(conv)
        self.seconds = 0.0

    def base(self, lsn: int, key: Any) -> Justification:
        hit = self._base.get((lsn, key))
        if hit is not None:
            return hit
        entries = self.adm.admitted_by_key(lsn).get(key, [])
        t0 = time.perf_counter()
        j = justify_key(self.conv.kschema, key, entries, SEMANTIC, change_from=self.conv.change_from)
        self.seconds += time.perf_counter() - t0
        if isinstance(j, ResourceLimitedResult):
            self.resource_limited += 1
            raise RuntimeError(f"resource limited: {j.detail}")  # noqa: TRY004
        self.relax[j.relax_level] += 1
        self._base[(lsn, key)] = j
        return j


def _same(a: dict[str, Any], b: dict[str, Any], norm: Any) -> bool:
    return signature(a, norm) == signature(b, norm)


def run(frozen_dir: Path, st: Any, limit: int | None, stride: int, inject: str, provenance: bool,
        progress: bool) -> dict[str, Any]:
    names = stream_names(frozen_dir, limit, stride)
    if not names:
        raise frozen.FrozenError(f"no frozen streams found in {frozen_dir}")
    manifest = frozen.load_manifest()
    frozen.require_valid(frozen_dir, manifest, only=[n for name in names for n in (name, name.replace(".json", ".gold.json"))])
    t0 = time.time()
    tot: Counter[str] = Counter()
    per_slot: dict[str, Counter[str]] = {}
    routes: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    fast_seconds = enum_seconds = 0.0
    examples: list[dict[str, Any]] = []
    for i, name in enumerate(names):
        stream = st.load_stream(str(frozen_dir / name))
        gold = json.loads((frozen_dir / name.replace(".json", ".gold.json")).read_text())
        conv = to_converted(stream, "sidetable")
        check_schema(conv.kschema)
        fast = FastEval(conv, inject)
        enum = EnumEval(to_converted(stream, "sidetable"))
        for q in sorted(stream.queries, key=lambda q: (q.tau, q.id)):
            slot_key = q.slot + (":" + q.prop["kind"] if q.slot == "yesno" else "")
            c = per_slot.setdefault(slot_key, Counter())
            fa = answer_query(fast, q, Counter())
            ea = answer_query(enum, q, Counter())
            c["queries"] += 1
            tot["queries"] += 1
            bad = []
            if not _same(fa, gold[q.id], st.norm):
                bad.append("fast!=gold")
            if not _same(fa, ea, st.norm):
                bad.append("fast!=enumeration")
            if not _same(ea, gold[q.id], st.norm):
                bad.append("enumeration!=gold")
            if provenance:
                fc, fp, _ = provenance_query(fast, q, Counter())
                ec, ep, _ = provenance_query(enum, q, Counter())
                oracle = set(st.supporting_ids(stream, q, gold[q.id]))
                c["prov_queries"] += 1
                tot["prov_queries"] += 1
                if fc != oracle:
                    bad.append("fast-profile-provenance!=oracle")
                if fc != ec or fp != ep:
                    bad.append("fast-provenance!=enumeration")
            if bad:
                c["disagreements"] += 1
                tot["disagreements"] += 1
                for b in bad:
                    tot["by:" + b] += 1
                if len(examples) < 8:
                    examples.append({"stream": name, "query": q.id, "slot": slot_key, "what": bad,
                                     "fast": {k: fa.get(k) for k in ("status", "assertion", "alternatives")},
                                     "gold": {k: gold[q.id].get(k) for k in ("status", "assertion", "alternatives")}})
        routes.update(fast.routes)
        reasons.update(fast.reasons)
        fast_seconds += fast.seconds
        enum_seconds += enum.seconds
        tot["streams"] += 1
        tot["resource_limited"] += fast.resource_limited + enum.resource_limited
        tot["relax_events"] += sum(n for lv, n in fast.relax.items() if lv)
        if progress and (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(names)} streams, {tot['disagreements']} disagreements, {time.time() - t0:.0f}s", flush=True)
    return {
        "streams": tot["streams"], "queries": tot["queries"], "disagreements": tot["disagreements"],
        "by_kind": {k[3:]: v for k, v in tot.items() if k.startswith("by:")},
        "resource_limited": tot["resource_limited"], "relax_events": tot["relax_events"],
        "per_slot": {k: dict(v) for k, v in sorted(per_slot.items())},
        "routes": dict(routes), "enumeration_route_reasons": dict(reasons),
        "kernel_seconds": {"fast_path_dispatch": round(fast_seconds, 2), "enumeration": round(enum_seconds, 2)},
        "provenance": {"enabled": provenance, "queries": tot["prov_queries"]},
        "examples": examples, "inject_bug": inject, "wall_seconds": round(time.time() - t0, 1),
        "passed": tot["disagreements"] == 0 and tot["resource_limited"] == 0,
    }


# --------------------------------------------------------------------------- speed vs n


def speed(sizes: tuple[int, ...] = (6, 8, 10, 12, 14, 16, 24, 40, 70, 100), per_size: int = 5, enum_max: int = 16) -> list[dict[str, Any]]:
    from palimem.kernel.evidence import Ev
    from palimem.kernel.fast import justify_ev_fast
    from palimem.kernel.interpret import P0CSU, key_interpretations
    from palimem.kernel.spec import AttrSpec

    spec = AttrSpec(name="employer", cardinality="single", changeable=True)
    rows: list[dict[str, Any]] = []
    for n in sizes:
        rng = random.Random(1000 + n)
        fast_t: list[float] = []
        enum_t: list[float] = []
        for _ in range(per_size):
            ev = []
            for i in range(n):  # an agent-style log: a few origins re-stating values, occasional changes
                ev.append(Ev(id="01H" + str(i).zfill(23), anchor=rng.randint(0, 4 * n), value=f"v{rng.randint(0, 3)}",
                             op_cue="none", op_from=None, op_of=None, origin_group=f"g{rng.randint(0, 2)}", lsn=i))
            t0 = time.perf_counter()
            fj = justify_ev_fast(spec, ev, P0CSU, enumeration_limit=0)
            lo, hi = min(e.anchor for e in ev), max(e.anchor for e in ev)
            for t in range(lo - 1, hi + 2):
                fj.candidates_at(t)
            fj.segments()
            fast_t.append(time.perf_counter() - t0)
            if n <= enum_max:
                t0 = time.perf_counter()
                key_interpretations(spec, ev, P0CSU)
                enum_t.append(time.perf_counter() - t0)
        fast_t.sort()
        enum_t.sort()
        rows.append({"n": n, "fast_median_s": round(fast_t[len(fast_t) // 2], 4),
                     "enumeration_median_s": round(enum_t[len(enum_t) // 2], 4) if enum_t else None})
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness.fast_kernel_diff", description=__doc__.split("\n\n")[0])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--inject-bug", choices=INJECTIONS, default="none")
    ap.add_argument("--provenance", action="store_true", help="also compare provenance (profile vs oracle, principled vs enumeration)")
    ap.add_argument("--speed", action="store_true", help="add the speed-vs-n micro-benchmark")
    ap.add_argument("--study-dir")
    ap.add_argument("--frozen-dir")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--progress", action="store_true")
    ns = ap.parse_args(argv)
    try:
        st = study.load(ns.study_dir)
        fdir = frozen.locate_frozen(st.dir, explicit=ns.frozen_dir, fetch=ns.fetch)
        res = run(fdir, st, ns.limit, ns.stride, ns.inject_bug, ns.provenance, ns.progress)
    except (FileNotFoundError, frozen.FrozenError) as e:
        print(f"setup error: {e}", file=sys.stderr)
        return 2
    if ns.speed:
        res["speed"] = speed()
    print(f"streams {res['streams']}, queries {res['queries']}, disagreements {res['disagreements']}, "
          f"routes {res['routes']}, wall {res['wall_seconds']}s")
    for slot, c in res["per_slot"].items():
        print(f"  {slot:<18} {c['queries']:>7} queries  {c.get('disagreements', 0)} disagreements")
    print(f"  enumeration route reasons: {res['enumeration_route_reasons']}")
    print(f"  kernel seconds: {res['kernel_seconds']}")
    for row in res.get("speed", []):
        print(f"  n={row['n']:>3}  fast {row['fast_median_s']:>8}s   enumeration {row['enumeration_median_s']}")
    for ex in res["examples"][:5]:
        print("  DISAGREEMENT", json.dumps(ex, default=str)[:300])
    if ns.out:
        ns.out.parent.mkdir(parents=True, exist_ok=True)
        ns.out.write_text(json.dumps(res, indent=1, sort_keys=True))
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
