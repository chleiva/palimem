"""Token and dollar estimator for running RETRACT-ACT with LLM agents. Makes NO API calls.

Token counts are a chars/4 heuristic over the scenario files plus fixed prompt overheads, inflated by a
margin; prices are ASSUMPTIONS (not verified against the AWS price list) and are overridable with --prices.
The budget arithmetic uses a deliberately pessimistic price ceiling. The paid runner (not written yet)
must still enforce the ledger's hard cap; this script only decides what is worth proposing.

    python bench/agent/estimate_cost.py                       # table for the pre-registered design
    python bench/agent/estimate_cost.py --budget 8 --json
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

try:
    from . import score as _score
except ImportError:  # pragma: no cover
    import score as _score

# ---- assumptions (all tunable) ------------------------------------------------------------------
AGENT_BASE = 650            # system prompt + action vocabulary + tool description, tokens
TASK_OVERHEAD = 40
TOKEN_MARGIN = 1.25         # chars/4 under-counts structured text; inflate input estimates
RETRY_FACTOR = 1.15         # one format-repair retry on ~15% of calls
# output tokens per agent call (a reasoning model spends most of them on reasoning)
AGENT_OUT = {"gpt-oss-20b": 500, "ministral-8b": 90}
# $/1M tokens (input, output). UNVERIFIED planning figures.
PRICES = {"gpt-oss-20b": (0.07, 0.30), "ministral-8b": (0.15, 0.15)}
CEILING = (0.20, 0.60)      # pessimistic ceiling used for the budget arithmetic
# memory-context tokens handed to the agent per decision point
CONTEXT = {"structured": 140, "mem0": 170, "graphiti": 230}
# ingestion LLM calls per report: (calls, input tokens per call excluding report text, output tokens per call)
INGEST = {"none": (0, 0, 0), "nl_extract": (1, 450, 130), "mem0": (2, 800, 175), "graphiti": (6, 1400, 250)}
# third-party internals are unverified: multiply their ceiling cost (prompt sizes, extra graph/vector LLM calls)
UNCERTAINTY = {"mem0": 3.0, "graphiti": 3.0}
SYSTEMS = {  # name: (context kind or "raw_log", ingestion kind)
    "raw_log": ("raw_log", "none"),            # agent LLM adjudicates from the full log (the paper's baseline shape)
    "lww_store": ("structured", "none"),
    "palimem_typed": ("structured", "none"),   # typed reports: no extraction LLM
    "palimem_nl": ("structured", "nl_extract"),
    "mem0": ("mem0", "mem0"),
    "graphiti": ("graphiti", "graphiti"),
}


def tok(text: str) -> float:
    return math.ceil(len(text) / 4) * TOKEN_MARGIN


def scenario_tokens(scn: dict, system: str, model: str) -> tuple[float, float, int]:
    """(input tokens, output tokens, llm calls) for one scenario, one system, one seed."""
    ctx_kind, ing_kind = SYSTEMS[system]
    calls, ing_in, ing_out = INGEST[ing_kind]
    tin = tout = 0.0
    n_calls = 0
    for r in scn["reports"]:
        tin += calls * (ing_in + tok(r["text"]))
        tout += calls * ing_out
        n_calls += calls
    ids = [r["id"] for r in scn["reports"]]
    for p in scn["decision_points"]:
        visible = scn["reports"][: ids.index(p["after_report"]) + 1]
        ctx = sum(tok(r["text"]) + 8 for r in visible) if ctx_kind == "raw_log" else CONTEXT[ctx_kind]
        tin += AGENT_BASE + ctx + tok(p["task"]) + TASK_OVERHEAD
        tout += AGENT_OUT[model]
        n_calls += 1
    return tin * RETRY_FACTOR, tout * RETRY_FACTOR, n_calls


def dollars(tin: float, tout: float, price: tuple[float, float]) -> float:
    return tin / 1e6 * price[0] + tout / 1e6 * price[1]


def estimate(scenarios: list[dict], prices: dict, budget: float) -> dict:
    out: dict = {"assumptions": {"agent_base": AGENT_BASE, "token_margin": TOKEN_MARGIN, "retry_factor": RETRY_FACTOR,
                                 "agent_out_tokens": AGENT_OUT, "prices_per_M": prices, "ceiling_per_M": CEILING,
                                 "context_tokens": CONTEXT, "ingest": INGEST},
                 "n_scenarios": len(scenarios), "n_decision_points": sum(len(s["decision_points"]) for s in scenarios),
                 "table": {}, "budget": budget}
    for model in AGENT_OUT:
        for system in SYSTEMS:
            tin = tout = 0.0
            calls = 0
            for s in scenarios:
                a, b, c = scenario_tokens(s, system, model)
                tin, tout, calls = tin + a, tout + b, calls + c
            n = len(scenarios)
            out["table"][f"{model}|{system}"] = {
                "model": model, "system": system, "tokens_in_per_scenario": tin / n, "tokens_out_per_scenario": tout / n,
                "llm_calls_per_scenario": calls / n, "usd_per_scenario_assumed": dollars(tin, tout, prices[model]) / n,
                "usd_per_scenario_ceiling": dollars(tin, tout, CEILING) / n}
    return out


def plan(est: dict, budget: float, reserve: float = 0.25) -> dict:
    """Max scenario x system x seed units, and the proposed pre-registered grid, at the ceiling price."""
    usable = budget * (1 - reserve)
    unit = {k: v["usd_per_scenario_ceiling"] for k, v in est["table"].items()}
    return {"usable_usd": usable, "reserve_fraction": reserve, "unit_usd_ceiling": unit}


def grid_cost(est: dict, systems: list[str], model: str, scenarios: int, seeds: int) -> tuple[float, float]:
    a = sum(est["table"][f"{model}|{s}"]["usd_per_scenario_assumed"] for s in systems) * scenarios * seeds
    c = sum(est["table"][f"{model}|{s}"]["usd_per_scenario_ceiling"] * UNCERTAINTY.get(s, 1.0) for s in systems) * scenarios * seeds
    return a, c


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenarios", default=str(_score.DEFAULT_SCENARIOS))
    ap.add_argument("--budget", type=float, default=8.0, help="USD available for the confirmatory agent-level runs")
    ap.add_argument("--reserve", type=float, default=0.25, help="fraction kept back for dry-runs, dev-split tuning and repairs")
    ap.add_argument("--prices", help="JSON {model: [usd_per_M_in, usd_per_M_out]} overriding the assumed prices")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    prices = {**PRICES, **(json.loads(Path(a.prices).read_text()) if a.prices else {})}
    scn = _score.load_scenarios(a.scenarios)
    test = [s for s in scn if s["split"] == "test"]
    est_all, est_test = estimate(scn, prices, a.budget), estimate(test, prices, a.budget)
    usable = a.budget * (1 - a.reserve)
    result = {"all": est_all, "test": est_test, "usable_usd": usable}
    unit_ceiling = {k: v["usd_per_scenario_ceiling"] for k, v in est_test["table"].items()}
    result["max_units_at_ceiling"] = {k: math.floor(usable / u) for k, u in unit_ceiling.items()}
    # the pre-registered grids; scenario counts come from the split, not from this script
    core = ["raw_log", "lww_store", "palimem_typed", "palimem_nl"]
    dev = [s for s in scn if s["split"] == "dev"]
    est_dev = estimate(dev, prices, a.budget)
    GRIDS = [  # (label, estimate to use, n scenarios, systems, model, seeds)
        ("G1 confirmatory core: test x raw_log,lww_store,palimem_typed,palimem_nl x gpt-oss-20b x 5 seeds", est_test, len(test), core, "gpt-oss-20b", 5),
        ("G2 third-party: test x mem0 x gpt-oss-20b x 5 seeds", est_test, len(test), ["mem0"], "gpt-oss-20b", 5),
        ("G3 third-party: test x graphiti x gpt-oss-20b x 3 seeds", est_test, len(test), ["graphiti"], "gpt-oss-20b", 3),
        ("G4 second model: test x core+mem0 x ministral-8b x 5 seeds", est_test, len(test), core + ["mem0"], "ministral-8b", 5),
        ("G5 tuning reserve: dev x core+mem0 x both models x 3 seeds (spent BEFORE freeze)", est_dev, len(dev), core + ["mem0"], "gpt-oss-20b", 3),
        ("G5b (same, ministral-8b)", est_dev, len(dev), core + ["mem0"], "ministral-8b", 3),
        ("G6 optional: 10x paraphrase variants of test, core x gpt-oss-20b x 3 seeds", est_test, 10 * len(test), core, "gpt-oss-20b", 3),
    ]
    grids = {label: grid_cost(e, systems, model, n, seeds) for label, e, n, systems, model, seeds in GRIDS}
    result["grids_assumed_vs_ceiling_usd"] = {k: {"assumed": v[0], "ceiling": v[1]} for k, v in grids.items()}
    if a.json:
        print(json.dumps(result, indent=2))
        return
    print(f"{len(scn)} scenarios / {est_all['n_decision_points']} decision points; test split {len(test)} scenarios / "
          f"{est_test['n_decision_points']} points. Budget ${a.budget:.2f}, usable ${usable:.2f} after a {a.reserve:.0%} reserve.")
    print("\nPer scenario, one seed, TEST split (tokens include margin and retries). usd assumed | usd at ceiling | max scenario-seed units in usable budget")
    print("model         system          in_tok  out_tok calls   $assumed  $ceiling   max_units")
    for k, v in est_test["table"].items():
        print(f"{v['model']:<13} {v['system']:<14} {v['tokens_in_per_scenario']:>7.0f} {v['tokens_out_per_scenario']:>8.0f} "
              f"{v['llm_calls_per_scenario']:>5.1f} {v['usd_per_scenario_assumed']:>10.5f} {v['usd_per_scenario_ceiling']:>9.5f} "
              f"{result['max_units_at_ceiling'][k]:>10d}")
    print("\nPre-registered grids (assumed price | ceiling price, third-party internals x3 at ceiling):")
    tot_a = tot_c = 0.0
    for k, v in grids.items():
        print(f"  {k:<92} ${v[0]:.2f} | ${v[1]:.2f}")
        tot_a += v[0]
        tot_c += v[1]
    print(f"  {'TOTAL':<92} ${tot_a:.2f} | ${tot_c:.2f}   (usable ${usable:.2f}; hard cap ${a.budget:.2f})")


if __name__ == "__main__":
    main()
