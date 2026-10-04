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

from palimem.costs import CostLedger
from palimem.extract import (
    BedrockConverseTransport,
    Extractor,
    LLMExtractor,
    prompt_hash,
)


def run_items(extractor: Extractor, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    schema = load_schema()
    preds = []
    for it in items:
        # predictions are the model-level claims (after strict parsing, before host policy), so the
        # model's own cue accuracy is measured; the host policy that refuses authority-bearing cues
        # by default is tested separately (tests/test_extract.py)
        res = extractor.extract(it["text"], item_ctx(it, schema))
        claims = [{k: v for k, v in c.to_dict().items() if k != "span"} for c in res.claims]
        preds.append({"item_id": it["id"], "claims": claims, "identity_fields_seen": res.identity_fields_seen})
    return preds


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--split", choices=["dev", "test"], required=True)
    ap.add_argument("--out", help="predictions JSONL (default: bench/extract/runs/<model>-<split>.jsonl)")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--allow-test-reuse", action="store_true")
    a = ap.parse_args(argv)
    items = [json.loads(x) for x in (HERE / "items" / f"{a.split}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    schema = load_schema()
    ph = prompt_hash(schema)
    runs = HERE / "runs"
    out = Path(a.out) if a.out else runs / f"{a.model.replace(':', '_')}-{a.split}-{ph[:8]}.jsonl"
    if not a.execute:
        print(f"dry run: {len(items)} items, prompt_hash={ph[:12]}…; see bench/extract/extract_estimate_cost.py for the cost model")
        return 0
    if os.environ.get("PALIMEM_ALLOW_PAID_CALLS") != "1":
        print("refusing: set PALIMEM_ALLOW_PAID_CALLS=1 to allow real model calls", file=sys.stderr)
        return 2
    if a.split == "test" and list(runs.glob(f"*-test-{ph[:8]}.jsonl")) and not a.allow_test_reuse:
        print("refusing: the test split was already run with this prompt hash (single-use)", file=sys.stderr)
        return 2
    runs.mkdir(exist_ok=True)
    ex = LLMExtractor(a.model, BedrockConverseTransport(), CostLedger(), purpose=f"G-X {a.split} {ph[:8]}")
    preds = run_items(ex, items)
    out.write_text("".join(json.dumps(p, sort_keys=True) + "\n" for p in preds), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
