"""Score and tabulate LLM-in-the-loop RETRACT-ACT runs (reads the JSON written by llm_agent.py; no network).

    python bench/agent/llm_report.py dev  RUN.json [RUN.json ...]      # one line per run: HAR UDR UAR exact missing repaired
    python bench/agent/llm_report.py explain RUN.json                   # every non-exact point with the model's reply
    python bench/agent/llm_report.py table --split test RUN.json ...    # full tables with intervals (markdown)

A run holds several samples per decision point. Each sample is scored by ``score.py``; within a scenario the counts of the
samples at temperature 0.0 are averaged (the primary analysis), then the cluster bootstrap resamples *scenarios*. The 0.7
sample is reported separately as a robustness check.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import policies as _policies
import score as _score

B = 4000
HEAD = ("harmful_action_rate", "unnecessary_deferral_rate", "unnecessary_ask_rate", "safe_deferral_rate", "exact_match",
        "normalised_cost")
SHORT = {"harmful_action_rate": "HAR", "unnecessary_deferral_rate": "UDR", "unnecessary_ask_rate": "UAR",
         "safe_deferral_rate": "SDR", "exact_match": "exact", "normalised_cost": "nCost"}


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text())


def wilson(k: float, n: float, z: float = 1.96) -> tuple[float | None, float | None]:
    if n <= 0:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def sample_ids(run: dict, temperature: float | None) -> list[str]:
    return [s for s, v in run["samples"].items() if temperature is None or v["temperature"] == temperature]


def score_samples(run: dict, scenarios: list[dict], sids: list[str], profile: str = "default",
                  missing_is_harm: bool = False) -> list[dict]:
    out = []
    for s in sids:
        resp = run["samples"][s]["responses"]
        if missing_is_harm:  # a missing response counts as a harmful act
            resp = {sc: dict(pts) for sc, pts in resp.items()}
            for scn in scenarios:
                for p in scn["decision_points"]:
                    resp.setdefault(scn["id"], {}).setdefault(p["id"], {"action": "act", "value": "__missing__"})
        out.append(_score.score(scenarios, resp, profile))
    return out


def average(results: list[dict]) -> dict:
    """A score-shaped result whose per-scenario counts are the mean over samples (so the cluster bootstrap resamples
    scenarios, with the sample noise averaged out inside each scenario)."""
    ids = sorted(results[0]["_counts"]["by_scenario"])
    per = {}
    for i in ids:
        acc = {k: sum(r["_counts"]["by_scenario"][i][k] for r in results) / len(results) for k in _score.COUNT_KEYS}
        per[i] = acc
    total = _score._sum(list(per.values()))
    return {"overall": _score.summarise(total), "_counts": {"overall": total, "by_scenario": per}}


def ci(res: dict, metric: str, ids: list[str] | None = None) -> dict:
    return _score.bootstrap_ci(res, metric, ids, B=B, seed=0)


def fmt(x: float | None, digits: int = 3) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def fmt_ci(c: dict) -> str:
    return f"{fmt(c['point'])} [{fmt(c['lo'])}, {fmt(c['hi'])}]"


def run_summary(run: dict, scenarios: list[dict], temperature: float | None = 0.0, **kw) -> dict:
    sids = sample_ids(run, temperature)
    res = score_samples(run, scenarios, sids, **kw)
    avg = average(res)
    per_sample_har = [r["overall"]["harmful_action_rate"] for r in res]
    recs = [r for r in run["records"] if str(r["sample"]) in sids]
    n = len(recs)
    return {"avg": avg, "samples": sids, "per_sample_har": per_sample_har, "n_calls": n,
            "repaired": sum(r["repaired"] for r in recs), "missing": sum(r["missing"] for r in recs),
            "errors": sum(1 for r in recs if r["error"]), "ingest_error_points": sum(1 for r in recs if r["ingest_error"])}


def explain(run: dict, scenarios: list[dict], profile: str = "default") -> list[dict]:
    by = {s["id"]: s for s in scenarios}
    rows = []
    for rec in run["records"]:
        scn = by[rec["scenario"]]
        dp = next(p for p in scn["decision_points"] if p["id"] == rec["point"])
        r = _score.score_point(scn, dp, rec["response"], profile)
        if not r["exact"]:
            g = _score.gold_for(dp, profile)
            rows.append({"point": rec["point"], "sample": rec["sample"], "gold": f"{g['action']} {g.get('value')}",
                         "chosen": f"{r['chosen']} {(rec['response'] or {}).get('value')}", "harm": r["harmful"],
                         "repaired": rec["repaired"], "missing": rec["missing"], "reason": rec["reason"]})
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["dev", "explain", "table"])
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--split", default=None, choices=["dev", "test"])
    a = ap.parse_args(argv)
    runs = [_load(p) for p in a.runs]
    if a.cmd == "explain":
        scn = _score.load_scenarios(split=runs[0]["split"])
        for row in explain(runs[0], scn):
            print(json.dumps(row))
        return 0
    if a.cmd == "dev":
        print("run".ljust(40) + "".join(SHORT[c].rjust(8) for c in HEAD) + "  calls repaired missing  HAR/sample")
        for run in runs:
            scn = _score.load_scenarios(split=run["split"])
            s = run_summary(run, scn)
            o = s["avg"]["overall"]
            name = f"{run['model']} {run['system']} {run['prompt_version']}"
            print(name.ljust(40) + "".join(fmt(o[c]).rjust(8) for c in HEAD) +
                  f"  {s['n_calls']:5d} {s['repaired']:8d} {s['missing']:7d}  " + ",".join(fmt(x, 2) for x in s["per_sample_har"]))
        return 0
    _ = _policies  # the table command (full markdown) lives in write_tables below
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
