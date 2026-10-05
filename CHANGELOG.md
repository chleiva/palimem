# Changelog

All notable changes are recorded here, following [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The package follows the versioning policy in [`docs/VERSIONING.md`](docs/VERSIONING.md). During 0.x any `0.MINOR`
may change the contract; such changes are marked **BREAKING** with a migration note.

## [Unreleased]

Nothing here is released: the only published artefact is the 0.0.1 name reservation. Everything below is installed from a
checkout. What does not exist, was not measured or was not decided is in [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md); the
numbers and their caveats are in the README (section 7).

### Added
- **Contract**: `palimem.types` (frozen, validated dataclasses with canonical JSON) and generated JSON Schemas in `schemas/`,
  drift-checked in CI. Decision records S-01 to S-13 (all decided) and the six contract changes that needed an explicit
  author decision ([`docs/decisions/`](docs/decisions/), [`docs/CONTRACT_PENDING.md`](docs/CONTRACT_PENDING.md)).
- **Admission, authority and policy** (`palimem.admission`, `palimem.policy`): outcomes `admissible`, `quarantined` and
  `excluded` with a reason and an admission version; derived confirmation; principal kinds and a typed authority grant table;
  incremental admission with the whole-log evaluation kept as the audit oracle; policy presets `justified`, `recency` and `lww`.
- **Storage** (`palimem.store`): backend protocol, in-memory and SQLite backends, a salted hash-chained evidence log with
  `verify_log`, one transaction per append with idempotency keys, the generation barrier with durable completion jobs, a
  notification outbox with `subscribe`, versioned inputs, erasure with dependency repair and a tombstone that keeps the chain,
  JSONL export and import, crash tests.
- **Kernel** (`palimem.kernel`): enumeration kernel with a per-key environment budget, rule engine for derived keys, static
  exactness check, per-candidate subset-minimal supports, `explain`. A faster **candidate** kernel (`palimem.kernel.fast`) is
  built and off by default.
- **Pipeline** (`palimem.engine`, `palimem.memory`) and the `revise-stream-v1` compatibility adapter (`palimem.compat`).
- **Public API**: `from palimem import Memory` (`observe`, `ask`, `withdraw`, `explain`, `find`, `subscribe`, `delete`,
  `verify`, `declare`, `agent_session`) with a zero-config profile; the **agent tool API** (`palimem.agent`: `remember`,
  `recall`, `retract`, `explain`, and `dispute` only for a granted principal); an **MCP server** over stdio (agent tools only,
  `--read-only`, no network listener); the **`palimem` CLI** (`inspect`, `explain`, `diff`, `export`, `import`, `verify`, `mcp`).
- **Extraction** (`palimem.extract`): extractor protocol, a no-LLM passthrough, an LLM extractor over Bedrock and
  OpenAI-compatible transports with no identity fields in its output grammar, and the cost ledger with a hard cap
  (`palimem.costs`).
- **Verification**: differential harness against the study's frozen Setting 1 data with a checksum guard (30,272 queries, 0
  disagreements with the gold on status, value and alternatives, and on provenance through the compat projection); kernel and
  pipeline differentials; a 90-fixture implementation-neutral conformance suite with a ratchet; 20 trust-boundary fixtures;
  the RETRACT-ACT agent-level benchmark (design, 30 scenarios, deterministic scorer, first symbolic run); the
  extractor-quality set and G-X gate (one gate run, which failed); a performance target declaration and benchmark suite; a
  budget cross-check against the brute-force oracle.
- **Documentation**: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md), the docs index,
  threat model, `SECURITY.md`, versioning policy, and a test that fails on a broken relative link.
- **Workflows**: `ci` (tests on 3.11 to 3.13, differential gates), `security` (pip-audit, CodeQL), `release` (Trusted
  Publishing on a published release; manual runs go to TestPyPI only), `perf` (nightly, never blocking), `fast-kernel`.

### Changed
- **Behaviour**: the default per-key environment budget is **12** (was 7), after a cross-check against the brute-force
  oracle on 1,050 fresh streams ([`docs/BUDGET_CROSSCHECK.md`](docs/BUDGET_CROSSCHECK.md)). Keys with 8 to 12 reports are now
  answered where they previously answered `ResourceLimited(environment_budget)`; pass `budget=` to keep the old limit.
- Packaging: explicit sdist excludes (a local frozen-data cache was being included), Python 3.11 to 3.13 classifiers, project
  URLs.

### Removed
- The `mcp` and `anthropic` extras that the 0.0.1 skeleton declared: no code imports either (the MCP server is a
  standard-library JSON-RPC loop and no Anthropic transport exists). The `bedrock` and `openai-compat` extras remain.

### Known limitations
- The extractor gate failed on one criterion for the only model run on the test split; negative evidence, open-world rule
  exceptions and authorised-dispute semantics are not implemented; four of seven declared performance targets are missed at
  the sizes reached (none at the 10^5 reference size). See [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md).

## [0.0.1] - 2026-10-04

### Added
- Placeholder release that reserves the package name on PyPI. It contains no functionality.
