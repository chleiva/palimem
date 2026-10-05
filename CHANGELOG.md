# Changelog

All notable changes are recorded here, following [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The package follows the versioning policy in [`docs/VERSIONING.md`](docs/VERSIONING.md). During 0.x any `0.MINOR`
may change the contract; such changes are marked **BREAKING** with a migration note.

## [Unreleased]

Nothing here is released: the only published artefact is the 0.0.1 name reservation. Everything below is installed from a
checkout. What does not exist, was not measured or was not decided is in [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md); the
numbers and their caveats are in the README (section 7).

### Added
- **`Report.change_from`** (additive, author ruling 2026-10-05): the optional previous value stated by a `change` cue, allowed only for that cue and omitted from the canonical JSON when absent (earlier reports keep their exact bytes and hash-chain commitments). The kernel and the compat converter read it; the out-of-band `change_from` mapping and the `raw_ref` carrier of the compat profile are retired as the carrier (the old carrier is still read). Schemas regenerated.
- **`NotReconstructable` answer variant** (additive, author ruling 2026-10-05): `Answer = Resolved | ResourceLimited | NotReconstructable`. A snapshot whose belief version was erased answers with the variant (reason, the key and snapshot asked for, the redacted version and log position, `current_available`; no segment, no kernel_status, no content) instead of raising `NotReconstructableError`, which remains for `explain`. Schemas, the agent renderer and the v1 compat projection (which refuses it) are updated.
- **Typed entity merges** (additive, author ruling 2026-10-05): `Power.MERGE` (granted by identity, on its own, never to an agent), the `MergeRecord` contract type (id, members, representative, reason, resolver, admission version, `reversed_by`) and the typed `MergeMarker` payload (version 2; the earlier version 1 payload is still read, so existing logs load unchanged). `Entities.records()` returns the typed records; the host call result formerly named `MergeRecord` is now `MergeOutcome`.
- **Registered-benchmark reproducibility** (`bench/agent/registered_product_v1.py`, `registered_render_v1.py`,
  `registered_rerun.py`, `current_main_column.py`): the registered RETRACT-ACT runs (symbolic and LLM-in-the-loop) correspond
  to the product behaviour of commit 034d520 and re-score offline to identical responses under a pin; a new run on current
  main differs at RA-007 (dev) and RA-006 (test) symbolically (see the dated notes in `docs/eval/`).
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
- Contract types and generated JSON Schemas (`palimem.types`, `schemas/`).
- Admission, authority and policy layers (`palimem.admission`, `palimem.policy`).
- Storage layer: backend protocol, in-memory and SQLite backends with a salted hash-chained log (`palimem.store`).
- Differential harness against the PALIMPSEST study, frozen-set guard and cost ledger (`harness/`, `palimem.costs`).
- Threat model, security policy, decision records S-01 to S-13, and the RETRACT-ACT benchmark design.
- `Memory.attributions(key)` and an `attribution_only` / `attributions` field in the agent tool API's `recall`: what a third
  party is reported to believe is read apart from the value.
- `CompletionReport.stamped` / `.skipped` (the keys a completion run wrote or left alone) and `Tombstone.requester_ref`
  with `Memory.delete(requester=...)` / `Memory.tombstone_requested_by` (the erasure requester, stored as a pseudonym
  only; older tombstones still load).

### Changed
- **Attribution safety.** A value query over attribution-only evidence no longer ends in a `commit` to a `belief_of`
  candidate: the policy asks and gives no `assertion` (`kernel_status` and the candidates are unchanged, so the `Answer`
  contract is not changed). The agent tool API says the content is unknown.
- **Product-profile authority (default, pending author confirmation).** In the `open-world` profile a `correct` that
  fails the authority check is recorded as an `allege` with no effect (design v0.3). `revise-stream-v1` is unchanged.
  New `AdmissionConfig.failed_correction_is_allege` reverses the default in one line (`False` = the paper's competing
  assertion); it changes RETRACT-ACT RA-006 (test) and RA-007 (dev), see `docs/decisions/S-02.md`.

## [0.0.1] - 2026-10-04

### Added
- Placeholder release that reserves the package name on PyPI. It contains no functionality.
