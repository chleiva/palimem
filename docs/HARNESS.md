# Harness, frozen-set guard and cost ledger (lane E)

Tasks T-E1, T-E2, T-E6. The harness is the merge gate for every other lane: nothing that changes a
status, value or alternative on a frozen query may land.

## Differential harness (`harness/differential.py`)

```bash
python -m harness.differential                    # all 500 frozen Setting 1 streams, ~27 s
python -m harness.differential --stride 5         # every 5th stream (what CI runs on push/PR), ~6 s
python -m harness.differential --limit 25 --inject-bug drop-propagation   # must exit 1
python -m harness.differential --out report.json  # machine-readable report
```

For every query it compares three answers on **status, assertion and alternatives**:

| Side | What | Study code reused |
|---|---|---|
| incremental store | writes interleaved with reads | `palimpsest.core.BeliefStore` (policy `P0c`, propagation on) |
| symbolic replay | recompute from the log per query, cache cleared | `baselines.structured.LogQueryTime` |
| frozen gold | oracle answer fixed when the streams were generated | `data/setting1/s1_NNNN.gold.json` |

Any difference in store-vs-replay or store-vs-gold fails the run. Provenance is compared too, but only
counted (≈2.5% of queries differ, inside the study's 2.3–5.3%), because closing that gap is task T-B4.

**Exit codes:** `0` all agree · `1` disagreement · `2` setup error (study checkout or data missing,
checksum failure). A drifted frozen file is a setup error, not a pass.

**Baseline recorded 2026-10-04** (study commit `244de16`, Python 3.13): 500 streams, 30,272 queries,
0 store/replay and 0 store/gold disagreements, 763 provenance differences (2.5%), 26.8 s.
(The study's 40,030 figure spans more settings than Setting 1; this harness covers Setting 1 only.
Settings 2 and 3 need the cached LLM extractions and are future work.)

**Self-test.** `--inject-bug drop-propagation` runs the store with retraction propagation off (the
study's own ablation, i.e. the cross-key withdrawal bug class); `--inject-bug mutate-answer` flips one
answer per stream. Both must exit 1; CI and `tests/test_differential.py` assert this. On 25 streams the
first produced 87 disagreements and the second 25.

**Inputs.**
- Study checkout: `PALIMPSEST_STUDY_DIR` (default `~/palimpsest`), pinned in `harness/study_pin.json`
  (`chleiva/palimpsest@244de16`, the published `origin/main`). The harness warns if it is elsewhere
  and records the commit in the report. It only reads the study repo.
- Frozen data, first match wins: `--frozen-dir` / `PALIMPSEST_FROZEN_DIR`, `<study>/data/setting1`,
  `.cache/frozen/setting1`, extraction from a local copy of the Zenodo zip
  (`PALIMPSEST_DEPOSIT_ZIP`, `~/Downloads/palimpsest-v1.0.zip`), or `--fetch` (downloads ~37 MB from
  Zenodo, hash-checked).

## Frozen-set guard (`harness/frozen.py`, `harness/frozen_manifest.json`)

The design refers to a `frozen.json`. **No such file exists in the study repo.** The frozen set is the
`data/setting1/` directory of the Zenodo deposit; `frozen_manifest.json` is its pinned record: the SHA-256
of all 1,501 files (500 × stream, gold, hidden world, plus `_stats.json`). It was built from the Zenodo
zip and cross-checked against the deposit's own `MANIFEST.sha256` (1,501 of 1,501 entries agree).

```bash
python -m harness.frozen verify [--dir DIR] [--fetch]    # every file; also reports unexpected files
python -m harness.frozen build-manifest --zip Z          # only accepts the published Zenodo zip
```

The differential run verifies, before reading, every stream and gold file it will use. Altering one
byte of one gold file makes the run exit 2 (tested).

**Zenodo checksum.** The study records it in `scripts/fetch_release_data.py`:
`palimpsest-v1.0.zip` SHA-256 `686cf0bbf43f2cf7177aa84abaf5e27397b9543ae5363854545b26bcbf077dc6`
(record 23127764). The harness pins the same value. Note: `~/palimpsest/palimpsest-v1.0.zip` in the
study checkout is a later rebuild (SHA-256 `4c0fc810…`; it differs in `LICENSE`, `MANIFEST.sha256`, the
PDF, `README_DEPOSIT.md`, `docs/PLAN.md`, `paper/main.{pdf,tex}`) and is **not** the published artefact.
Its `data/setting1/` is byte-identical, but the harness refuses any zip whose hash is not the Zenodo one.

## Cost ledger (`src/palimem/costs.py`, `src/palimem/prices.json`)

Hard cap **$20.00** (`palimem.costs.CAP_USD`; a ledger may be given a lower cap, never a higher one).

```python
from palimem.costs import CostLedger
led = CostLedger()                       # ./ledger/ledger.jsonl (gitignored); override with PALIMEM_LEDGER
with led.authorize("openai.gpt-oss-20b-1:0", input_tokens=900, max_output_tokens=800, purpose="smoke") as r:
    resp = call_bedrock(...)             # your call
    r.commit(input_tokens=resp_in, output_tokens=resp_out)
```

- `authorize` refuses (`BudgetExceeded`) when spent + open reservations + this call's worst case would
  exceed the cap; it refuses (`UnknownModel`) any model not in `prices.json`; `purpose` is required.
- An unfinalised reservation (exception or crash after sending) is charged at its worst case.
- `commit` always records the real usage; if that pushes the total over the cap it raises afterwards.
- Append-only JSONL with `reserve` / `commit` / `cancel` records, `flock`-guarded. `python -m palimem.costs status`
  prints spend; `estimate --model M --input N --output N` prints a worst-case cost.
- **Prices** (USD per Mtok, Bedrock us-west-2, standard tier, **verified 2026-10-04 from the AWS Price List
  API**, service `AmazonBedrock`): gpt-oss-20b 0.07 in / 0.30 out; Ministral 3 8B 0.15 / 0.15; Ministral 3 14B
  0.20 / 0.20. Flex and batch tiers are cheaper and are not assumed. gpt-oss reasoning tokens are billed as
  output, so give `max_output_tokens` headroom.
- Rough scale: one call with 1M input and 1M output tokens on gpt-oss-20b is $0.37, so the cap is generous
  for extractor experiments; the binding constraint is third-party baselines that make many internal calls.
- The study's own ledger ($88.85 spent, `~/palimpsest/data/anthropic_ledger.jsonl`) is separate and not read.
