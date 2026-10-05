# Conformance fixtures

Language-neutral JSON, so that other-language ports and other backends can prove contract
conformance without importing any palimem code. This directory is the home of every fixture suite.

| Directory | What it specifies | Runner | Status |
|---|---|---|---|
| `trust_boundary/` | The host/agent API split of `docs/API_TRUST_BOUNDARY.md` (tb-01 … tb-22): who may set `source`/`origin`/`actor`, agent retraction limits, dispute grants, caller-supplied chain fields ignored | none yet (lands with the facade, T-F2). `tests/test_trust_boundary_fixtures.py` only checks they are well-formed | specification |
| `../conformance/fixtures/` | The independent acceptance suite of design v0.3 (22 rows), the decision-record fixtures (S-04, S-06, S-10, S-12), the behavioural SEC-xx fixtures and their index | `python -m tests.conformance.runner` (format and runner: `docs/CONFORMANCE.md`); the runner also loads `trust_boundary/` | written (T-A5); runs against a `ReferenceStub` until an implementation exists |
| `../../schemas/examples/` | One canonical example per contract type, validated against `schemas/*.schema.json` and round-tripped by the tests | `tests/test_types_roundtrip.py` | in force |

## Trust-boundary fixture format

Documented in full in [`trust_boundary/README.md`](trust_boundary/README.md). In short, each file is one
scenario: a `setup` (profile, schema, connectors, authority rules, session, host event ledger, prior log,
`now`), a list of `steps` (`tier` `agent` or `host`, a `call`, `args`), and an `expect` block of
**subset matchers** (`$any`, `$absent`, `$not:`, `$eq:`, `$gt:`, `$after:`) over step results, log rows
appended, audit rows appended and post-hoc queries. `"ref"` names a prior log report and `"$r1"` refers to
its id.

Fixtures are data about the **contract**, not about one implementation: they use the field names of
`palimem.types` (`docs/TYPES.md`). Where a fixture's authority rules appear they are `AuthorityRule`
records (`who`, `may`, `on`, `targets`, `over_origins`); principal ids are typed (`agent:planner`).

## Adding a fixture

1. Name it `<suite>-NN-slug.json`; `id` must equal the file stem.
2. List every spec decision the expectation rests on in `spec_dependencies` and tag `covers` with rule or
   decision ids, so a later decision change finds the fixtures it affects.
3. Keep expectations at the contract level (answers, log and audit rows). Do not assert storage layout.
4. A fixture that needs an optional backend capability (for example `verify_log`) lists it in `requires`
   and is skipped by backends without it; a backend without the hash chain is still conformant.
5. Run `pytest tests/test_trust_boundary_fixtures.py` (structure) and, once a runner exists, the runner.
