# Contributing to palimem

palimem is pre-alpha (0.0.x) and is built in the open. Read the decision records in `docs/decisions/` and `docs/TYPES.md` (the tracked work) first. Contributions that follow the tracker are the easiest to merge.

## Development setup

```bash
git clone https://github.com/chleiva/palimem && cd palimem
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # the runtime has no dependencies; dev adds pytest, jsonschema, ruff, mypy
```

Python 3.11 or newer. The core is standard library only: do not add a runtime dependency. Optional integrations (extractors, MCP) live behind extras.

## The checks every change must pass

```bash
ruff check .
mypy --strict src/palimem
python -m palimem.schemas --check     # regenerate with --write if you changed a type
pytest -q
./scripts/check_secrets.sh --all
```

Install the pre-commit secret scan once: `cp scripts/check_secrets.sh .git/hooks/pre-commit` (or a one-line hook that calls it). Never commit `.env`, tokens or keys.

## The differential gate

**The differential CI is the merge gate.** The incremental store must give the same status, value and alternatives as symbolic replay and as the frozen oracle gold on every frozen query. A change that alters any such answer does not merge, whatever else it improves.

```bash
export PALIMPSEST_STUDY_DIR=~/palimpsest         # checkout of chleiva/palimpsest at the commit in harness/study_pin.json
python -m harness.frozen verify --fetch          # fetch and verify the frozen Setting 1 set (1,501 files)
python -m harness.differential --stride 5        # bounded run (what CI runs on push); drop --stride for all 500 streams
python -m harness.differential --limit 25 --inject-bug drop-propagation   # self-test: must exit 1
```

See `docs/HARNESS.md`. The frozen files are read-only inputs: never edit them, and never change a benchmark number without a dated note (design hard constraint).

## The conformance suite

Language-neutral fixtures under `tests/conformance/` (and the trust-boundary fixtures under `tests/fixtures/trust_boundary/`) are the acceptance tests every implementation must pass. Rules:

- Expectations are written by hand with a `why` that a reviewer can check without running code. Do not derive an expected output from the code under test.
- You may add fixtures that existing behaviour already passes. Changing the expected output of a published fixture needs an RFC.

## Proposing a contract change

A contract change is anything that changes a `Report`, `Answer`, schema, status or decision value, an authority/admission/quarantine default, a semantics rule, a backend-interface operation, a policy preset or the versioning rules.

1. Open a **contract change** issue (template provided) to discuss.
2. Copy `docs/rfcs/0000-template.md` to `docs/rfcs/NNNN-short-title.md` and fill it in: motivation, the change as before/after, effect on the `revise-stream-v1` profile and frozen numbers, fixtures, migration.
3. The maintainer records the outcome in the RFC and, where it settles an ambiguity, in `docs/decisions/`.
4. The implementation PR links the RFC, updates `CHANGELOG.md` (mark **BREAKING** if it breaks 0.x consumers) and the schemas.

Before G0 freezes, any contract change also needs an explicit author line in `docs/CONTRACT_PENDING.md`. The policy is in `docs/VERSIONING.md`.

## Pull requests

- One task or issue per PR where possible. Say which gates you ran.
- Keep PRs small and tests alongside the change. A skipped test needs a reason in the PR.
- No paid LLM calls outside `palimem.costs` (the ledger enforces a hard cap). Do not add API keys or recorded model outputs that contain personal data.
- Security issues: do not open an issue or PR. Follow `SECURITY.md`.

## Licence

By contributing you agree that your code is released under the MIT licence and your documentation and data under CC BY 4.0, as stated in `LICENSE` and `LICENSE-CC-BY-4.0`.
