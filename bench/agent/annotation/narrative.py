"""Plain-language, BLIND rendering of RETRACT-ACT decision points for the second annotator.

Blindness is enforced by construction: every field in the output is picked from a whitelist of the scenario
(sources, attributes, derivations, reports, executed actions, the decision point's task, mode, kind, day and
plan fields). Nothing that carries the gold, a system output, a scenario title, slug, description, category, tag,
stakes tier, cost weight or resolver hint is read. Standard library only.

An *item* is one decision point shown with only the information available at that moment: the reports up to and
including ``after_report`` and the executed actions recorded up to that point.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"

ACTIONS = ("act", "ask", "abstain", "revalidate")

# The only scenario fields this module is allowed to read. Anything else (gold, gold_by_profile, resolvers, title,
# slug, description, category, tags, depends_on_decision, stakes, costs, split) is never touched.
SCENARIO_FIELDS = ("id", "attrs", "derivations", "sources", "reports", "executed_actions", "decision_points")
REPORT_FIELDS = ("id", "recorded_at", "key", "proposition", "cue", "source", "origin", "origin_group", "target", "valid_from")
DP_FIELDS = ("id", "after_report", "kind", "mode", "task", "valid_at", "plan_formed_after")

CLASS_GLOSS = {
    "trusted": "a source treated as authoritative",
    "standard": "an ordinary source",
    "low": "a low-reliability source",
    "quarantined": "an unverified source (not yet vouched for)",
    "agent_self": "the agent's own notes (a hypothesis, not an observation of the world)",
}
ATTR_GLOSS = {
    "single_stable": "one value; it does not normally change",
    "single_changeable": "one value at a time; it can change over time",
}


def _words(name: str) -> str:
    return name.replace("_", " ")


def whitelist_scenario(raw: dict[str, Any]) -> dict[str, Any]:
    """Copy only the whitelisted fields (explicit picks, never a delete-list)."""
    scn = {k: raw[k] for k in SCENARIO_FIELDS if k in raw}
    scn["reports"] = [{k: r[k] for k in REPORT_FIELDS if k in r} for r in raw["reports"]]
    scn["decision_points"] = [{k: d[k] for k in DP_FIELDS if k in d} for d in raw["decision_points"]]
    return scn


def _prop_phrase(entity: str, attr: str, prop: dict[str, Any] | None) -> str:
    a = _words(attr)
    if prop is None:
        return ""
    form = prop["form"]
    if form == "value":
        return f"{entity}'s {a} is {prop['value']}"
    if form == "not_value":
        return f"{entity}'s {a} is NOT {prop['value']}"
    if form == "belief_of":
        inner = prop["inner"]
        return f"{prop['holder']} believes that {_prop_phrase(entity, attr, inner)}"
    raise ValueError(f"unsupported proposition form {form!r}")


def _day(n: int) -> str:
    return f"day {n}"


def report_sentence(scn: dict[str, Any], r: dict[str, Any], number: dict[str, int]) -> str:
    src = r["source"]["id"]
    ent, attr = r["key"]["entity"], r["key"]["attr"]
    prop = r.get("proposition")
    eff = f" (effective from {_day(r['valid_from'])})" if r.get("valid_from") is not None else ""
    cue = r["cue"]
    origin = r["origin"]
    by_id = {x["id"]: x for x in scn["reports"]}
    tgt = by_id.get(r.get("target")) if r.get("target") else None
    tgt_desc = ""
    if tgt is not None:
        tgt_desc = f"report {number[tgt['id']]} (made by {tgt['source']['id']} on {_day(tgt['recorded_at'])})"

    if origin == "agent_hypothesis":
        what = _prop_phrase(ent, attr, prop)
        return f"{src}: the agent notes a hypothesis, not an observation: it suspects that {what}{eff}."
    if origin == "attributed":
        return f"{src} says that {_prop_phrase(ent, attr, prop)}{eff}."
    if cue == "assert":
        verb = "states that" if prop and prop["form"] == "not_value" else "reports that"
        return f"{src} {verb} {_prop_phrase(ent, attr, prop)}{eff}."
    if cue == "change":
        a = _words(attr)
        return f"{src} reports that {ent}'s {a} has changed to {prop['value']}{eff}."
    if cue == "correct":
        return f"{src} corrects {tgt_desc}: {_prop_phrase(ent, attr, prop)}{eff}."
    if cue == "withdraw":
        return f"{src} withdraws {tgt_desc}."
    if cue == "dispute":
        return f"{src} disputes {tgt_desc}: it states that {_prop_phrase(ent, attr, prop)}{eff}."
    raise ValueError(f"unsupported cue {cue!r}")


def source_rows(scn: dict[str, Any]) -> list[dict[str, str]]:
    rows = []
    for sid, s in scn["sources"].items():
        og = s.get("origin_group", sid)
        rows.append({
            "source": sid,
            "reliability": f"{s['class']}: {CLASS_GLOSS.get(s['class'], s['class'])}",
            "origin_group": og,
        })
    return rows


def attribute_rows(scn: dict[str, Any]) -> list[str]:
    rows = []
    derived = {d["head"]: d for d in scn.get("derivations", [])}
    for name, a in scn["attrs"].items():
        if a["class"] == "derived":
            d = derived.get(name)
            if d is None:
                rows.append(f"{_words(name)}: derived from other facts")
            elif d["kind"] == "lookup":
                rows.append(f"{_words(name)}: derived. An entity's {_words(name)} is the {_words(d['lookup'])} of that entity's {_words(d['via'])}.")
            elif d["kind"] == "copy":
                rows.append(f"{_words(name)}: derived. An entity's {_words(name)} is a copy of its {_words(d['from'])}.")
            else:
                raise ValueError(f"unsupported derivation kind {d['kind']!r}")
        else:
            rows.append(f"{_words(name)}: {ATTR_GLOSS[a['class']]}")
    # derived heads that are not in attrs (should not happen) are still described
    for head, d in derived.items():
        if head not in scn["attrs"]:
            rows.append(f"{_words(head)}: derived from other facts")
    return rows


def build_item(raw_scn: dict[str, Any], dp_id: str) -> dict[str, Any]:
    scn = whitelist_scenario(raw_scn)
    reports = scn["reports"]
    order = [r["id"] for r in reports]
    number = {rid: i + 1 for i, rid in enumerate(order)}
    dp = next(d for d in scn["decision_points"] if d["id"] == dp_id)
    last = order.index(dp["after_report"])
    shown = reports[: last + 1]
    executed = [e for e in scn.get("executed_actions", []) if order.index(e["after_report"]) <= last]

    events: list[dict[str, Any]] = []
    for r in shown:
        events.append({"day": r["recorded_at"], "label": f"Report {number[r['id']]}", "text": report_sentence(scn, r, number)})
        for e in executed:
            if e["after_report"] == r["id"]:
                events.append({"day": r["recorded_at"], "label": "Action taken", "text": f"{e['tool'].replace('_', ' ')}: {e['note']}"})

    today = shown[-1]["recorded_at"]
    lines = [f"Today is {_day(today)}.", f"Task ({dp['mode']}): {dp['task']}"]
    if dp.get("valid_at") is not None:
        lines.append(f"The task concerns {_day(dp['valid_at'])}.")
    if dp["kind"] == "post_hoc_review":
        lines.append("An action has already been taken (see the timeline). You are asked to review it.")
    elif dp.get("plan_formed_after"):
        pf = dp["plan_formed_after"]
        lines.append(f"You formed a plan for this task earlier, right after report {number[pf]} ({_day(reports[order.index(pf)]['recorded_at'])}).")
    return {
        "attributes": attribute_rows(scn),
        "sources": source_rows(scn),
        "events": events,
        "decision": lines,
        "review": dp["kind"] == "post_hoc_review",
        "optional": dp["mode"] == "optional",
        "actions": list(ACTIONS),
    }


def load_raw_scenarios(path: Path = SCENARIOS) -> dict[str, dict[str, Any]]:
    out = {}
    for p in sorted(Path(path).glob("RA-*.json")):
        s = json.loads(p.read_text(encoding="utf-8"))
        out[s["id"]] = s
    return out


def select_items(raw: dict[str, dict[str, Any]]) -> list[tuple[str, str]]:
    """(scenario id, decision point id) for every test decision point plus all points of RA-006, RA-007, RA-026."""
    keep: list[tuple[str, str]] = []
    for sid, s in raw.items():
        if s["split"] == "test" or sid in ("RA-006", "RA-007", "RA-026"):
            keep += [(sid, d["id"]) for d in s["decision_points"]]
    return keep


def opaque_id(seed: int, sid: str, dp_id: str) -> str:
    return "I-" + hashlib.sha256(f"{seed}|{sid}|{dp_id}".encode()).hexdigest()[:6]
