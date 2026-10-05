"""Score and explain palimem runs of RETRACT-ACT (reads the JSON written by palimem_system.py).

    python bench/agent/palimem_report.py explain RUN.json [--split dev|test|all] [--profile default]
        every decision point where the chosen action is not the gold action, with palimem's answer behind it
    python bench/agent/palimem_report.py table --split test RUN.json [RUN.json ...] [--out tables.json]
        the results table (overall, risk and recency strata, per category) for the given runs plus the six scripted
        reference policies, with cluster-bootstrap CIs and the paired difference to last-write-wins

Standard library only apart from the benchmark's own modules.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import policies as _policies
import score as _score

B = 4000
HEADLINE = ("harmful_action_rate", "unnecessary_deferral_rate", "unnecessary_ask_rate", "safe_deferral_rate",
            "exact_match", "normalised_cost")
SHORT = {"harmful_action_rate": "HAR", "unnecessary_deferral_rate": "UDR", "unnecessary_ask_rate": "UAR",
         "safe_deferral_rate": "SDR", "exact_match": "exact", "normalised_cost": "nCost", "ask_recall": "ask-recall",
         "gap_surfacing_rate": "gap-surf"}


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text())


def explain(run: dict, split: str, profile: str) -> list[dict]:
    scn = _score.load_scenarios(split=split)
    by_id = {s["id"]: s for s in scn}
    res = _score.score(scn, run["responses"], profile)
    traces = {(t["scenario"], t["point"]): t for t in run["traces"]}
    out = []
    for p in res["points"]:
        if p["exact"]:
            continue
        sid = p["id"].split(".")[0]
        dp = next(d for d in by_id[sid]["decision_points"] if d["id"] == p["id"])
        t = traces.get((sid, p["id"]))
        out.append({"point": p["id"], "category": p["category"], "gold": p["gold"], "chosen": p["chosen"],
                    "harmful": p["harmful"], "kind": p["kind"], "mode": dp["mode"],
                    "gold_value": _score.gold_for(dp, profile).get("value"),
                    "palimem": None if t is None else {k: t["now"][k] for k in ("kind", "value", "kernel_status", "decision", "note")},
                    "reference": None if t is None or not t.get("reference") else t["reference"],
                    "events": None if t is None else t["events"], "error": run["errors"].get(sid)})
    return out


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.3f}"


def _ci(res: dict, metric: str, ids: list[str]) -> str:
    c = _score.bootstrap_ci(res, metric, ids, B=B, seed=0)
    return f"{_fmt(c['point'])} [{_fmt(c['lo'])}, {_fmt(c['hi'])}]"


def build_tables(runs: dict[str, dict], split: str, profile: str = "default") -> dict:
    """Systems -> results for `split`. `runs` maps a system label to the loaded run JSON (palimem systems)."""
    scn = _score.load_scenarios(split=split)
    systems: dict[str, dict] = {}
    for name, fn in _policies.POLICIES.items():
        systems[name] = _score.score(scn, _policies.run_policy(fn, scn, profile), profile)
    for label, run in runs.items():
        systems[label] = _score.score(scn, run["responses"], profile)
    strata = {k: _score.scenario_ids_for(scn, k) for k in ("all", "risk", "recency")}
    out: dict = {"split": split, "profile": profile, "n_scenarios": len(scn), "n_points": sum(len(s["decision_points"]) for s in scn),
                 "strata_sizes": {k: len(v) for k, v in strata.items()}, "systems": {}}
    lww = systems["lww"]
    for name, res in systems.items():
        entry: dict = {"overall": res["overall"], "by_category": res["by_category"], "ci": {}, "vs_lww": {}}
        for stratum, ids in strata.items():
            entry["ci"][stratum] = {m: _score.bootstrap_ci(res, m, ids, B=B, seed=0) for m in HEADLINE if m != "safe_deferral_rate"}
            acc = _score._sum([res["_counts"]["by_scenario"][i] for i in ids])
            entry.setdefault("stratum", {})[stratum] = _score.summarise(acc)
        if name != "lww":
            for stratum in ("risk", "all"):
                entry["vs_lww"][stratum] = {
                    m: _score.paired_diff_ci(lww, res, m, strata[stratum], B=B, seed=0)
                    for m in ("harmful_action_rate", "unnecessary_deferral_rate", "normalised_cost")}
        out["systems"][name] = entry
    return out


def markdown(tables: dict) -> str:
    sysn = list(tables["systems"])
    lines = [(f"Split `{tables['split']}`, profile `{tables['profile']}`: {tables['n_scenarios']} scenarios, "
              f"{tables['n_points']} decision points; strata: {tables['strata_sizes']}. CIs are 95% percentile intervals "
              f"from a cluster bootstrap over scenarios ({B} draws, seed 0).")]
    for stratum in ("all", "risk", "recency"):
        lines += ["", f"**Stratum `{stratum}`** ({tables['strata_sizes'][stratum]} scenarios)", "",
                  "| System | HAR [95% CI] | UDR [95% CI] | UAR | SDR | exact | nCost [95% CI] |", "|---|---|---|---|---|---|---|"]
        for n in sysn:
            e = tables["systems"][n]
            s = e["stratum"][stratum]
            ci = e["ci"][stratum]

            def c(m: str, ci: dict = ci) -> str:
                x = ci[m]
                return f"{_fmt(x['point'])} [{_fmt(x['lo'])}, {_fmt(x['hi'])}]"
            lines.append(f"| `{n}` | {c('harmful_action_rate')} | {c('unnecessary_deferral_rate')} | {_fmt(s['unnecessary_ask_rate'])} | "
                         f"{_fmt(s['safe_deferral_rate'])} | {_fmt(s['exact_match'])} | {c('normalised_cost')} |")
    lines += ["", "**Paired difference to last-write-wins** (`lww` minus system; positive HAR difference = the system is safer), risk stratum", "",
              "| System | HAR diff [95% CI] | UDR diff [95% CI] | nCost diff [95% CI] |", "|---|---|---|---|"]
    for n in sysn:
        v = tables["systems"][n]["vs_lww"].get("risk")
        if not v:
            continue

        def d(m: str, v: dict = v) -> str:
            x = v[m]
            return f"{_fmt(x['point'])} [{_fmt(x['lo'])}, {_fmt(x['hi'])}]"
        lines.append(f"| `{n}` | {d('harmful_action_rate')} | {d('unnecessary_deferral_rate')} | {d('normalised_cost')} |")
    cats = sorted(tables["systems"]["lww"]["by_category"])
    lines += ["", "**By category** (HAR / UDR; `-` = no actable point in the category)", "",
              "| Category | " + " | ".join(f"`{n}`" for n in sysn) + " |", "|---|" + "---|" * len(sysn)]
    for cat in cats:
        row = []
        for n in sysn:
            b = tables["systems"][n]["by_category"].get(cat)
            row.append("-" if b is None else f"{_fmt(b['harmful_action_rate'])} / {_fmt(b['unnecessary_deferral_rate'])}")
        lines.append(f"| {cat} | " + " | ".join(row) + " |")
    return "\n".join(lines)


def missing_as_harm(run: dict, scn: list[dict]) -> dict:
    """Pre-registered sensitivity (ii): a missing response is scored as an act with a value no gold contains, i.e.
    as harm wherever acting is wrong and as a wrong-value act where acting is right."""
    out: dict[str, dict] = {}
    for s in scn:
        have = run["responses"].get(s["id"], {})
        out[s["id"]] = {p["id"]: have.get(p["id"]) or {"action": "act", "value": "__missing__"} for p in s["decision_points"]}
    return out


def leave_one_category_out(runs: dict[str, dict], split: str) -> dict[str, dict[str, dict]]:
    """Pre-registered sensitivity (iv): HAR and UDR with each category left out, per palimem run and for `lww`."""
    scn = _score.load_scenarios(split=split)
    cats = sorted({s["category"] for s in scn})
    systems = {"lww": _policies.run_policy(_policies.lww, scn)} | {n: r["responses"] for n, r in runs.items()}
    out: dict[str, dict[str, dict]] = {}
    for name, resp in systems.items():
        out[name] = {}
        for cat in cats:
            sub = [s for s in scn if s["category"] != cat]
            res = _score.score(sub, resp)["overall"]
            out[name][cat] = {"harmful_action_rate": res["harmful_action_rate"], "unnecessary_deferral_rate": res["unnecessary_deferral_rate"]}
    return out


def sensitivity_markdown(runs: dict[str, dict], split: str) -> str:
    scn = _score.load_scenarios(split=split)
    lines = ["**(ii) A missing response counted as harm** (the RA-012 points: negative evidence is rejected by the kernel)", "",
             "| System | missing | HAR as scored (missing = abstain) | HAR, missing = harm |", "|---|---|---|---|"]
    for n, r in runs.items():
        base = _score.score(scn, r["responses"])["overall"]
        hard = _score.score(scn, missing_as_harm(r, scn))["overall"]
        lines.append(f"| `{n}` | {int(base['missing'])} | {_fmt(base['harmful_action_rate'])} | {_fmt(hard['harmful_action_rate'])} |")
    lines += ["", "**(iii) Alternative gold profiles** (HAR / UDR / exact; each profile changes the gold of exactly one scenario)", "",
              "| System | default | `authority_source` (RA-007, dev only) | `self_update_off` (RA-023) |", "|---|---|---|---|"]
    for n, r in runs.items():
        cells = []
        for prof in ("default", "authority_source", "self_update_off"):
            o = _score.score(scn, r["responses"], prof)["overall"]
            cells.append(f"{_fmt(o['harmful_action_rate'])} / {_fmt(o['unnecessary_deferral_rate'])} / {_fmt(o['exact_match'])}")
        lines.append(f"| `{n}` | " + " | ".join(cells) + " |")
    loco = leave_one_category_out(runs, split)
    cats = sorted(next(iter(loco.values())))
    lines += ["", "**(iv) Leave one category out** (HAR; the system's value with that category removed)", "",
              "| Category left out | " + " | ".join(f"`{n}`" for n in loco) + " |", "|---|" + "---|" * len(loco)]
    for c in cats:
        lines.append(f"| {c} | " + " | ".join(_fmt(loco[n][c]["harmful_action_rate"]) for n in loco) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sn = sub.add_parser("sensitivity")
    sn.add_argument("runs", nargs="+")
    sn.add_argument("--split", default="test")
    e = sub.add_parser("explain")
    e.add_argument("run")
    e.add_argument("--split", default="dev")
    e.add_argument("--profile", default="default")
    t = sub.add_parser("table")
    t.add_argument("runs", nargs="+")
    t.add_argument("--split", default="test")
    t.add_argument("--profile", default="default")
    t.add_argument("--out")
    a = ap.parse_args(argv)
    if a.cmd == "explain":
        for row in explain(_load(a.run), a.split, a.profile):
            print(json.dumps(row))
        return 0
    if a.cmd == "sensitivity":
        loaded = {f"palimem_{(r := _load(p))['system']}": r for p in a.runs}
        print(sensitivity_markdown(loaded, a.split))
        return 0
    runs = {}
    for p in a.runs:
        r = _load(p)
        runs[f"palimem_{r['system']}"] = r
    tables = build_tables(runs, a.split, a.profile)
    print(markdown(tables))
    if a.out:
        Path(a.out).write_text(json.dumps(tables, indent=1, sort_keys=True, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
