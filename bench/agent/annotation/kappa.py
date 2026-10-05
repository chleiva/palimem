"""Agreement between the author's blind annotations and the registered RETRACT-ACT gold (ruling 22).

    python bench/agent/annotation/kappa.py --annotations annotations-XXXX.json \\
        --mapping bench/agent/annotation/out/private/mapping.json
    python bench/agent/annotation/kappa.py --selftest

Reports Cohen's kappa over the action labels (act / ask / abstain / revalidate), raw agreement with a Wilson 95%
interval, a seeded bootstrap interval for kappa, a gold x annotator confusion matrix, agreement on the value where
both chose `act` or `revalidate`, overall (29 decision points), for the 25 test points, and for the RA-006 / RA-007 /
RA-026 items separately. Every disagreement is listed with the scenario narrative, both answers and an empty
`adjudication` field: a written reason is required for each, and the gold is NEVER edited silently (errata go in
bench/agent/gold_errata.md with date, item, old and new, reason and adjudicator; a draft is written for you).
Standard library only. This tool runs AFTER annotating: its report shows gold actions and rationales.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import narrative as nv
import score

ACTIONS = score.ACTIONS
RULING_ITEMS = ("RA-006", "RA-007", "RA-026")
OUT = HERE / "out"


# ------------------------------------------------------------------------------------------------- statistics


def cohen_kappa(a: Sequence[str], b: Sequence[str], labels: Sequence[str] = ACTIONS) -> float | None:
    """Cohen's kappa for two raters. None when undefined (no items, or chance agreement is 1)."""
    if len(a) != len(b):
        raise ValueError("rating lists differ in length")
    n = len(a)
    if n == 0:
        return None
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(k) / n) * (b.count(k) / n) for k in labels)
    if math.isclose(pe, 1.0):
        return None
    return (po - pe) / (1.0 - pe)


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - h) / d), min(1.0, (c + h) / d))


def bootstrap_kappa(a: Sequence[str], b: Sequence[str], n_boot: int = 2000, seed: int = 20261005) -> dict[str, Any]:
    n = len(a)
    if n == 0:
        return {"lo": None, "hi": None, "n_valid": 0, "n_boot": n_boot}
    rng = random.Random(seed)
    vals = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        k = cohen_kappa([a[i] for i in idx], [b[i] for i in idx])
        if k is not None:
            vals.append(k)
    if not vals:
        return {"lo": None, "hi": None, "n_valid": 0, "n_boot": n_boot}
    vals.sort()
    lo = vals[int(0.025 * (len(vals) - 1))]
    hi = vals[math.ceil(0.975 * (len(vals) - 1))]
    return {"lo": lo, "hi": hi, "n_valid": len(vals), "n_boot": n_boot}


def confusion(gold: Sequence[str], ann: Sequence[str], labels: Sequence[str] = ACTIONS) -> dict[str, dict[str, int]]:
    m = {g: {a: 0 for a in labels} for g in labels}
    for g, a in zip(gold, ann):
        m[g][a] += 1
    return m


def summarise(rows: list[dict[str, Any]], seed: int = 20261005) -> dict[str, Any]:
    gold = [r["gold_action"] for r in rows]
    ann = [r["ann_action"] for r in rows]
    n = len(rows)
    k_agree = sum(g == a for g, a in zip(gold, ann))
    both_value = [r for r in rows if r["gold_action"] in ("act", "revalidate") and r["ann_action"] in ("act", "revalidate")]
    v_agree = sum(score._norm(r["gold_value"]) == score._norm(r["ann_value"]) for r in both_value)
    kap = cohen_kappa(gold, ann)
    return {
        "n": n,
        "agreement": (k_agree / n) if n else None,
        "agreement_k": k_agree,
        "agreement_wilson95": wilson(k_agree, n),
        "kappa": kap,
        "kappa_note": None if kap is not None else "undefined (no items, or chance agreement is 1: one label for both raters)",
        "kappa_bootstrap95": bootstrap_kappa(gold, ann, seed=seed),
        "confusion_gold_rows_by_annotator_columns": confusion(gold, ann),
        "value_agreement": {"n_both_with_value": len(both_value), "agree": v_agree},
    }


# ------------------------------------------------------------------------------------------------- loading


def load_rows(annotations: dict[str, Any], mapping: dict[str, Any], scenarios: dict[str, dict[str, Any]], profile: str) -> tuple[list[dict[str, Any]], list[str]]:
    if annotations.get("pack_hash") != mapping.get("pack_hash"):
        raise ValueError("annotations and mapping come from different packs (pack_hash differs)")
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for oid, ref in sorted(mapping["items"].items()):
        scn = scenarios[ref["scenario"]]
        dp = next(d for d in scn["decision_points"] if d["id"] == ref["decision_point"])
        a = annotations.get("items", {}).get(oid)
        if not a or a.get("action") not in ACTIONS:
            missing.append(oid)
            continue
        g = score.gold_for(dp, profile)
        alt = {p: v for p, v in dp.get("gold_by_profile", {}).items() if p != profile}
        rows.append({
            "item": oid, "scenario": scn["id"], "decision_point": dp["id"], "split": scn["split"],
            "gold_action": g["action"], "gold_value": g.get("value"), "gold_rationale": g.get("rationale"),
            "gold_alternatives": alt,
            "ann_action": a["action"], "ann_value": a.get("value"), "ann_reason": a.get("reason"),
        })
    return rows, missing


def partition(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {
        "all": rows,
        "test_25": [r for r in rows if r["split"] == "test"],
        "ruling_items_RA-006_007_026": [r for r in rows if r["scenario"] in RULING_ITEMS],
    }


def narrative_text(scn: dict[str, Any], dp_id: str) -> str:
    it = nv.build_item(scn, dp_id)
    out = [f"  attributes: {'; '.join(it['attributes'])}"]
    out += [f"  source {s['source']} ({s['reliability']}; origin group {s['origin_group']})" for s in it["sources"]]
    out += [f"  day {e['day']} [{e['label']}] {e['text']}" for e in it["events"]]
    out += [f"  > {line}" for line in it["decision"]]
    return "\n".join(out)


def disagreements(rows: list[dict[str, Any]], scenarios: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if r["gold_action"] != r["ann_action"] or (
            r["gold_action"] in ("act", "revalidate") and score._norm(r["gold_value"]) != score._norm(r["ann_value"])
        ):
            out.append({**r, "narrative": narrative_text(scenarios[r["scenario"]], r["decision_point"]), "adjudication": ""})
    return out


# ------------------------------------------------------------------------------------------------- report


def _fmt(x: float | None, nd: int = 3) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def markdown(profile: str, coverage: dict[str, Any], summaries: dict[str, Any], diffs: list[dict[str, Any]], today: str) -> str:
    L = [f"# Second-annotation agreement ({today}, gold profile `{profile}`)", ""]
    L.append(f"Coverage: {coverage['answered']} of {coverage['expected']} decision points annotated"
             + ("" if not coverage["missing"] else f"; **missing: {', '.join(coverage['missing'])}**") + ".")
    if coverage["missing"]:
        L.append("\n**Incomplete: these numbers are provisional until every item is annotated.**")
    L += ["", "| set | n | agreement | Wilson 95% | kappa | bootstrap 95% (valid/total) | value agreement |", "|---|---|---|---|---|---|---|"]
    for name, s in summaries.items():
        w = s["agreement_wilson95"]
        b = s["kappa_bootstrap95"]
        va = s["value_agreement"]
        w_txt = "n/a" if w is None else f"[{w[0]:.3f}, {w[1]:.3f}]"
        b_txt = "n/a" if b["lo"] is None else f"[{b['lo']:.3f}, {b['hi']:.3f}]"
        agree_txt = f"{_fmt(s['agreement'])} ({s['agreement_k']}/{s['n']})"
        boot_txt = f"{b_txt} ({b['n_valid']}/{b['n_boot']})"
        val_txt = f"{va['agree']}/{va['n_both_with_value']}"
        L.append(f"| {name} | {s['n']} | {agree_txt} | {w_txt} | {_fmt(s['kappa'])} | {boot_txt} | {val_txt} |")
    notes = {n: s["kappa_note"] for n, s in summaries.items() if s["kappa_note"]}
    for n, t in notes.items():
        L.append(f"\n*{n}: kappa {t}.*")
    L.append("\nWith so few items the intervals are wide; report the interval, not only the point estimate.")
    for name, s in summaries.items():
        L += ["", f"### Confusion, {name} (rows: registered gold; columns: annotator)", "",
              "| gold \\ annotator | " + " | ".join(ACTIONS) + " |", "|---|" + "---|" * len(ACTIONS)]
        for g in ACTIONS:
            L.append(f"| {g} | " + " | ".join(str(s["confusion_gold_rows_by_annotator_columns"][g][a]) for a in ACTIONS) + " |")
    L += ["", f"## Disagreements ({len(diffs)})", "",
          "Each needs a written adjudication. Do not edit the gold silently: record any change in `bench/agent/gold_errata.md`.", ""]
    for d in diffs:
        L += [f"### {d['scenario']} / {d['decision_point']} (item {d['item']}, split {d['split']})", "", "```", d["narrative"], "```", "",
              f"- registered gold: **{d['gold_action']}**" + (f" `{d['gold_value']}`" if d["gold_value"] else "") + f". Rationale: {d['gold_rationale']}"]
        for p, g in d["gold_alternatives"].items():
            L.append(f"- gold under profile `{p}`: **{g['action']}**" + (f" `{g.get('value')}`" if g.get("value") else "") + f". {g.get('rationale', '')}")
        L += [f"- annotator: **{d['ann_action']}**" + (f" `{d['ann_value']}`" if d["ann_value"] else "") + (f". Reason: {d['ann_reason']}" if d["ann_reason"] else ""),
              "- adjudication (written reason required): ", ""]
    L += ["## Gold errata draft", "",
          "Paste into `bench/agent/gold_errata.md` only for items whose adjudication changes the gold.", "",
          "| date | item | old | new | reason | adjudicator |", "|---|---|---|---|---|---|"]
    for d in diffs:
        L.append(f"| {today} | {d['decision_point']} | {d['gold_action']} {d['gold_value'] or ''} | | | |")
    return "\n".join(L) + "\n"


def run(annotations_path: Path, mapping_path: Path, profile: str, out_prefix: Path, scenarios_dir: Path | None = None, seed: int = 20261005) -> dict[str, Any]:
    import datetime
    raw = nv.load_raw_scenarios(scenarios_dir) if scenarios_dir else nv.load_raw_scenarios()
    annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    rows, missing = load_rows(annotations, mapping, raw, profile)
    parts = partition(rows)
    summaries = {k: summarise(v, seed) for k, v in parts.items()}
    diffs = disagreements(rows, raw)
    coverage = {"expected": len(mapping["items"]), "answered": len(rows), "missing": missing}
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    result = {"profile": profile, "coverage": coverage, "summaries": summaries, "disagreements": diffs, "date": today}
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(result, indent=1, sort_keys=True, default=list), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(markdown(profile, coverage, summaries, diffs, today), encoding="utf-8")
    return result


# ------------------------------------------------------------------------------------------------- self-test


def selftest() -> int:
    ok = True

    def check(name: str, got: Any, want: Any, tol: float = 1e-9) -> None:
        nonlocal ok
        good = (got is None and want is None) or (got is not None and want is not None and abs(got - want) <= tol)
        ok &= good
        print(("PASS " if good else "FAIL ") + f"{name}: got {got!r} want {want!r}")

    # hand-computed: 50 items, both yes 20, A yes/B no 5, A no/B yes 10, both no 15
    a = ["y"] * 20 + ["y"] * 5 + ["n"] * 10 + ["n"] * 15
    b = ["y"] * 20 + ["n"] * 5 + ["y"] * 10 + ["n"] * 15
    check("kappa 2x2 (po .7, pe .5)", cohen_kappa(a, b, ("y", "n")), 0.4)
    gold = ["act", "ask", "abstain", "revalidate"] * 5
    check("perfect agreement", cohen_kappa(gold, gold), 1.0)
    shifted = gold[1:] + gold[:1]
    check("cyclic shift over 4 labels (po 0, pe .25)", cohen_kappa(gold, shifted), -1 / 3)
    three = ["act", "ask", "abstain"] * 4
    check("cyclic shift over 3 labels (po 0, pe 1/3)", cohen_kappa(three, three[1:] + three[:1], ("act", "ask", "abstain")), -0.5)
    check("one label for both raters is undefined", cohen_kappa(["act"] * 5, ["act"] * 5), None)
    check("disjoint constant labels (po 0, pe 0)", cohen_kappa(["act"] * 5, ["ask"] * 5), 0.0)
    rng = random.Random(7)
    ra = [rng.choice(ACTIONS) for _ in range(4000)]
    rb = [rng.choice(ACTIONS) for _ in range(4000)]
    k = cohen_kappa(ra, rb)
    good = k is not None and abs(k) < 0.05
    ok &= good
    print(("PASS " if good else "FAIL ") + f"independent random raters give kappa near 0: {k:.4f}")
    lo, hi = wilson(7, 10) or (0, 0)
    check("Wilson 7/10 lower", lo, 0.39679, 5e-5)
    check("Wilson 7/10 upper", hi, 0.89223, 5e-5)
    lo, hi = wilson(0, 10) or (0, 0)
    check("Wilson 0/10 lower", lo, 0.0)
    check("Wilson 0/10 upper", hi, 3.841459 / 13.841459, 1e-6)
    lo, hi = wilson(10, 10) or (0, 0)
    check("Wilson 10/10 lower", lo, 10 / 13.841459, 1e-6)
    check("Wilson 10/10 upper", hi, 1.0)
    bt = bootstrap_kappa(gold, gold, n_boot=200)
    check("bootstrap of perfect agreement is degenerate-but-valid (lo)", bt["lo"], 1.0)
    check("bootstrap of perfect agreement (hi)", bt["hi"], 1.0)
    print("SELFTEST", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--annotations", type=Path, help="the JSON file saved from the form")
    ap.add_argument("--mapping", type=Path, default=OUT / "private" / "mapping.json")
    ap.add_argument("--profile", default="default", help="gold profile to compare against (RA-007 has `authority_source` too)")
    ap.add_argument("--out", type=Path, default=OUT / "kappa-report", help="output prefix (.json and .md are written)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.annotations:
        ap.error("--annotations is required (or use --selftest)")
    res = run(a.annotations, a.mapping, a.profile, a.out)
    s = res["summaries"]["all"]
    print(f"answered {res['coverage']['answered']}/{res['coverage']['expected']}; agreement {_fmt(s['agreement'])}, kappa {_fmt(s['kappa'])}; "
          f"{len(res['disagreements'])} disagreements to adjudicate -> {a.out.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
