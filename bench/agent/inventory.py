"""Print (or splice into docs/eval/AGENT_BENCHMARK.md) the scenario inventory table.

    python bench/agent/inventory.py             # print the markdown table
    python bench/agent/inventory.py --splice    # replace the block between the INVENTORY markers in the doc

For each scenario it shows the hand-written gold action per decision point and which scripted reference
policies fail it (harm = acted when it should not have; defer = unnecessary ask/abstain/revalidate).
`*` marks a post-hoc review point. Standard library only.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from . import policies, score
except ImportError:  # pragma: no cover
    import policies
    import score

DOC = Path(__file__).resolve().parents[2] / "docs" / "eval" / "AGENT_BENCHMARK.md"
BEGIN, END = "<!-- INVENTORY:BEGIN -->", "<!-- INVENTORY:END -->"
REFS = ("lww", "lww_retract", "stale_plan")


def _verdict(points: list[dict]) -> str:
    h = sum(p["harmful"] for p in points)
    d = sum(1 for p in points if p["cost"] > 0 and not p["harmful"])
    if not h and not d:
        return "ok"
    return ", ".join(x for x in (f"harm {h}" if h else "", f"defer {d}" if d else "") if x)


def table(scenarios: list[dict] | None = None) -> str:
    scn = scenarios or score.load_scenarios()
    by_policy = {}
    for name in REFS:
        res = score.score(scn, policies.run_policy(policies.POLICIES[name], scn))
        by_policy[name] = {p["id"]: p for p in res["points"]}
    rows = ["| ID | Split | Category | Stakes | Gold per decision point | What it tests | LWW | LWW-retract | Stale-plan |",
            "|---|---|---|---|---|---|---|---|---|"]
    for s in scn:
        gold = " → ".join(p["gold"]["action"] + ("*" if p["kind"] == "post_hoc_review" else "") for p in s["decision_points"])
        verdicts = [_verdict([by_policy[n][p["id"]] for p in s["decision_points"]]) for n in REFS]
        rows.append(f"| {s['id']} | {s['split']} | {s['category']} | {s['stakes']} | {gold} | {s['title']} | " + " | ".join(verdicts) + " |")
    return "\n".join(rows)


def splice(doc: Path = DOC) -> None:
    text = doc.read_text()
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    doc.write_text(f"{head}{BEGIN}\n{table()}\n{END}{tail}")


if __name__ == "__main__":
    if "--splice" in sys.argv:
        splice()
        print(f"spliced into {DOC}")
    else:
        print(table())
