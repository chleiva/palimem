"""Differential harness (T-E1): incremental store vs symbolic replay vs frozen gold.

For every query of every frozen Setting 1 stream the harness asks the same question of

  * the incremental store    ``palimpsest.core.BeliefStore``   (writes interleaved with reads)
  * symbolic replay          ``baselines.structured.LogQueryTime`` (cold: recompute from the log)
  * the frozen oracle gold   ``s1_NNNN.gold.json``

and fails on ANY difference in status, assertion or alternatives. Provenance differences are
counted and reported but do not fail the run: the study measured 2.3 to 5.3% provenance
disagreement and closing it is task T-B4, not a conformance bug.

This mirrors ``scripts/run_setting1.py`` and ``scripts/systems_bench.py`` of the study (same
policy P0c, same query order, ``ingest_until`` interleaving inside ``BeliefStore.answer``); it
reuses their classes rather than re-implementing the semantics.

Exit codes: 0 all agree · 1 disagreement found · 2 setup error (missing data, checksum failure).

Self-test: ``--inject-bug {drop-propagation,mutate-answer}`` perturbs the store; the harness
must then exit 1. CI runs this to prove the gate can fail.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import sys
import time
from pathlib import Path

from . import frozen, study

POLICY = "P0c"
STREAM_RE = re.compile(r"^s1_\d+\.json$")
INJECTIONS = ("none", "drop-propagation", "mutate-answer")


def stream_names(frozen_dir: Path, limit: int | None = None, stride: int = 1) -> list[str]:
    names = sorted(p.name for p in frozen_dir.iterdir() if STREAM_RE.match(p.name))
    names = names[::stride]
    return names[:limit] if limit else names


def signature(ans: dict, norm) -> tuple:
    return (ans["status"], norm(ans.get("assertion")), frozenset(norm(a) for a in (ans.get("alternatives") or [])))


def _describe(ans: dict) -> dict:
    return {"status": ans["status"], "assertion": ans.get("assertion"), "alternatives": ans.get("alternatives")}


def _mutate(ans: dict) -> dict:
    out = dict(ans)
    out["status"] = "unresolved" if ans["status"] == "established" else "established"
    out["assertion"] = None if ans["status"] == "established" else ans.get("assertion")
    return out


def run(frozen_dir: Path, st, limit: int | None = None, stride: int = 1, inject: str = "none",
        manifest: dict | None = None, max_examples: int = 10, progress: bool = False) -> dict:
    if inject not in INJECTIONS:
        raise ValueError(f"unknown injection {inject!r}")
    manifest = manifest or frozen.load_manifest()
    names = stream_names(frozen_dir, limit, stride)
    if not names:
        raise frozen.FrozenError(f"no frozen streams found in {frozen_dir}")
    needed = [n for name in names for n in (name, name.replace(".json", ".gold.json"))]
    frozen.require_valid(frozen_dir, manifest, only=needed)  # refuse to run on drifted files

    propagation = "off" if inject == "drop-propagation" else "on"
    t0 = time.time()
    totals = {"streams": 0, "queries": 0, "store_vs_replay": 0, "store_vs_gold": 0, "provenance_diffs": 0,
              "streams_with_disagreement": 0}
    examples: list[dict] = []
    for i, name in enumerate(names):
        path = frozen_dir / name
        gold = json.loads((frozen_dir / name.replace(".json", ".gold.json")).read_text())
        s_store, s_replay = st.load_stream(str(path)), st.load_stream(str(path))
        qs = sorted(s_store.queries, key=lambda q: (q.tau, q.id))
        store = st.BeliefStore(s_store, propagation=propagation, policy=POLICY)
        replay = st.LogQueryTime(s_replay, policy=POLICY)
        mutated = False
        stream_bad = False
        for q in qs:
            a_store = store.answer(q)
            if inject == "mutate-answer" and not mutated and a_store["status"] == "established":
                a_store, mutated = _mutate(a_store), True
            a_replay = replay.answer(q)
            totals["queries"] += 1
            sig_store, sig_replay, sig_gold = (signature(a_store, st.norm), signature(a_replay, st.norm),
                                               signature(gold[q.id], st.norm))
            if sig_store != sig_replay:
                totals["store_vs_replay"] += 1
                stream_bad = True
                if len(examples) < max_examples:
                    examples.append({"stream": name, "query": q.id, "slot": q.slot, "kind": "store_vs_replay",
                                     "store": _describe(a_store), "replay": _describe(a_replay)})
            if sig_store != sig_gold:
                totals["store_vs_gold"] += 1
                stream_bad = True
                if len(examples) < max_examples:
                    examples.append({"stream": name, "query": q.id, "slot": q.slot, "kind": "store_vs_gold",
                                     "store": _describe(a_store), "gold": _describe(gold[q.id])})
            if set(a_store.get("provenance") or []) != set(a_replay.get("provenance") or []):
                totals["provenance_diffs"] += 1
        totals["streams"] += 1
        totals["streams_with_disagreement"] += int(stream_bad)
        if progress and (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(names)} streams, {totals['store_vs_replay']} store/replay and "
                  f"{totals['store_vs_gold']} store/gold disagreements, {time.time() - t0:.0f}s", flush=True)
    return {
        **totals,
        "seconds": round(time.time() - t0, 1),
        "passed": totals["store_vs_replay"] == 0 and totals["store_vs_gold"] == 0,
        "examples": examples,
        "provenance_diff_rate": round(totals["provenance_diffs"] / max(1, totals["queries"]), 4),
        "inject_bug": inject,
        "policy": POLICY,
        "frozen_dir": str(frozen_dir),
        "frozen_files_verified": len(needed),
        "study_commit": st.commit,
        "study_commit_pinned": study.pin()["commit"],
        "study_commit_matches_pin": st.commit == study.pin()["commit"],
        "python": platform.python_version(),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness.differential", description=__doc__.split("\n\n")[0])
    ap.add_argument("--limit", type=int, help="only the first N streams (after --stride)")
    ap.add_argument("--stride", type=int, default=1, help="take every k-th stream")
    ap.add_argument("--inject-bug", choices=INJECTIONS, default="none",
                    help="self-test: perturb the store; the harness must then exit 1")
    ap.add_argument("--study-dir", help="PALIMPSEST study checkout (default $PALIMPSEST_STUDY_DIR or ~/palimpsest)")
    ap.add_argument("--frozen-dir", help="directory with the frozen data/setting1 files")
    ap.add_argument("--fetch", action="store_true", help="download the Zenodo deposit if no local copy exists")
    ap.add_argument("--out", help="write the JSON report here")
    ap.add_argument("--max-examples", type=int, default=10)
    a = ap.parse_args(argv)
    try:
        st = study.load(a.study_dir)
        frozen_dir = frozen.locate_frozen(st.dir, a.frozen_dir, a.fetch)
        if st.commit != study.pin()["commit"]:
            print(f"warning: study checkout is at {st.commit}, harness pinned to {study.pin()['commit']}",
                  file=sys.stderr)
        report = run(frozen_dir, st, a.limit, a.stride, a.inject_bug, max_examples=a.max_examples, progress=True)
    except (FileNotFoundError, frozen.FrozenError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=1) + "\n")
    verdict = "PASS" if report["passed"] else "FAIL"
    print(f"{verdict}: {report['streams']} streams, {report['queries']} queries; "
          f"store vs replay {report['store_vs_replay']} disagreements, store vs gold {report['store_vs_gold']}; "
          f"provenance differs on {report['provenance_diffs']} ({100 * report['provenance_diff_rate']:.1f}%, informational); "
          f"{report['seconds']}s [inject={report['inject_bug']}]")
    for ex in report["examples"][:3]:
        print("  e.g.", json.dumps(ex)[:300])
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
