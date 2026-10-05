"""RETRACT-ACT gold v1.1: the erratum overlay, and an offline re-score of every stored result under it.

The registered benchmark is frozen: the scenario files, ``score.py``, the adapters and the registered runs are never
edited. The author's adjudication of 2026-10-05 (``gold_errata.md``, mirrored in ``gold_errata.json``) corrects the gold of
one decision point (RA-026.d1: ``act london`` -> ``ask``). This module applies that correction as a *versioned overlay*:

    python bench/agent/gold_overlay.py check                     # errata json == markdown, old gold matches the registered files
    python bench/agent/gold_overlay.py materialise DIR           # write the overlaid scenario set (v1.1) to DIR
    python bench/agent/gold_overlay.py rescore [--annotations F --mapping F] [--out PREFIX]
        re-score every stored registered output offline under the registered gold and under v1.1, side by side

Nothing here calls a model or a network. Standard library only apart from the benchmark's own modules.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import re
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import policies as _policies
import score as _score

VERSION = "v1.1"
ERRATA_JSON = HERE / "gold_errata.json"
ERRATA_MD = HERE / "gold_errata.md"
RESULTS = HERE / "results"
RUNS = HERE / "runs" / "2026-10-05"
PROFILES = ("default", "authority_source", "self_update_off")
HEADLINE = ("harmful_action_rate", "unnecessary_deferral_rate", "unnecessary_ask_rate", "exact_match", "normalised_cost")
SHORT = {"harmful_action_rate": "HAR", "unnecessary_deferral_rate": "UDR", "unnecessary_ask_rate": "UAR",
         "exact_match": "exact", "normalised_cost": "nCost"}
B = 4000

# the loader as registered; captured before any patching so the overlay can always reach the registered gold
_REGISTERED_LOAD = _score.load_scenarios


class OverlayError(ValueError):
    """The overlay does not apply to the scenario set it was given (the registered gold is not what the errata expect)."""


# ------------------------------------------------------------------------------------------------- the overlay


def load_errata(path: Path | str = ERRATA_JSON) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def errata_sha256(errata: dict[str, Any] | None = None) -> str:
    """Identity of the overlay (recorded next to every v1.1 number)."""
    e = errata if errata is not None else load_errata()
    return hashlib.sha256(json.dumps(e, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _same_gold(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return a.get("action") == b.get("action") and _score._norm(a.get("value")) == _score._norm(b.get("value"))


def apply(scenarios: list[dict[str, Any]], errata: dict[str, Any] | None = None, *, strict: bool = True) -> list[dict[str, Any]]:
    """Return a deep copy of ``scenarios`` with the errata applied; the input is not modified.

    Each erratum names a decision point and the gold it expects to replace (``old``). If the scenario set carries a different
    gold, the overlay refuses (``OverlayError``): it must never be applied to a scenario set it was not written against.
    With ``strict=False`` an erratum whose scenario is absent (e.g. the other split) is skipped.
    """
    e = errata if errata is not None else load_errata()
    out = copy.deepcopy(scenarios)
    by_id = {s["id"]: s for s in out}
    for item in e["errata"]:
        sid = item["item"].split(".")[0]
        scn = by_id.get(sid)
        if scn is None:
            if strict:
                raise OverlayError(f"{item['item']}: scenario {sid} not in the scenario set")
            continue
        dp = next((p for p in scn["decision_points"] if p["id"] == item["item"]), None)
        if dp is None:
            raise OverlayError(f"{item['item']}: decision point not found")
        if not _same_gold(dp["gold"], item["old"]):
            raise OverlayError(f"{item['item']}: registered gold is {dp['gold']!r}, the erratum expects {item['old']!r}")
        # the corrected gold applies to the default profile and, because the point has no profile-specific gold, to every profile
        dp["gold"] = {"action": item["new"]["action"], "rationale": item["rationale"],
                      **({"value": item["new"]["value"]} if item["new"].get("value") is not None else {})}
        dp["gold_erratum"] = {"version": e["version"], "old": item["old"], "date": e["date"]}
    return out


def registered_scenarios(split: str | None = None) -> list[dict[str, Any]]:
    return _REGISTERED_LOAD(split=split)


def v1_1_scenarios(split: str | None = None) -> list[dict[str, Any]]:
    return apply(registered_scenarios(split), strict=False)


def materialise(dest: Path | str, src: Path | str | None = None) -> Path:
    """Write the overlaid scenario set (v1.1) as RA-*.json files in ``dest`` (the registered files are left untouched)."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    scns = apply(_REGISTERED_LOAD(src) if src else _REGISTERED_LOAD(), strict=False)
    for s in scns:
        (dest / f"{s['id']}.json").write_text(json.dumps(s, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return dest


@contextlib.contextmanager
def gold_v1_1() -> Iterator[None]:
    """Make ``score.load_scenarios`` return the v1.1 scenarios inside the block (for report tools that load by themselves).

    The registered scorer is not edited: the loader is swapped for the duration of the block and always restored.
    """
    original = _score.load_scenarios

    def patched(path: Path | str = _score.DEFAULT_SCENARIOS, split: str | None = None) -> list[dict[str, Any]]:
        return apply(original(path, split), strict=False)

    _score.load_scenarios = patched  # type: ignore[assignment]
    try:
        yield
    finally:
        _score.load_scenarios = original  # type: ignore[assignment]


# ------------------------------------------------------------------------------------------------- the markdown mirror


_CELL = re.compile(r"`([^`]*)`")


def _action_value(cell: str) -> dict[str, Any]:
    m = _CELL.search(cell)
    parts = (m.group(1) if m else cell).split()
    return {"action": parts[0], "value": parts[1] if len(parts) > 1 else None}


def parse_markdown(path: Path | str = ERRATA_MD) -> dict[str, list[dict[str, Any]]]:
    """Read the two tables of ``gold_errata.md`` (the human record) into the same shape as the JSON companion."""
    section, errata, kept = "", [], []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section = line[3:].strip().lower()
            continue
        if not line.startswith("|") or set(line.replace("|", "").strip()) <= {"-", " "}:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells[0] in ("date",) or not re.match(r"\d{4}-\d{2}-\d{2}", cells[0]):
            continue
        if section.startswith("errata"):
            errata.append({"item": cells[1], "old": _action_value(cells[2]), "new": _action_value(cells[3]), "reason": cells[4],
                           "adjudicator": cells[5]})
        elif section.startswith("adjudications"):
            kept.append({"item": cells[1], "gold": _action_value(cells[2]), "annotator": _action_value(cells[3]),
                         "decision": cells[4], "reason": cells[5], "adjudicator": cells[6]})
    return {"errata": errata, "kept": kept}


# ------------------------------------------------------------------------------------------------- offline re-scoring


def _metrics(res: dict[str, Any], ids: list[str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for m in HEADLINE:
        c = _score.bootstrap_ci(res, m, ids, B=B, seed=0)
        out[m] = {"point": c["point"], "lo": c["lo"], "hi": c["hi"]}
    return out


def _point(res: dict[str, Any], pid: str) -> dict[str, Any] | None:
    return next((p for p in res["points"] if p["id"] == pid), None)


def symbolic(split: str) -> dict[str, Any]:
    """Re-score the stored symbolic palimem runs (registered, under ``registered_product_v1``) and the scripted policies."""
    stems = {"palimem_justified": "justified", "palimem_justified_su_off": "justified_su_off", "palimem_recency": "recency",
             "palimem_lww": "lww"}
    runs = {label: json.loads((RESULTS / f"{split}-{stem}.json").read_text())["responses"]
            for label, stem in stems.items() if (RESULTS / f"{split}-{stem}.json").exists()}
    out: dict[str, Any] = {"split": split, "runs": {label: f"results/{split}-{stems[label]}.json" for label in runs}, "profiles": {}}
    for profile in PROFILES:
        golds: dict[str, Any] = {}
        for gname in ("registered", VERSION):
            scn = registered_scenarios(split) if gname == "registered" else v1_1_scenarios(split)
            ids = _score.scenario_ids_for(scn, "all")
            risk = _score.scenario_ids_for(scn, "risk")
            systems = {n: _score.score(scn, _policies.run_policy(fn, scn, profile), profile) for n, fn in _policies.POLICIES.items()}
            for label, resp in runs.items():
                systems[label] = _score.score(scn, resp, profile)
            golds[gname] = {name: {"all": _metrics(res, ids), "risk": _metrics(res, risk),
                                   "RA-026.d1": _point(res, "RA-026.d1")} for name, res in sorted(systems.items())}
        out["profiles"][profile] = golds
    return out


def llm(split: str) -> dict[str, Any]:
    """Re-score the stored LLM-in-the-loop runs (cached model outputs; no call) from their samples."""
    import llm_report as _llm

    out: dict[str, Any] = {"split": split, "runs": {}}
    files = sorted(p for p in RUNS.glob(f"{split}-*.json") if "smoke" not in p.name)
    for f in files:
        run = json.loads(f.read_text())
        if "samples" not in run or "records" not in run:
            continue
        entry: dict[str, Any] = {"file": f"runs/2026-10-05/{f.name}", "model": run.get("model"), "system": run.get("system")}
        for gname in ("registered", VERSION):
            scn = registered_scenarios(split) if gname == "registered" else v1_1_scenarios(split)
            summ = _llm.run_summary(run, scn, 0.0)
            avg = summ["avg"]
            entry[gname] = {m: {"point": c["point"], "lo": c["lo"], "hi": c["hi"]}
                            for m in HEADLINE for c in [_llm.ci(avg, m)]}
            by = {s["id"]: s for s in scn}
            recs = []
            for rec in run["records"]:
                if rec["point"] != "RA-026.d1":
                    continue
                dp = next(p for p in by[rec["scenario"]]["decision_points"] if p["id"] == rec["point"])
                r = _score.score_point(by[rec["scenario"]], dp, rec["response"], "default")
                recs.append({"sample": rec["sample"], "chosen": r["chosen"], "value": (rec["response"] or {}).get("value"),
                             "gold": r["gold"], "harmful": r["harmful"], "exact": r["exact"]})
            entry[gname]["RA-026.d1"] = recs
        out["runs"][f.stem] = entry
    return out


def changed_cells(sym: dict[str, Any], llms: dict[str, Any]) -> list[dict[str, Any]]:
    """Every headline cell whose point value differs between the registered gold and v1.1."""
    rows = []
    for split, s in sym.items():
        for profile, golds in s["profiles"].items():
            for system, reg in golds["registered"].items():
                new = golds[VERSION][system]
                for stratum in ("all", "risk"):
                    for m in HEADLINE:
                        a, b = reg[stratum][m]["point"], new[stratum][m]["point"]
                        if a != b:
                            rows.append({"kind": "symbolic", "split": split, "profile": profile, "system": system,
                                         "stratum": stratum, "metric": SHORT[m], "registered": a, VERSION: b})
    for split, s in llms.items():
        for name, e in s["runs"].items():
            for m in HEADLINE:
                a, b = e["registered"][m]["point"], e[VERSION][m]["point"]
                if a != b:
                    rows.append({"kind": "llm", "split": split, "run": name, "metric": SHORT[m], "registered": a, VERSION: b})
    return rows


def _gv(action: str, value: Any) -> str:
    return action if value in (None, "", "None") else f"{action} {value}"


def kappa_both(annotations: Path, mapping: Path, write: bool = True) -> dict[str, Any]:
    """Agreement of the stored annotation with the registered gold and with v1.1, for each gold profile.

    With ``write`` the default-profile reports are saved under ``results/gold-v1.1/``; tests pass ``write=False``.
    """
    sys.path.insert(0, str(HERE / "annotation"))
    import kappa as _kappa

    out: dict[str, Any] = {"profiles": {}}
    with tempfile.TemporaryDirectory() as td:
        v11 = materialise(Path(td) / "scenarios-v1.1")
        for profile in PROFILES:
            row: dict[str, Any] = {}
            for gname, sd in (("registered", None), (VERSION, v11)):
                tag = gname.replace(".", "_")  # `with_suffix` would read the dot of "v1.1" as a file extension
                res = _kappa.run(annotations, mapping, profile, Path(td) / f"kappa-{tag}-{profile}", scenarios_dir=sd)
                row[gname] = {"summaries": res["summaries"], "disagreements": [
                    {"item": d["item"], "scenario": d["scenario"], "decision_point": d["decision_point"], "split": d["split"],
                     "gold": _gv(d["gold_action"], d["gold_value"]), "annotator": _gv(d["ann_action"], d["ann_value"])}
                    for d in res["disagreements"]], "coverage": res["coverage"]}
                if profile == "default" and write:
                    (RESULTS / "gold-v1.1").mkdir(parents=True, exist_ok=True)
                    for ext in (".md", ".json"):
                        src = Path(td) / f"kappa-{tag}-{profile}{ext}"
                        (RESULTS / "gold-v1.1" / f"kappa-{'registered-gold' if gname == 'registered' else 'gold-v1.1'}{ext}").write_text(
                            src.read_text(encoding="utf-8"), encoding="utf-8")
            out["profiles"][profile] = row
    return out


# ------------------------------------------------------------------------------------------------- report


def _f(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.3f}"


def _cell(m: dict[str, Any]) -> str:
    return f"{_f(m['point'])} [{_f(m['lo'])}, {_f(m['hi'])}]"


def markdown(result: dict[str, Any]) -> str:
    intro = (f"Errata sha256 `{result['errata_sha256']}`. Registered gold vs {VERSION}; 95% cluster-bootstrap intervals over scenarios "
             f"({B} draws, seed 0). Only RA-026.d1 differs (a *dev* scenario), so test-split numbers cannot change.")
    L = [f"# Gold {VERSION} (erratum overlay): offline re-score of the registered results", "", intro, ""]
    for split, s in result["symbolic"].items():
        for profile in PROFILES:
            g = s["profiles"][profile]
            L += [f"## Symbolic, `{split}` split, gold profile `{profile}`", "",
                  "| System | HAR registered | HAR v1.1 | UDR registered | UDR v1.1 | exact registered | exact v1.1 | RA-026.d1 registered | RA-026.d1 v1.1 |",
                  "|---|---|---|---|---|---|---|---|---|"]
            for name in g["registered"]:
                a, b = g["registered"][name], g[VERSION][name]

                def pt(x: dict[str, Any] | None) -> str:
                    return "-" if x is None else f"{x['chosen']} (gold {x['gold']}{', harmful' if x['harmful'] else ''})"
                L.append(f"| `{name}` | {_cell(a['all']['harmful_action_rate'])} | {_cell(b['all']['harmful_action_rate'])} | "
                         f"{_cell(a['all']['unnecessary_deferral_rate'])} | {_cell(b['all']['unnecessary_deferral_rate'])} | "
                         f"{_cell(a['all']['exact_match'])} | {_cell(b['all']['exact_match'])} | {pt(a['RA-026.d1'])} | {pt(b['RA-026.d1'])} |")
            L.append("")
    for split, s in result["llm"].items():
        L += [f"## LLM-in-the-loop, `{split}` split (mean of the temperature-0.0 samples)", "",
              "| Run | HAR registered | HAR v1.1 | UDR registered | UDR v1.1 | exact registered | exact v1.1 | RA-026.d1 samples |",
              "|---|---|---|---|---|---|---|---|"]
        for name, e in s["runs"].items():
            recs = e[VERSION]["RA-026.d1"]
            txt = ", ".join(f"{r['chosen']}{'*' if r['harmful'] else ''}" for r in recs) or "-"
            L.append(f"| `{name}` | {_cell(e['registered']['harmful_action_rate'])} | {_cell(e[VERSION]['harmful_action_rate'])} | "
                     f"{_cell(e['registered']['unnecessary_deferral_rate'])} | {_cell(e[VERSION]['unnecessary_deferral_rate'])} | "
                     f"{_cell(e['registered']['exact_match'])} | {_cell(e[VERSION]['exact_match'])} | {txt} |")
        L.append("")
    L += ["`*` = harmful act under v1.1 (an `act` where the corrected gold is `ask`).", ""]
    if "kappa" in result:
        L += ["## Agreement of the annotation (model third opinion) with the gold", "",
              "| Gold profile | Set | registered: agreement / kappa | v1.1: agreement / kappa |", "|---|---|---|---|"]
        for profile, row in result["kappa"]["profiles"].items():
            for sset in ("all", "test_25", "ruling_items_RA-006_007_026"):
                a, b = row["registered"]["summaries"][sset], row[VERSION]["summaries"][sset]
                L.append(f"| `{profile}` | {sset} (n={a['n']}) | {a['agreement']:.3f} / {_f(a['kappa'])} | {b['agreement']:.3f} / {_f(b['kappa'])} |")
        L.append("")
        for gname in ("registered", VERSION):
            ds = result["kappa"]["profiles"]["default"][gname]["disagreements"]
            L.append(f"Disagreements under the {gname} gold (default profile): " + (", ".join(f"{d['decision_point']} (gold {d['gold']}, annotator {d['annotator']})" for d in ds) or "none") + ".")
        L.append("")
    L += ["## Cells whose point value changes", ""]
    ch = result["changed_cells"]
    if not ch:
        L.append("None.")
    else:
        L += ["| Kind | Split | Profile / run | System | Stratum | Metric | Registered | v1.1 |", "|---|---|---|---|---|---|---|---|"]
        for r in ch:
            L.append(f"| {r['kind']} | {r['split']} | {r.get('profile', r.get('run'))} | {r.get('system', '')} | {r.get('stratum', 'all')} | "
                     f"{r['metric']} | {_f(r['registered'])} | {_f(r[VERSION])} |")
    return "\n".join(L) + "\n"


def rescore(annotations: Path | None = None, mapping: Path | None = None) -> dict[str, Any]:
    sym = {split: symbolic(split) for split in ("dev", "test")}
    llms = {split: llm(split) for split in ("dev", "test")}
    result: dict[str, Any] = {"version": VERSION, "errata_sha256": errata_sha256(), "symbolic": sym, "llm": llms}
    if annotations and mapping and annotations.exists() and mapping.exists():
        result["kappa"] = kappa_both(annotations, mapping)
    result["changed_cells"] = changed_cells(sym, llms)
    return result


# ------------------------------------------------------------------------------------------------- CLI


def check() -> list[str]:
    problems: list[str] = []
    js, md = load_errata(), parse_markdown()
    if [(e["item"], e["old"], e["new"]) for e in js["errata"]] != [(e["item"], e["old"], e["new"]) for e in md["errata"]]:
        problems.append("errata: json and markdown disagree")
    try:
        apply(registered_scenarios(), js)
    except OverlayError as exc:
        problems.append(str(exc))
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    m = sub.add_parser("materialise")
    m.add_argument("dest", type=Path)
    r = sub.add_parser("rescore")
    r.add_argument("--annotations", type=Path, default=HERE / "annotation" / "annotations-2cb1a5b5.json")
    r.add_argument("--mapping", type=Path, default=HERE / "annotation" / "out" / "private" / "mapping.json")
    r.add_argument("--out", type=Path, default=RESULTS / "gold-v1.1" / "rescore")
    a = ap.parse_args(argv)
    if a.cmd == "check":
        probs = check()
        print("\n".join(probs) if probs else f"gold errata {VERSION}: json == markdown; applies to the registered scenarios")
        return 1 if probs else 0
    if a.cmd == "materialise":
        print(f"wrote the {VERSION} scenario set to {materialise(a.dest)}")
        return 0
    res = rescore(a.annotations, a.mapping)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.with_suffix(".json").write_text(json.dumps(res, indent=1, sort_keys=True, default=list) + "\n", encoding="utf-8")
    a.out.with_suffix(".md").write_text(markdown(res), encoding="utf-8")
    print(f"wrote {a.out.with_suffix('.md')}; {len(res['changed_cells'])} cell(s) change")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
