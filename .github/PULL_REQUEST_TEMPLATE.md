## What and why

<!-- One paragraph. Link the issue, and any decision record or RFC. -->

## Gate checklist

- [ ] `ruff check .` clean
- [ ] `mypy --strict src/palimem` clean
- [ ] `python -m palimem.schemas --check` clean (regenerate with `--write` if types changed)
- [ ] `pytest -q` green
- [ ] **Differential gate**: the `harness` CI job is green (store vs replay vs frozen gold). If I touched kernel, store or admission code I also ran `python -m harness.differential` locally
- [ ] Conformance fixtures: no expected output changed, or the change has an RFC and a dated note
- [ ] No secrets, tokens, `.env` files or personal data added (`./scripts/check_secrets.sh`)
- [ ] No paid LLM call added without going through `palimem.costs` (ledger and cap)

## Contract impact

- [ ] None
- [ ] Changes `Report`, `Answer`, a schema, a status or decision value, an authority/admission/quarantine default, or a profile. RFC linked, and `CHANGELOG.md` marks it **BREAKING** if it breaks 0.x consumers (see `docs/VERSIONING.md`)

## Tests

<!-- What you added or changed, and what you ran. If a test is skipped, say why. -->
