# Documentation index

Every document in this directory, with its purpose. Start with [`ARCHITECTURE.md`](ARCHITECTURE.md) for the shape of the
system and [`LIMITATIONS.md`](LIMITATIONS.md) for what it does not do. The project overview is the repository
[README](../README.md). A test (`tests/test_docs_links.py`) fails if a relative link breaks or a document is missing from
this page.

## Using palimem

| Document | Purpose |
|---|---|
| [`AGENT_GUIDE.md`](AGENT_GUIDE.md) | how an LLM should read an `Answer` (`established`, `unresolved`, `unknown`, `single_origin`, `resource_limited`), the agent tools, the MCP server and the CLI |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | components, write and read paths, the three API tiers, trust boundaries and where each gate lives |
| [`LIMITATIONS.md`](LIMITATIONS.md) | what is not built, not measured or not decided, each with its evidence |

## Contract and semantics

| Document | Purpose |
|---|---|
| [`TYPES.md`](TYPES.md) | the executable contract: records, canonical JSON, generated JSON Schemas, the decisions applied |
| [`VERSIONING.md`](VERSIONING.md) | what counts as a breaking change, the 0.x policy, deprecation, the RFC process |
| [`rfcs/0000-template.md`](rfcs/0000-template.md) | template for a contract-change proposal |
| [`CONTRACT_PENDING.md`](CONTRACT_PENDING.md) | the six contract changes that needed an explicit author decision, and their outcomes |
| [`decisions/README.md`](decisions/README.md) | index of decision records S-01 to S-13 with their status; the records themselves are listed below |
| [`decisions/S-01.md`](decisions/S-01.md) | `confirm` cue versus derived confirmation |
| [`decisions/S-02.md`](decisions/S-02.md) | authority: origin versus source, and the study's retraction rules |
| [`decisions/S-03.md`](decisions/S-03.md) | the study's `blocked` class versus `quarantined`, and the `excluded` outcome |
| [`decisions/S-04.md`](decisions/S-04.md) | one status enum and the study's per-slot-type closed-world conventions |
| [`decisions/S-05.md`](decisions/S-05.md) | the belief axis: log sequence number or timestamp |
| [`decisions/S-06.md`](decisions/S-06.md) | behaviour above the environment budget, and the default budget |
| [`decisions/S-07.md`](decisions/S-07.md) | principal kinds and the authority grant table |
| [`decisions/S-08.md`](decisions/S-08.md) | inertia as a per-attribute flag |
| [`decisions/S-09.md`](decisions/S-09.md) | partial dates, `valid_to` and negative evidence (staged) |
| [`decisions/S-10.md`](decisions/S-10.md) | rule exceptions in an open world |
| [`decisions/S-11.md`](decisions/S-11.md) | attributed reports and nesting depth |
| [`decisions/S-12.md`](decisions/S-12.md) | `explain` depth and the provenance contract |
| [`decisions/S-13.md`](decisions/S-13.md) | tombstone contents and the preserved hash chain |
| [`API_TRUST_BOUNDARY.md`](API_TRUST_BOUNDARY.md) | the host API versus the agent tool API: who binds source, origin and actor, and what an agent may do |
| [`THREAT_MODEL.md`](THREAT_MODEL.md) | assets, trust boundaries, adversaries, 40 threats (T-01 to T-40) with mitigation status, fixture IDs SEC-01 to SEC-44, the log hash-chain evaluation, gate G-S criteria |

## Implementation

| Document | Purpose |
|---|---|
| [`KERNEL.md`](KERNEL.md) | the enumeration kernel, status ladder per slot type, provenance and its parity with the oracle, contract gaps |
| [`FAST_KERNEL.md`](FAST_KERNEL.md) | the candidate fast kernel: class covered, parity, speed, and the open promotion criteria |
| [`BUDGET_CROSSCHECK.md`](BUDGET_CROSSCHECK.md) | the evidence for raising the default environment budget from 7 to 12, and its limits |
| [`research/R41_MEMO.md`](research/R41_MEMO.md) | the R4.1 spike: why enumerating interpretations is inherently exponential and what is tractable |
| [`STORAGE.md`](STORAGE.md) | backends, tables, the salted hash chain, the generation barrier, outbox, erasure, export and import |
| [`PIPELINE.md`](PIPELINE.md) | how `Memory`, admission, kernel, store and policy fit together; the v1 compat adapter; conformance results |
| [`ENTITIES.md`](ENTITIES.md) | entity resolution: `find`, canonicalisation, reversible merges as marker reports, the lexical resolver, its evaluation and the false-merge policy |
| [`EXTRACTION.md`](EXTRACTION.md) | the extractor interface, its trust rules, the cost gating and the revision protocol |

## Verification and evidence

| Document | Purpose |
|---|---|
| [`HARNESS.md`](HARNESS.md) | the differential harness, the frozen-set guard and the cost ledger |
| [`CONFORMANCE.md`](CONFORMANCE.md) | the implementation-neutral fixture format and suite, with the ratchet |
| [`PERFORMANCE.md`](PERFORMANCE.md) | declared performance targets, the benchmark method and every measured pass, with misses |
| [`eval/AGENT_BENCHMARK.md`](eval/AGENT_BENCHMARK.md) | RETRACT-ACT: the agent-level benchmark's protocol, scenarios and scoring (a draft pre-registration) |
| [`eval/AGENT_BENCHMARK_RESULTS.md`](eval/AGENT_BENCHMARK_RESULTS.md) | the first symbolic run of RETRACT-ACT with palimem, failures and caveats |
| [`eval/AGENT_BENCHMARK_LLM_RESULTS.md`](eval/AGENT_BENCHMARK_LLM_RESULTS.md) | RETRACT-ACT with an LLM in the loop (gpt-oss-20b, ministral-14b): palimem against last-write-wins and a raw-log baseline, with caveats |
| [`eval/EXTRACTION_GATE.md`](eval/EXTRACTION_GATE.md) | the extractor-quality gate (G-X): thresholds declared per model before any run |
| [`eval/EXTRACTION_RESULTS.md`](eval/EXTRACTION_RESULTS.md) | live extractor measurements on the dev split, prompt revisions and the single test run |

## Operating the project

| Document | Purpose |
|---|---|
| [`RELEASING.md`](RELEASING.md) | one-time setup, the release checklist, the 0.1.0 promise, the release-candidate audit |
| [`MORNING_REVIEW.md`](MORNING_REVIEW.md) | the maintainer's running list of defaults chosen, findings and open questions, in the order they arose |

The repository root also holds [`CONTRIBUTING.md`](../CONTRIBUTING.md), [`GOVERNANCE.md`](../GOVERNANCE.md),
[`CODE_OF_CONDUCT.md`](../CODE_OF_CONDUCT.md), [`SECURITY.md`](../SECURITY.md) and [`CHANGELOG.md`](../CHANGELOG.md).
