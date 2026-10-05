# palimem

**Justified memory for LLM agents.** Beliefs are held with the evidence that justifies them; withdrawing or correcting a report repairs every conclusion that depended on it; unresolved alternatives are kept rather than guessed.

> **Status: pre-alpha (0.0.x).** The `Memory` facade, the agent tool API, an MCP server and a CLI exist in this repository; the package on PyPI (0.0.1) is still only a name reservation, so install from a checkout (`pip install -e .`). Contracts may break at any 0.x release, and 1.0 means the gate-G2 acceptance suite passes. Nothing below is a claim about a released system. What has and has not been measured is in [section 7](#7-measured-so-far-and-what-it-does-not-show) and [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md).

palimem is the SDK built on the revision kernel validated in the PALIMPSEST study ([DOI 10.5281/zenodo.23127764](https://doi.org/10.5281/zenodo.23127764), code at [`chleiva/palimpsest`](https://github.com/chleiva/palimpsest)).

## 1. What it is for

Every derived belief pins the versions of the reports that justified it, so withdrawing or correcting a report removes the justifications that depended on it, two or more steps downstream. Conclusions are withdrawn only when their surviving support no longer warrants them: two independent reports for the same value survive the loss of one. Where evidence does not decide, unresolved alternatives are retained and reported (status, assertion, alternatives, provenance) rather than resolved by recency, reliability or an LLM's guess. Answers come from a store, not from replaying the log, and every answer can be traced to the reports that justify it.

These are the **measured results of the PALIMPSEST study** (working paper, not peer reviewed; the study's own benchmark and generator), not measurements of this SDK. Figures from the study:

- Disabling retraction propagation costs about **20 points** on warranted downstream queries in every setting of the study.
- The symbolic kernel commits on about **a seventh** as many unresolved slots as a prompted LLM adjudicator over the same claims.
- The incremental store returned the same status, value and alternatives as symbolic replay on all **40,030** benchmark queries, at **6 to 12 times lower latency**.

This SDK reproduces the *agreement* part on Setting 1 (the full pipeline matches the study's frozen gold on all 30,272 Setting 1 queries, 0 disagreements; see [section 7](#7-measured-so-far-and-what-it-does-not-show)). The 20-point and the 6-to-12-times figures have **not** been reproduced by this SDK.

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

The full regime table (accuracy against the hidden world, by whether the latest report is true, delayed or erroneous) is in the paper: [`PALIMPSEST_v1.0`](https://doi.org/10.5281/zenodo.23127764). The headline numbers, all study results:

| Situation (study generator) | Last-write-wins | Justified belief (`P0c`) |
|---|---|---|
| Latest report is true | right on about 92% of value queries | abstains on 20 to 45% |
| Latest report is an injected attack | commits to it on 100% of queries | commits to it on 78% (support-argmax: 82%) |

The poisoning figure comes from 78 queries and a single attacker model; the design calls it "better, not safe". The threat model is in [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md).

## 6. Quickstart

Three calls cover the common case (a checkout install, `pip install -e .`; this snippet is run by the test suite):

```python
from palimem import Memory

m = Memory("agent.db")                                   # SQLite file (":memory:" for a throwaway store)
r = m.observe({"entity": "alice", "attr": "employer", "value": "Acme"}, source="hr")
a = m.ask("employer", "alice")                           # an Answer: kernel_status, decision, provenance
print(a.kernel_status.value, a.decision.value)
m.withdraw(r.report_id, actor="connector:hr")            # authority-checked; cascades through justifications
print(m.ask("employer", "alice").kernel_status.value)
```

```text
established commit
unknown
```

With no schema declared, an attribute is declared the first time it is seen as a multi-valued, open-world set (the
safest class: absence is `unknown`, never "no"). Declare a schema for single-valued or changeable attributes
(`Memory("agent.db", schema=...)`). `observe` takes a typed claim or a `Report`; plain text needs an extractor and is
refused otherwise: palimem never guesses a key from prose.

An LLM never gets this object. It gets a session whose source, origin and actor the host fixes:

```python
tools = m.agent_session("agent:support")                 # host code; the tools below are what the LLM may call
print(tools.call("recall", {"query": {"entity": "alice", "attr": "employer"}}).text)
```

```text
alice/employer: UNKNOWN (no admissible evidence). Do not guess; say it is unknown or ask.
  decision=abstain; policy=p-default.
```

The same tools are served over MCP by `palimem mcp agent.db --principal agent:support`. How to read an answer
(`established`, `unresolved`, `unknown`, `single_origin`, `resource_limited`) is in [`docs/AGENT_GUIDE.md`](docs/AGENT_GUIDE.md).
The structure of the system is in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

The acceptance tests are the study's frozen evaluation sets (fetched and checksum-verified by the harness) plus the
independent conformance fixtures in [`tests/conformance/`](tests/conformance/) (90 fixtures; format in
[`docs/CONFORMANCE.md`](docs/CONFORMANCE.md)). The fixtures are written to be implementation-neutral JSON.

## 7. Measured so far, and what it does not show

Every number here comes from a document in this repository that holds the method, the commands and the caveats; read
that document before quoting a number. All runs were on one laptop, with one seed or one pass unless stated, and most
rest on a single author's data.

| What | Result | Does not show | Where |
|---|---|---|---|
| **Agreement with the study's frozen gold (Setting 1)** | The full pipeline (log, admission, kernel, store, policy, through the v1 compat adapter) answers all **30,272** queries of the **500** frozen streams with **0 disagreements** on status, value and alternatives, on both backends (in memory over all streams; SQLite over every second stream, 15,149 queries). | Settings 2 and 3 (they need the cached LLM extractions) are not covered. Agreement with an oracle written by the same author is conformance, not proof the semantics are the right ones. | [`docs/PIPELINE.md`](docs/PIPELINE.md), [`docs/HARNESS.md`](docs/HARNESS.md), [`docs/PERFORMANCE.md`](docs/PERFORMANCE.md) §10.5 |
| **Provenance** | Through the compat projection (the oracle's flat set of report ids), **0 disagreements** on the same 30,272 queries. The product's own rule (subset-minimal environments over base reports) is **not** what the oracle computes: over the 27,578 segment queries it equals the oracle's set on 19,613 and differs on 7,965, all classified. | The product rule has not been shown to be better, only different and better-defined. | [`docs/KERNEL.md`](docs/KERNEL.md) |
| **Environment budget** | The enumeration kernel at a budget of 12 reports per key matched the brute-force global oracle on **1,050** freshly generated streams: **80,856** comparisons, **0 disagreements**; the default budget is now **12**. | The P0cSU half of the reference oracle was extended from the addendum text, so it is not independent; only one key per stream is at 8 to 12 reports; a key at 12 costs about 80 ms per recompute. | [`docs/BUDGET_CROSSCHECK.md`](docs/BUDGET_CROSSCHECK.md) |
| **Agent-level benchmark (RETRACT-ACT), symbolic** | Test split, 20 scenarios, 25 decision points, each system run once, no LLM: harmful-action rate **0.000** for `palimem_justified`, 0.080 for `palimem_recency`, 0.120 for `palimem_lww`, against 0.560 for scripted last-write-wins and 0.360 for scripted last-write-wins with retractions. | A fixed rule stands in for the agent, so this checks that the kernel reproduces the gold (H0); it is not evidence that an agent behaves better. One author wrote the scenarios and the gold; no second annotator; about ±0.17 per system at 20 scenarios; typed input removes extraction error; negative evidence is a kernel gap (the one test miss). | [`docs/eval/AGENT_BENCHMARK_RESULTS.md`](docs/eval/AGENT_BENCHMARK_RESULTS.md) |
| **Extractor quality (G-X gate), cheap Bedrock models** | On the frozen **test** split, run once, **gpt-oss-20b with prompt revision 3 fails the declared gate on one criterion**: dropped change cues 4 of 17 = **0.235** against a 0.20 maximum (95% interval 0.096 to 0.473; three drops would have passed). Claim F1 0.837, cue accuracy 0.926, wrong-value rate 0.045 and injection compliance 0 of 7 all pass. On dev, with the first frozen prompt, all three models failed; after three prompt revisions (dev numbers are optimistic) claim F1 reached 0.882 (gpt-oss-20b), 0.957 (Ministral 14B) and 0.897 (Ministral 8B). The Ministral models were **not** run on test. | 72 test items and 17 change claims cannot separate close rates; the items and labels were written by one author with LLM assistance; the dropped-change-cue upper bound (0.473) sits near the study's 0.50 point where last-write-wins overtakes justified belief. | [`docs/eval/EXTRACTION_RESULTS.md`](docs/eval/EXTRACTION_RESULTS.md), [`docs/eval/EXTRACTION_GATE.md`](docs/eval/EXTRACTION_GATE.md) |
| **Performance targets (declared before any run)** | At the sizes reached (up to 10,000 reports): query latency (T1, p99 4.6 ms at 10,000 reports) and recovery after a kill (T4, 0.13 s, nothing lost) are inside their targets. **Missed:** append p99 (T2, 240 ms against 100 ms), memory per report (T3, about 5 times over), append-latency flatness (T6) and disk per report (T7, about 10 times over). The store-versus-replay crossover (T5) is not shown at its reference size. | No run reached the 10^5-report reference size, so every target judged there is "not shown" or extrapolated; concurrency, multi-process use and real workloads are not measured. | [`docs/PERFORMANCE.md`](docs/PERFORMANCE.md) |

The candidate fast kernel is built and matches the enumeration kernel and the gold on the frozen set, but it is **not**
switched on; five promotion criteria are open ([`docs/FAST_KERNEL.md`](docs/FAST_KERNEL.md)).

## 8. Status of this repository

| Part | State | Where |
|---|---|---|
| Contract types and JSON Schemas | Built; schemas generated and drift-checked in CI | [`docs/TYPES.md`](docs/TYPES.md), `schemas/` |
| Decision records S-01 to S-13 and contract items | Written and decided | [`docs/decisions/`](docs/decisions/), [`docs/CONTRACT_PENDING.md`](docs/CONTRACT_PENDING.md) |
| Admission, authority and policy | Built; admission is incremental, with the whole-log evaluation kept as the audit oracle | `src/palimem/admission/`, `src/palimem/policy/`, [`docs/API_TRUST_BOUNDARY.md`](docs/API_TRUST_BOUNDARY.md) |
| Kernel | Enumeration kernel is the production kernel; a faster candidate kernel exists and is off by default; no negative evidence, no rule exceptions, no authorised-dispute semantics yet | [`docs/KERNEL.md`](docs/KERNEL.md), [`docs/FAST_KERNEL.md`](docs/FAST_KERNEL.md) |
| Storage | In-memory and SQLite backends, salted hash-chained log with `verify_log`, generation barrier, notification outbox, versioned inputs, erasure with repair, JSONL export and import, crash tests. Reversible entity merges are not built | [`docs/STORAGE.md`](docs/STORAGE.md) |
| Pipeline and v1 compat adapter | Built; matches the frozen gold (section 7) | [`docs/PIPELINE.md`](docs/PIPELINE.md) |
| Differential harness, frozen-set guard, cost ledger | Built; Setting 1 only; a hard cap of $20 for new LLM work | [`docs/HARNESS.md`](docs/HARNESS.md) |
| Conformance fixtures | Built: 90 fixtures plus a ratchet; against `Memory` 27 pass, 10 fail with recorded causes, 13 are skipped, 18 await a decision, 2 are shells; the 20 trust-boundary fixtures run through their own runner | [`docs/CONFORMANCE.md`](docs/CONFORMANCE.md), [`docs/PIPELINE.md`](docs/PIPELINE.md) |
| `Memory` facade, agent tool API, MCP server (stdio), CLI | Built, pre-alpha; 18 of 20 trust-boundary fixtures pass, the other two have recorded causes; the MCP server is tested against a subprocess and an in-process fake, not real MCP clients | [`docs/AGENT_GUIDE.md`](docs/AGENT_GUIDE.md), [`docs/API_TRUST_BOUNDARY.md`](docs/API_TRUST_BOUNDARY.md) |
| Extractor interface, extraction-quality set and gate | Built; one gate run, which failed (section 7). Entity resolution beyond lexical `find` is not built | [`docs/EXTRACTION.md`](docs/EXTRACTION.md), [`docs/eval/EXTRACTION_GATE.md`](docs/eval/EXTRACTION_GATE.md) |
| Agent-level benchmark (RETRACT-ACT) | Designed; first symbolic run done; no LLM-in-the-loop run and no third-party baselines | [`docs/eval/AGENT_BENCHMARK.md`](docs/eval/AGENT_BENCHMARK.md) |
| Threat model and security policy | Written | [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md), [`SECURITY.md`](SECURITY.md) |
| Performance benchmarks | Targets declared; suite built; four of seven targets missed at the sizes reached | [`docs/PERFORMANCE.md`](docs/PERFORMANCE.md) |

Everything not yet done, not measured or not decided is listed with its evidence in [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md).
The index of all documents is [`docs/README.md`](docs/README.md). Versioning: [`docs/VERSIONING.md`](docs/VERSIONING.md).
Releasing: [`docs/RELEASING.md`](docs/RELEASING.md).

## Contributing and licence

See [`CONTRIBUTING.md`](CONTRIBUTING.md), [`GOVERNANCE.md`](GOVERNANCE.md) and [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md). Licence: MIT (code), CC BY 4.0 (documentation and data).
