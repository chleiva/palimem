"""EXPLORATORY, UNREGISTERED: the symbolic RETRACT-ACT benchmark on CURRENT main next to the registered results.

The registered runs (``results/{dev,test}-<system>.json``) correspond to the product behaviour of commit 034d520 (see
``registered_product_v1.py``). This script replays the same scenarios through ``palimem_system.run_system`` on the
product as it is NOW (no pin) and writes ``results/current-main-symbolic.json``: for every registered system and split,
the registered and the current-main score side by side and the exact list of decision points whose response differs.

It is symbolic (no model, no spend) and deliberately bypasses the one-run registry (``results/test_runs.json``): it
calls ``run_system`` directly and never ``palimem_system.main``, so it does not consume the registered test run and
it is not a registered result. The test split here is therefore a *comparison column*, not a second test result; the
scenarios and gold are frozen and nothing was tuned on it.

    python bench/agent/current_main_column.py [--out results/current-main-symbolic.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import palimem_system as ps
import score

RESULTS = HERE / "results"
SYSTEMS = ("justified", "recency", "lww")
KEYS = ("harmful_action_rate", "unnecessary_deferral_rate", "exact_match", "n", "missing")


def compare(split: str, system: str) -> dict:
    registered = json.loads((RESULTS / f"{split}-{system}.json").read_text())
    scns = score.load_scenarios(split=split)
    assert [s["id"] for s in scns] == registered["scenario_ids"], "scenario set changed since registration"
    current = ps.run_system(system, scns, "memory")
    differing = sorted(
        {(sc, p) for sc, pts in registered["responses"].items() for p, v in pts.items() if current["responses"].get(sc, {}).get(p) != v}
        | {(sc, p) for sc, pts in current["responses"].items() for p, v in pts.items() if registered["responses"].get(sc, {}).get(p) != v}
    )
    out: dict = {"split": split, "system": system, "differing_points": [list(x) for x in differing], "profiles": {}}
    for profile in ("default", "authority_source"):
        a = score.score(scns, registered["responses"], profile)["overall"]
        b = score.score(scns, current["responses"], profile)["overall"]
        out["profiles"][profile] = {"registered": {k: a[k] for k in KEYS}, "current_main": {k: b[k] for k in KEYS}}
    out["responses_current_main_at_differing_points"] = {
        f"{sc}/{p}": {"registered": registered["responses"].get(sc, {}).get(p), "current_main": current["responses"].get(sc, {}).get(p)}
        for sc, p in differing
    }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(RESULTS / "current-main-symbolic.json"))
    a = ap.parse_args(argv)
    rows = [compare(split, system) for split in ("dev", "test") for system in SYSTEMS]
    doc = {
        "note": "exploratory, unregistered; symbolic; registered columns = product behaviour of commit 034d520",
        "rows": rows,
    }
    Path(a.out).write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    for r in rows:
        d = r["profiles"]["default"]
        print(f"{r['split']:4} {r['system']:9} differing={['/'.join(x) for x in r['differing_points']] or '-'}  "
              f"HAR {d['registered']['harmful_action_rate']:.3f} -> {d['current_main']['harmful_action_rate']:.3f}  "
              f"exact {d['registered']['exact_match']:.3f} -> {d['current_main']['exact_match']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
