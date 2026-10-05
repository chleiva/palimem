"""Run an extractor over an item split and write predictions for ``extract_score.py``.

    python bench/extract/extract_run.py --model openai.gpt-oss-20b-1:0 --split dev --execute

Without ``--execute`` it only prints the worst-case cost and exits. A real run needs *both*
``--execute`` and ``PALIMEM_ALLOW_PAID_CALLS=1``, goes through the $20 ledger (``palimem.costs``),
and records the prompt hash. The **test split is single-use per prompt hash**: a second test run with
the same hash is refused unless ``--allow-test-reuse`` is given, and the reuse is recorded in the output.
Nothing in this file is exercised against a network by the test suite (it uses ``run_items`` with a
fake extractor).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent.parent / "src"))
sys.path.insert(0, str(HERE))

from extract_estimate_cost import item_ctx, load_schema

from palimem.costs import CAP_USD, CostError, CostLedger
from palimem.extract import (
    PROMPT_VERSION,
    PROMPT_VERSIONS,
    BedrockConverseTransport,
    Extractor,
    LLMExtractor,
    RecordingTransport,
    prompt_hash,
)
from palimem.extract.llm import REPAIR_SCOPES, default_max_output_tokens


def run_items(extractor: Extractor, items: list[dict[str, Any]], *, retry_once: bool = False) -> list[dict[str, Any]]:
    """One prediction per item. With ``retry_once`` an item whose call raised (not a ledger refusal) is tried
    exactly once more; if it fails again it is recorded with ``error`` set and no claims, never silently dropped
    (the results document counts such items)."""
    schema = load_schema()
    preds = []
    for it in items:
        # predictions are the model-level claims (after strict parsing, before host policy), so the
        # model's own cue accuracy is measured; the host policy that refuses authority-bearing cues
        # by default is tested separately (tests/test_extract.py)
        res = None
        error: str | None = None
        for _attempt in range(2 if retry_once else 1):
            try:
                res = extractor.extract(it["text"], item_ctx(it, schema))
                break
            except CostError:
                raise  # a ledger refusal ends the run; it is never retried
            except Exception as exc:  # transport errors: retry once, then record the failure
                if not retry_once:
                    raise
                error = f"{type(exc).__name__}: {str(exc)[:200]}"
        if res is None:
            preds.append({"item_id": it["id"], "claims": [], "identity_fields_seen": False, "error": error})
            continue
        claims = [{k: v for k, v in c.to_dict().items() if k != "span"} for c in res.claims]
        pred: dict[str, Any] = {
            "item_id": it["id"], "claims": claims, "identity_fields_seen": res.identity_fields_seen,
        }
        if res.rejections:
            pred["rejections"] = [r.reason for r in res.rejections]
        if res.unexpected_fields:
            pred["unexpected_fields"] = list(res.unexpected_fields)
        if res.repair_triggered:
            pred["repair_triggered"] = True
        if res.repair_used:
            pred["repair_used"] = True
        if res.usage is not None:  # a fake extractor in tests may not report usage
            pred["usage"] = {"input_tokens": res.usage.input_tokens, "output_tokens": res.usage.output_tokens,
                             "cost_usd": res.usage.cost_usd}
            pred["calls"] = res.calls
        preds.append(pred)
    return preds


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--split", choices=["dev", "test"], required=True)
    ap.add_argument("--out", help="predictions JSONL (default: bench/extract/runs/<model>-<split>.jsonl)")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--allow-test-reuse", action="store_true")
    ap.add_argument("--raw", help="JSONL cache of the raw model responses (default: next to --out, '-raw.jsonl')")
    ap.add_argument("--cap-usd", type=float, default=CAP_USD,
                    help="spend cap for THIS run, applied to the shared ledger's total exposure (never above $20)")
    ap.add_argument("--retry-once", action="store_true", help="retry a failed item once, then record the error")
    ap.add_argument("--prompt-version", choices=list(PROMPT_VERSIONS), default=PROMPT_VERSION,
                    help="prompt template version (palimem-extract/1 is the frozen baseline)")
    ap.add_argument("--repair", choices=["none", *REPAIR_SCOPES], default="none",
                    help="one repair re-prompt: only on unusable output, or also on grammar-violating claims")
    ap.add_argument("--max-output-tokens", type=int, default=None, help="override the per-model/version ceiling")
    a = ap.parse_args(argv)
    items = [json.loads(x) for x in (HERE / "items" / f"{a.split}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    schema = load_schema()
    ph = prompt_hash(schema, a.prompt_version)
    runs = HERE / "runs"
    out = Path(a.out) if a.out else runs / f"{a.model.replace(':', '_')}-{a.split}-{ph[:8]}.jsonl"
    if not a.execute:
        print(f"dry run: {len(items)} items, prompt_hash={ph[:12]}…; see bench/extract/extract_estimate_cost.py for the cost model")
        return 0
    if os.environ.get("PALIMEM_ALLOW_PAID_CALLS") != "1":
        print("refusing: set PALIMEM_ALLOW_PAID_CALLS=1 to allow real model calls", file=sys.stderr)
        return 2
    if a.split == "test" and list(runs.rglob(f"*-test-{ph[:8]}*.jsonl")) and not a.allow_test_reuse:
        print("refusing: the test split was already run with this prompt hash (single-use)", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = Path(a.raw) if a.raw else out.with_name(out.stem + "-raw.jsonl")
    ledger = CostLedger(cap_usd=a.cap_usd)
    transport = RecordingTransport(BedrockConverseTransport(), raw)
    ceiling = a.max_output_tokens or default_max_output_tokens(a.model, a.prompt_version)
    ex = LLMExtractor(
        a.model, transport, ledger, max_output_tokens=ceiling, max_repairs=0 if a.repair == "none" else 1,
        repair_scope="output" if a.repair == "none" else a.repair, prompt_version=a.prompt_version,
        purpose=f"G-X {a.split} {ph[:8]}")
    preds = run_items(ex, items, retry_once=a.retry_once)
    out.write_text("".join(json.dumps(p, sort_keys=True) + "\n" for p in preds), encoding="utf-8")
    meta = {"model": a.model, "split": a.split, "prompt_version": a.prompt_version, "prompt_hash": ph,
            "max_output_tokens": ceiling, "max_repairs": ex.max_repairs, "repair_scope": ex.repair_scope}
    out.with_name(out.stem + ".meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"raw responses: {raw}; ledger: {json.dumps(ledger.summary(), sort_keys=True)}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
