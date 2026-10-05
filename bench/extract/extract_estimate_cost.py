"""Cost model for running the extractor-quality set on the Bedrock models (no API calls).

    python bench/extract/extract_estimate_cost.py [--dev-passes 5] [--repairs 0]

Prices come from src/palimem/prices.json (verified against the AWS Price List API on 2026-10-04).
Token assumptions are explicit below and printed with the result:

* input  = system prompt + user prompt for each item (built by palimem.extract.prompt), counted two
  ways: ``worst`` = the ledger's own pessimistic estimate (len/3+1), used for reservations, and
  ``expected`` = len/4.
* output = 30 tokens of JSON scaffolding + 85 tokens per expected claim (a claim with a span is about
  70-90 tokens); gpt-oss-20b also emits reasoning tokens, billed as output (assumed 600 on average).
  ``worst`` output = the per-model ceiling in palimem.extract.llm (what the ledger reserves).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent.parent / "src"))

from palimem.costs import CAP_USD, CostLedger, rough_token_count
from palimem.extract import (
    DEFAULT_ALLOWED_CUES,
    PROMPT_VERSION,
    PROMPT_VERSIONS,
    ExtractionContext,
    build_prompt,
)
from palimem.extract.llm import DEFAULT_MAX_OUTPUT_TOKENS, default_max_output_tokens
from palimem.types import Schema
from palimem.types.report import Source

MODELS = ["openai.gpt-oss-20b-1:0", "mistral.ministral-3-14b-instruct", "mistral.ministral-3-8b-instruct"]
REASONING_TOKENS = {"openai.gpt-oss-20b-1:0": 600}
BASE_OUT, PER_CLAIM_OUT = 30, 85
BUDGET_SHARE_USD = 4.0


def load_schema() -> Schema:
    raw = json.loads((HERE / "schemas.json").read_text(encoding="utf-8"))["people_v1"]
    attrs = [{"name": a["name"], "class": a["class"], "value_type": a["value_type"], "inertia": a["inertia"]}
             for a in raw["attrs"]]
    return Schema.from_dict({"version": raw["version"], "attrs": attrs})


def item_ctx(item: dict[str, Any], schema: Schema) -> ExtractionContext:
    return ExtractionContext(
        source=Source(id="bench:extract", cls="standard"), origin_group="bench:extract", actor="connector:bench",
        observed_at=datetime.fromisoformat(item["context"]["observed_at"]).replace(tzinfo=UTC),
        subject_entity=item["context"].get("subject_entity"), schema=schema, allowed_cues=DEFAULT_ALLOWED_CUES)


def estimate(items: list[dict[str, Any]], model: str, ledger: CostLedger, schema: Schema, repairs: int,
             version: str = PROMPT_VERSION) -> dict[str, Any]:
    in_worst = in_exp = out_exp = 0
    # revisions ask for all eight claim keys (nulls included): about 15 more output tokens per claim
    per_claim = PER_CLAIM_OUT if version == PROMPT_VERSION else PER_CLAIM_OUT + 15
    for it in items:
        p = build_prompt(it["text"], item_ctx(it, schema), version)
        in_worst += rough_token_count(p.system) + rough_token_count(p.user)
        in_exp += (len(p.system) + len(p.user)) // 4
        out_exp += BASE_OUT + per_claim * len(it["expected"]) + REASONING_TOKENS.get(model, 0)
    out_worst = default_max_output_tokens(model, version) * len(items)
    calls = 1 + repairs
    return {
        "items": len(items),
        "input_tokens_expected": in_exp, "input_tokens_worst": in_worst,
        "output_tokens_expected": out_exp, "output_tokens_worst": out_worst,
        "usd_expected_one_pass": ledger.estimate(model, in_exp, out_exp),
        "usd_worst_one_pass": ledger.estimate(model, in_worst * calls, out_worst * calls),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev-passes", type=int, default=5, help="prompt-iteration passes over the dev split per model")
    ap.add_argument("--test-passes", type=int, default=1, help="the test split is single-use per prompt hash")
    ap.add_argument("--repairs", type=int, default=0, choices=[0, 1])
    ap.add_argument("--prompt-version", choices=list(PROMPT_VERSIONS), default=PROMPT_VERSION)
    ap.add_argument("--dev-only", action="store_true", help="price the dev split only (no test pass)")
    a = ap.parse_args(argv)
    if a.dev_only:
        a.test_passes = 0
    schema = load_schema()
    ledger = CostLedger(path=HERE / ".estimate-ledger-unused.jsonl", cap_usd=CAP_USD)
    splits = {s: [json.loads(x) for x in (HERE / "items" / f"{s}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
              for s in ("dev", "test")}
    per_model: dict[str, Any] = {}
    tot_exp = tot_worst = 0.0
    for m in MODELS:
        dev = estimate(splits["dev"], m, ledger, schema, a.repairs, a.prompt_version)
        test = estimate(splits["test"], m, ledger, schema, a.repairs, a.prompt_version)
        exp = dev["usd_expected_one_pass"] * a.dev_passes + test["usd_expected_one_pass"] * a.test_passes
        worst = dev["usd_worst_one_pass"] * a.dev_passes + test["usd_worst_one_pass"] * a.test_passes
        per_model[m] = {"dev_one_pass": dev, "test_one_pass": test, "plan_usd_expected": exp, "plan_usd_worst": worst}
        tot_exp += exp
        tot_worst += worst
    out = {
        "assumptions": {"base_output_tokens": BASE_OUT, "per_claim_output_tokens": PER_CLAIM_OUT,
                        "reasoning_tokens": REASONING_TOKENS, "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
                        "dev_passes": a.dev_passes, "test_passes": a.test_passes, "repairs": a.repairs,
                        "prompt_version": a.prompt_version,
                        "prices_file": "src/palimem/prices.json"},
        "per_model": per_model,
        "total_usd_expected": tot_exp, "total_usd_worst_case": tot_worst,
        "g_x_budget_share_usd": BUDGET_SHARE_USD, "project_cap_usd": CAP_USD,
        "share_of_cap_worst_case": tot_worst / CAP_USD,
        "fits_in_share": tot_worst <= BUDGET_SHARE_USD,
    }
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
