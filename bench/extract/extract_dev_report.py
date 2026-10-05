"""Score a recorded extractor run, classify its failures, and reproduce it offline from the raw cache.

    python bench/extract/extract_dev_report.py bench/extract/runs/2026-10-05 --split dev

For every ``<name>-<split>.jsonl`` + ``<name>-<split>-raw.jsonl`` pair in the run directory it

1. re-derives the predictions from the **raw model responses alone** (``ReplayTransport`` through the real
   ``LLMExtractor`` with a throw-away ledger; nothing is sent, nothing is spent) and checks that they equal the
   saved predictions, so the cached outputs are enough to reproduce every number;
2. scores them with ``extract_score.score`` (cluster bootstrap, seed 0) and checks the declared G-X thresholds
   in ``gate.json`` (**indicative** when the split is ``dev``: the thresholds were declared for the test split);
3. classifies the failures (wrong values, dropped change cues, cue confusions, invalid model outputs, spurious
   claims, injection compliance, key fragmentation);
4. writes ``results-<name>-<split>.json``.

Standard library only (plus the repo's own packages).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent.parent / "src"))
sys.path.insert(0, str(HERE))

from extract_estimate_cost import load_schema
from extract_gate_check import check, load_gate
from extract_run import run_items
from extract_score import _assign, alias_groups, load_jsonl, norm_prop, score

from palimem.costs import CostLedger, load_prices
from palimem.extract import PROMPT_VERSION, LLMExtractor, ReplayTransport

#: run-file stem -> Bedrock model id
MODELS = {
    "gpt-oss-20b": "openai.gpt-oss-20b-1:0",
    "ministral-14b": "mistral.ministral-3-14b-instruct",
    "ministral-8b": "mistral.ministral-3-8b-instruct",
}


def load_meta(pred_file: Path) -> dict[str, Any]:
    """The run's settings (prompt version, ceiling, repair), written next to the predictions by extract_run.py.
    A run directory without one (the 2026-10-05 baseline) used the frozen v1 prompt, the default ceiling, no repair."""
    meta = pred_file.with_name(pred_file.stem + ".meta.json")
    return json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}


def replay_predictions(items: list[dict[str, Any]], model: str, raw_cache: Path,
                       meta: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Predictions rebuilt from the raw responses only. Uses a scratch ledger: free, offline, deterministic."""
    meta = meta or {}
    with tempfile.TemporaryDirectory() as tmp:
        ledger = CostLedger(path=Path(tmp) / "scratch-ledger.jsonl")
        ex = LLMExtractor(
            model, ReplayTransport(raw_cache), ledger, purpose="offline re-score",
            prompt_version=meta.get("prompt_version", PROMPT_VERSION),
            max_output_tokens=meta.get("max_output_tokens"),
            max_repairs=meta.get("max_repairs", 0), repair_scope=meta.get("repair_scope", "output"))
        return run_items(ex, items)


def _core(preds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The fields the scorer reads (the saved predictions also carry usage/rejection bookkeeping)."""
    return [{k: v for k, v in p.items() if k in ("item_id", "claims", "identity_fields_seen")} for p in preds]


def _prop_str(p: dict[str, Any] | None) -> str:
    return "null" if p is None else json.dumps(p, sort_keys=True, ensure_ascii=False)


def failure_analysis(items: list[dict[str, Any]], preds: list[dict[str, Any]]) -> dict[str, Any]:
    by_pred = {p["item_id"]: p for p in preds}
    cue_confusion: Counter[str] = Counter()
    wrong_values: list[dict[str, Any]] = []
    dropped_change: list[dict[str, Any]] = []
    missing_by_cause: Counter[str] = Counter()
    missing: list[dict[str, Any]] = []
    spurious: list[dict[str, Any]] = []
    invalid_items: list[dict[str, Any]] = []
    injection: list[dict[str, Any]] = []
    for it in items:
        pred = by_pred[it["id"]]
        g = alias_groups(it.get("entity_aliases", {}))
        exps, claims = it["expected"], pred["claims"]
        pairs = _assign(claims, exps, g)
        matched_p = {i for i, _ in pairs}
        matched_e = {j for _, j in pairs}
        rejections = pred.get("rejections", [])
        model_invalid = [r for r in rejections if r != "cue_not_permitted"]
        if model_invalid:
            invalid_items.append({"item": it["id"], "category": it["category"], "reasons": sorted(set(model_invalid)),
                                  "expected_claims": len(exps), "parsed_claims": len(claims)})
        for i, j in pairs:
            p, e = claims[i], exps[j]
            if p["cue"] != e["cue"]:
                cue_confusion[f"{e['cue']}->{p['cue']}"] += 1
                if e["cue"] == "change":
                    dropped_change.append({"item": it["id"], "expected_cue": "change", "got_cue": p["cue"]})
            if e.get("proposition") is not None and norm_prop(p.get("proposition"), g) != norm_prop(e["proposition"], g):
                wrong_values.append({"item": it["id"], "category": it["category"],
                                     "expected": _prop_str(e["proposition"]), "got": _prop_str(p.get("proposition"))})
        for j, e in enumerate(exps):
            if j in matched_e:
                continue
            cause = "model output rejected by the strict parser" if model_invalid else (
                "no claim returned" if not claims else "claim returned under another entity/attribute")
            missing_by_cause[cause] += 1
            missing.append({"item": it["id"], "category": it["category"], "cue": e["cue"], "attr": e["attr"], "cause": cause})
            if e["cue"] == "change":
                dropped_change.append({"item": it["id"], "expected_cue": "change", "got_cue": None})
        for i, c in enumerate(claims):
            if i not in matched_p:
                spurious.append({"item": it["id"], "category": it["category"], "cue": c["cue"], "entity": c["entity"],
                                 "attr": c["attr"], "proposition": _prop_str(c.get("proposition"))})
        if it["category"] == "injection":
            injection.append({"item": it["id"], "identity_fields_seen": bool(pred.get("identity_fields_seen")),
                              "claims": [{"cue": c["cue"], "entity": c["entity"], "attr": c["attr"],
                                          "proposition": _prop_str(c.get("proposition"))} for c in claims],
                              "forbidden": it.get("forbidden", [])})
    return {
        "cue_confusion": dict(sorted(cue_confusion.items())),
        "wrong_values": wrong_values,
        "dropped_change_cues": dropped_change,
        "missing_claims_by_cause": dict(missing_by_cause),
        "missing_claims": missing,
        "spurious_claims": spurious,
        "invalid_model_outputs": invalid_items,
        "injection_items": injection,
    }


def run_meta(raw_cache: Path, preds: list[dict[str, Any]], model: str) -> dict[str, Any]:
    raw = [json.loads(x) for x in raw_cache.read_text(encoding="utf-8").splitlines() if x.strip()]
    price = load_prices()[model]
    in_tok = sum(r.get("input_tokens") or 0 for r in raw)
    out_tok = sum(r.get("output_tokens") or 0 for r in raw)
    return {
        "model": model, "requests": len(raw), "items": len(preds),
        "items_with_transport_error": sum(1 for p in preds if p.get("error")),
        "stop_reasons": dict(Counter(r.get("stop_reason") for r in raw)),
        "empty_responses": sum(1 for r in raw if not r["text"].strip()),
        "host_policy_rejections_cue_not_permitted": sum(1 for p in preds for r in p.get("rejections", []) if r == "cue_not_permitted"),
        "input_tokens": in_tok, "output_tokens": out_tok,
        "cost_usd": (in_tok * price.input_per_mtok + out_tok * price.output_per_mtok) / 1e6,
    }


def build_results(items: list[dict[str, Any]], preds: list[dict[str, Any]], model: str, raw_cache: Path,
                  split: str) -> dict[str, Any]:
    res = score(items, preds, bootstrap=1000, seed=0)
    gate = check(res, model, load_gate())
    return {
        "split": split,
        "gate_status": "indicative only: thresholds were declared for the TEST split" if split == "dev" else "gate",
        "run": run_meta(raw_cache, preds, model),
        "scores": res,
        "gate_check": gate,
        "failures": failure_analysis(items, preds),
    }


def process(run_dir: Path, split: str) -> dict[str, dict[str, Any]]:
    items = load_jsonl(HERE / "items" / f"{split}.jsonl")
    load_schema()
    out: dict[str, dict[str, Any]] = {}
    for name, model in MODELS.items():
        pred_file = run_dir / f"{name}-{split}.jsonl"
        raw_file = run_dir / f"{name}-{split}-raw.jsonl"
        if not pred_file.exists() or not raw_file.exists():
            continue
        saved = load_jsonl(pred_file)
        replayed = replay_predictions(items, model, raw_file, load_meta(pred_file))
        if _core(saved) != _core(replayed):
            raise SystemExit(f"{name}: predictions rebuilt from the raw cache differ from the saved predictions")
        results = build_results(items, saved, model, raw_file, split)
        meta = load_meta(pred_file)
        if meta:  # revisions record their settings; the 2026-10-05 baseline has none and stays byte-identical
            results["settings"] = meta
        (run_dir / f"results-{name}-{split}.json").write_text(
            json.dumps(results, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        out[name] = results
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("run_dir")
    ap.add_argument("--split", choices=["dev", "test"], default="dev")
    a = ap.parse_args(argv)
    res = process(Path(a.run_dir), a.split)
    for name, r in res.items():
        m = r["scores"]["metrics"]
        print(f"{name}: claim_f1={m['claim']['f1']:.3f} cue_acc={m['cue_accuracy']:.3f} wrong_value={m['wrong_value_rate']:.3f} "
              f"dropped_change={m['dropped_change_cue_rate']:.3f} abstain={m['abstention_accuracy']:.3f} "
              f"frag={m['key_fragmentation_rate']:.3f} inj={m['injection_compliance_rate']:.3f} "
              f"gate(indicative)={'PASS' if r['gate_check']['passed'] else 'FAIL'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
