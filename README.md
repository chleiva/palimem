# palimem

**Justified memory for LLM agents.** Beliefs are held with the evidence that justifies them; withdrawing or correcting a report repairs every conclusion that depended on it; unresolved alternatives are kept rather than guessed.

> **Status: pre-alpha (0.0.x). The memory API does not exist yet.** The package on PyPI is a name reservation. This repository holds the contracts, the evidence log and storage layer, admission and policy code, and the evaluation harness; the kernel is still being ported. Contracts may break at any 0.x release, and 1.0 means the gate-G2 acceptance suite passes. Nothing below is a claim about a released system.

palimem is the SDK built on the revision kernel validated in the PALIMPSEST study ([DOI 10.5281/zenodo.23127764](https://doi.org/10.5281/zenodo.23127764), code at [`chleiva/palimpsest`](https://github.com/chleiva/palimpsest)).

## 1. What it is for

Every derived belief pins the versions of the reports that justified it, so withdrawing or correcting a report removes the justifications that depended on it, two or more steps downstream. Conclusions are withdrawn only when their surviving support no longer warrants them: two independent reports for the same value survive the loss of one. Where evidence does not decide, unresolved alternatives are retained and reported (status, assertion, alternatives, provenance) rather than resolved by recency, reliability or an LLM's guess. Answers come from a store, not from replaying the log, and every answer can be traced to the reports that justify it.

These are the **measured results of the PALIMPSEST study** (working paper, not peer reviewed; the study's own benchmark and generator), not measurements of this SDK. Figures from the design document:

- Disabling retraction propagation costs about **20 points** on warranted downstream queries in every setting of the study.
- The symbolic kernel commits on about **a seventh** as many unresolved slots as a prompted LLM adjudicator over the same claims.
- The incremental store returned the same status, value and alternatives as symbolic replay on all **40,030** benchmark queries, at **6 to 12 times lower latency**.

The differential harness in this repository reproduces the store-versus-replay agreement on the 500 frozen Setting 1 streams (30,272 queries, 0 disagreements on status, value and alternatives; provenance lists differ on about 2.5%); see [`docs/HARNESS.md`](docs/HARNESS.md). The 20-point and latency figures have not been reproduced by this SDK.

## 2. What it is not

- **Not a vector store** and not a retrieval system.
- **Not a conversation-summary memory.** Conversation summaries, skills and tool-use traces are out of scope; they can sit beside it, not inside it.
- **Not a learning system in v1.** There are no weight updates. Learning the schema, learning the commitment policy and consolidating long histories are research work packages, not features.
- **Not a claim of truth.** It reports what the evidence justifies, under declared assumptions. Against a hidden world it can be wrong in ways last-write-wins would not be, and the reverse.
- **Not multi-tenant or distributed** in v1: one agent, one store, one process.

## 3. A note on LongMemEval

LongMemEval scores recency as truth: the latest statement in a conversation is the reference answer. This system scores against what the evidence justifies. The two targets disagree by construction on exactly the updating items. On the 78 knowledge-update items, the study measured **last-write-wins at 0.526** and **justified belief at 0.167** with coverage 0.32 (0.474 with the same-origin self-update rule, `P0cSU`).

A user who wants LongMemEval-style behaviour should run the `P0cSU` semantic configuration or the last-write-wins policy, and understand that they are choosing recency over justification. **palimem is not a LongMemEval improvement** under that benchmark's scoring target, and should not be described as one.

## 4. When to use it, and when not

Use it when evidence gets **withdrawn, corrected or disputed**, when there are **several sources of unequal reliability**, or when you need to answer **what was believed when** and to explain every answer.

Do not use it for **single-source streams where the latest statement is reliably true**. In the study, last-write-wins was right about 92% of the time in that regime, and justified belief abstained on 20 to 45% of those queries.

## 5. The regime trade

The full regime table (accuracy against the hidden world, by whether the latest report is true, delayed or erroneous) is in the paper: [`PALIMPSEST_v1.0`](https://doi.org/10.5281/zenodo.23127764). The headline numbers from the design document, all study results:

| Situation (study generator) | Last-write-wins | Justified belief (`P0c`) |
|---|---|---|
| Latest report is true | right on about 92% of value queries | abstains on 20 to 45% |
| Latest report is an injected attack | commits to it on 100% of queries | commits to it on 78% (support-argmax: 82%) |

The poisoning figure comes from 78 queries and a single attacker model; the design calls it "better, not safe". The threat model is in [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md).

## 6. Quickstart (planned API, not yet implemented)

```python
# PLANNED. This does not run today.
from palimem import Memory

m = Memory("agent.db")                                         # SQLite, schema declared or loaded
m.observe(text_or_report, source=..., origin=...)              # extract (optional) + append + admit + revise
a = m.ask("employer", "alice", valid_at=..., belief_as_of=...) # Answer: kernel_status, decision, provenance
m.withdraw(report_id, actor=...)                               # authority-checked; cascades through justifications
```

The acceptance tests are the study's frozen evaluation sets plus the independent conformance fixtures (in progress). The frozen sets are fetched and checksum-verified by the harness.

## Status of this repository

| Part | State | Where |
|---|---|---|
| Contract types and JSON Schemas | Built; schemas generated and drift-checked in CI | [`docs/TYPES.md`](docs/TYPES.md), `schemas/` |
| Decision records S-01 to S-13 and pending contract items | Written; all decided | [`docs/decisions/`](docs/decisions/), [`docs/CONTRACT_PENDING.md`](docs/CONTRACT_PENDING.md) |
| Admission, authority and policy | Built, with unit tests | `src/palimem/admission/`, `src/palimem/policy/`, [`docs/API_TRUST_BOUNDARY.md`](docs/API_TRUST_BOUNDARY.md) |
| Storage (in-memory and SQLite backends, salted hash-chained log, `verify_log`, crash tests) | Built; generation barrier, outbox, merges and erasure repair not yet | [`docs/STORAGE.md`](docs/STORAGE.md) |
| Differential harness, frozen-set guard, cost ledger | Built; Setting 1 only | [`docs/HARNESS.md`](docs/HARNESS.md) |
| Kernel (enumeration, with a faster candidate kernel researched) | In progress | [`docs/research/R41_MEMO.md`](docs/research/R41_MEMO.md) |
| Conformance fixtures | In progress | `tests/conformance/` |
| Extractor interface and extraction-quality evaluation | In progress | `tests/conformance/` |
| Agent-level benchmark (RETRACT-ACT) | Designed, not run | [`docs/eval/AGENT_BENCHMARK.md`](docs/eval/AGENT_BENCHMARK.md) |
| `Memory` facade, agent tool API, MCP server, CLI | Not started | |
| Threat model and security policy | Written | [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md), [`SECURITY.md`](SECURITY.md) |

Versioning: [`docs/VERSIONING.md`](docs/VERSIONING.md). Releasing: [`docs/RELEASING.md`](docs/RELEASING.md).

## Contributing and licence

See [`CONTRIBUTING.md`](CONTRIBUTING.md), [`GOVERNANCE.md`](GOVERNANCE.md) and [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md). Licence: MIT (code), CC BY 4.0 (documentation and data).
