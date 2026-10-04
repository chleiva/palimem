"""Scripted reference policies for RETRACT-ACT. No LLM, standard library only.

They exist to (a) validate the scorer, (b) show the metric space (harmful-action rate vs unnecessary
deferral), and (c) give the zero-cost baselines every real system must be compared with.

  oracle        the gold action (upper bound; cost 0 by construction)
  lww           naive last-write-wins: the latest value-bearing report wins; ignores withdrawals, cues,
                authority, source class, origin and valid time; unwraps attributed claims
  lww_retract   as lww, but a report targeted by any withdraw/correct report (from anyone) is dropped
  stale_plan    as lww, but a decision that executes a plan reads the belief as of plan formation
  always_abstain / always_ask   constant policies

Run `python bench/agent/policies.py` to print the reference table.
"""
from __future__ import annotations

from collections.abc import Callable

try:  # package use (bench.agent.policies) or direct use with bench/agent on sys.path
    from . import score as _score
except ImportError:  # pragma: no cover
    import score as _score

VALUE_CUES = ("assert", "change", "correct")


def _prefix(scn: dict, rid: str) -> list[dict]:
    ids = [r["id"] for r in scn["reports"]]
    return scn["reports"][: ids.index(rid) + 1]


def _stated_value(report: dict) -> str | None:
    p = report.get("proposition")
    if not p:
        return None
    if p["form"] == "belief_of":  # the classic failure: the attribution is stored as the claim
        p = p["inner"]
    return p.get("value") if p["form"] == "value" else None


def resolve(scn: dict, attr: str, entity: str, reports: list[dict], excluded: frozenset = frozenset(), _depth: int = 0) -> str | None:
    """Latest eligible stated value for (entity, attr), evaluating declared derivations recursively."""
    if _depth > 8:
        return None
    for d in scn["derivations"]:
        if d["head"] != attr:
            continue
        if d["kind"] == "copy":
            return resolve(scn, d["from"], entity, reports, excluded, _depth + 1)
        mid = resolve(scn, d["via"], entity, reports, excluded, _depth + 1)
        return None if mid is None else resolve(scn, d["lookup"], mid, reports, excluded, _depth + 1)
    for r in reversed(reports):
        if r["key"] == {"entity": entity, "attr": attr} and r["cue"] in VALUE_CUES and r["id"] not in excluded:
            v = _stated_value(r)
            if v is not None:
                return v
    return None


def _decide(scn: dict, dp: dict, upto: str, retract: bool) -> dict:
    reports = _prefix(scn, upto)
    excluded = frozenset(r["target"] for r in reports if retract and r["cue"] in ("withdraw", "correct") and r.get("target"))
    k = dp["tool"]["use_key"]
    v = resolve(scn, k["attr"], k["entity"], reports, excluded)
    if dp["kind"] == "post_hoc_review":
        return {"action": "act", "value": v}  # nothing in a recency store signals that an executed action is now suspect
    if v is None:
        return {"action": "ask" if dp["mode"] == "required" else "abstain", "value": None}
    return {"action": "act", "value": v}


def lww(scn, dp, profile="default"):
    return _decide(scn, dp, dp["after_report"], retract=False)


def lww_retract(scn, dp, profile="default"):
    return _decide(scn, dp, dp["after_report"], retract=True)


def stale_plan(scn, dp, profile="default"):
    return _decide(scn, dp, dp.get("plan_formed_after") or dp["after_report"], retract=False)


def always_abstain(scn, dp, profile="default"):
    return {"action": "abstain", "value": None}


def always_ask(scn, dp, profile="default"):
    return {"action": "ask", "value": None}


def oracle(scn, dp, profile="default"):
    g = _score.gold_for(dp, profile)
    return {"action": g["action"], "value": g.get("value")}


POLICIES: dict[str, Callable] = {"oracle": oracle, "lww": lww, "lww_retract": lww_retract, "stale_plan": stale_plan,
                                 "always_abstain": always_abstain, "always_ask": always_ask}


def run_policy(policy: Callable, scenarios: list[dict], profile: str = "default") -> dict:
    return {s["id"]: {p["id"]: policy(s, p, profile) for p in s["decision_points"]} for s in scenarios}


def reference_results(scenarios: list[dict], profile: str = "default") -> dict[str, dict]:
    return {name: _score.score(scenarios, run_policy(fn, scenarios, profile), profile) for name, fn in POLICIES.items()}


def main() -> None:
    scn = _score.load_scenarios()
    for title, subset in (("ALL (30 scenarios)", scn), ("TEST split", [s for s in scn if s["split"] == "test"])):
        res = reference_results(subset)
        print(f"\n== {title}: {sum(len(s['decision_points']) for s in subset)} decision points ==")
        print(_score.format_table({k: v["overall"] for k, v in res.items()}))
    res = reference_results(scn)
    for stratum in ("risk", "recency"):
        ids = set(_score.scenario_ids_for(scn, stratum))
        sub = [s for s in scn if s["id"] in ids]
        r = reference_results(sub)
        print(f"\n== stratum: {stratum} ({len(sub)} scenarios) ==")
        print(_score.format_table({k: v["overall"] for k, v in r.items()}))
    print("\n== by category (lww vs lww_retract vs oracle: harmful_action_rate) ==")
    cats = sorted(res["lww"]["by_category"])
    print("category".ljust(18) + "".join(n.rjust(14) for n in ("lww", "lww_retract", "stale_plan", "always_ask")))
    for c in cats:
        print(c.ljust(18) + "".join(f"{res[n]['by_category'][c]['harmful_action_rate']:.2f}".rjust(14) for n in ("lww", "lww_retract", "stale_plan", "always_ask")))


if __name__ == "__main__":
    main()
