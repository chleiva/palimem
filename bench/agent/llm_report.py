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
    split = a.split or runs[0]["split"]
    md, data = tables(split, runs)
    out = HERE / "results" / f"llm-tables-{split}"
    out.with_suffix(".md").write_text(md)
    out.with_suffix(".json").write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    print(md)
    return 0


def _ids(scns: list[dict], stratum: str) -> list[str]:
    return _score.scenario_ids_for(scns, stratum)


def _sub(avg: dict, ids: list[str]) -> dict:
    acc = _score._sum([avg["_counts"]["by_scenario"][i] for i in ids])
    return _score.summarise(acc)


def _cell(avg: dict, metric: str, ids: list[str]) -> str:
    return fmt_ci(ci(avg, metric, ids))


def tables(split: str, runs: list[dict]) -> tuple[str, dict]:
    """Markdown tables for the primary analysis (mean of the temperature-0.0 samples) plus sensitivities."""
    scns = _score.load_scenarios(split=split)
    by_model: dict[str, dict[str, dict]] = {}
    for r in runs:
        by_model.setdefault(r["model"], {})[r["system"]] = r
    sysnames = ["llm+lww", "llm+raw_log", "llm+palimem"]
    data: dict = {"split": split, "n_scenarios": len(scns), "points": sum(len(s["decision_points"]) for s in scns)}
    md: list[str] = []
    risk, rec = _ids(scns, "risk"), _ids(scns, "recency")
    allids = _ids(scns, "all")
    refs = _policies.reference_results(scns)
    sym = {}
    for name, f in (("palimem_justified", "test-justified.json"), ("palimem_recency", "test-recency.json"),
                    ("palimem_lww", "test-lww.json")):
        path = HERE / "results" / f
        if path.exists() and split == "test":
            sym[name] = _score.score(scns, json.loads(path.read_text())["responses"])
    for model, systems in sorted(by_model.items()):
        md.append(f"### Model `{model}`: primary analysis, `{split}` split, mean of the temperature-0.0 samples")
        avgs: dict[str, dict] = {}
        summaries: dict[str, dict] = {}
        for s in sysnames:
            if s in systems:
                summaries[s] = run_summary(systems[s], scns, 0.0)
                avgs[s] = summaries[s]["avg"]
        for stratum, ids in (("all", allids), ("risk", risk), ("recency", rec)):
            md += ["", f"**Stratum `{stratum}`** ({len(ids)} scenarios; 95% cluster-bootstrap intervals over scenarios, {B} draws, seed 0)", "",
                   "| System | HAR [95% CI] | UDR [95% CI] | UAR | SDR | exact | nCost [95% CI] |", "|---|---|---|---|---|---|---|"]
            for s, avg in avgs.items():
                o = _sub(avg, ids)
                md.append(f"| `{s}` | {_cell(avg, 'harmful_action_rate', ids)} | {_cell(avg, 'unnecessary_deferral_rate', ids)} | "
                          f"{fmt(o['unnecessary_ask_rate'])} | {fmt(o['safe_deferral_rate'])} | {fmt(o['exact_match'])} | "
                          f"{_cell(avg, 'normalised_cost', ids)} |")
            if stratum == "all":
                for name, res in {**{k: refs[k] for k in ("lww", "lww_retract", "always_ask", "oracle")}, **sym}.items():
                    o = _score.summarise(_score._sum(list(res["_counts"]["by_scenario"].values())))
                    md.append(f"| _{name} (no LLM, for orientation)_ | {fmt(o['harmful_action_rate'])} | {fmt(o['unnecessary_deferral_rate'])} | "
                              f"{fmt(o['unnecessary_ask_rate'])} | {fmt(o['safe_deferral_rate'])} | {fmt(o['exact_match'])} | {fmt(o['normalised_cost'])} |")
        md += ["", "**Paired difference on the `risk` stratum** (positive HAR difference = the second system is safer; 95% cluster bootstrap)", "",
               "| Contrast | HAR diff [95% CI] | UDR diff [95% CI] | nCost diff [95% CI] |", "|---|---|---|---|"]
        diffs: dict[str, dict] = {}
        for a_, b_ in (("llm+lww", "llm+palimem"), ("llm+raw_log", "llm+palimem"), ("llm+lww", "llm+raw_log")):
            if a_ in avgs and b_ in avgs:
                d = {m: _score.paired_diff_ci(avgs[a_], avgs[b_], m, risk, B=B, seed=0) for m in
                     ("harmful_action_rate", "unnecessary_deferral_rate", "normalised_cost")}
                diffs[f"{a_} - {b_}"] = d
                md.append(f"| `{a_}` minus `{b_}` | " + " | ".join(fmt_ci(d[m]) for m in d) + " |")
        md += ["", "**Scenario-level view, `risk` stratum** (clustered: how many scenarios contain a harmful act in at least one temperature-0.0 sample)", "",
               "| System | scenarios with a harmful act | share [Wilson 95%] |", "|---|---|---|"]
        for s, run in systems.items():
            if s not in avgs:
                continue
            per = avgs[s]["_counts"]["by_scenario"]
            k = sum(1 for i in risk if per[i]["harmful"] > 0)
            lo, hi = wilson(k, len(risk))
            md.append(f"| `{s}` | {k} of {len(risk)} | {fmt(k / len(risk))} [{fmt(lo)}, {fmt(hi)}] |")
        md += ["", "**By category** (HAR / UDR, mean of samples; `-` = no actable point)", "",
               "| Category | " + " | ".join(f"`{s}`" for s in avgs) + " |", "|---|" + "---|" * len(avgs)]
        cats = sorted({s["category"] for s in scns})
        for c in cats:
            ids = [s["id"] for s in scns if s["category"] == c]
            row = []
            for avg in avgs.values():
                o = _sub(avg, ids)
                row.append(f"{fmt(o['harmful_action_rate'], 2)} / {fmt(o['unnecessary_deferral_rate'], 2) if o['n_actable'] else '-'}")
            md.append(f"| {c} ({len(ids)}) | " + " | ".join(row) + " |")
        md += ["", "**Compliance, tokens and cost** (all four samples; a reply still unusable after one repair is a missing response)", "",
               "| System | calls | repaired | missing | call errors | input tok | output tok | live cost $ | $ per scenario-sample |", "|---|---|---|---|---|---|---|---|---|"]
        for s, run in systems.items():
            recs = run["records"]
            ns = len(scns) * len(run["samples"])
            md.append(f"| `{s}` | {len(recs)} | {sum(r['repaired'] for r in recs)} ({sum(r['repaired'] for r in recs) / len(recs):.1%}) | "
                      f"{sum(r['missing'] for r in recs)} ({sum(r['missing'] for r in recs) / len(recs):.1%}) | "
                      f"{sum(1 for r in recs if r['error'])} | {run['calls']['input_tokens']} | {run['calls']['output_tokens']} | "
                      f"{run['calls']['cost_usd']:.4f} | {run['calls']['cost_usd'] / ns:.5f} |")
        md += ["", "**Sensitivity** (HAR, `all` stratum)", "",
               "| System | primary | missing counted as harm | without RA-012 (palimem ingest error) | the 0.7 sample alone | per-sample HAR at 0.0 |", "|---|---|---|---|---|---|"]
        sens: dict[str, dict] = {}
        for s, run in systems.items():
            if s not in avgs:
                continue
            prim = avgs[s]["overall"]["harmful_action_rate"]
            mh = average(score_samples(run, scns, sample_ids(run, 0.0), missing_is_harm=True))["overall"]["harmful_action_rate"]
            keep = [i for i in allids if i != "RA-012"]
            no12 = _sub(avgs[s], keep)["harmful_action_rate"]
            hot = sample_ids(run, 0.7)
            h07 = average(score_samples(run, scns, hot))["overall"]["harmful_action_rate"] if hot else None
            sens[s] = {"primary": prim, "missing_is_harm": mh, "without_ra012": no12, "sample_0.7": h07}
            md.append(f"| `{s}` | {fmt(prim)} | {fmt(mh)} | {fmt(no12)} | {fmt(h07)} | {', '.join(fmt(x, 2) for x in summaries[s]['per_sample_har'])} |")
        data[model] = {"diffs": diffs, "sensitivity": sens,
                       "summary": {s: {k: v for k, v in summaries[s]["avg"]["overall"].items()} for s in summaries}}
        md.append("")
    return "\n".join(md), data


if __name__ == "__main__":
    raise SystemExit(main())
