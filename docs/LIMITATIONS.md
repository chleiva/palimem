# Limitations

What palimem does not do, what has not been measured, and what has not been decided, each with the evidence. This page is
meant to be read before relying on a number in the [README](../README.md). It is a list of facts as of 2026-10-05, not a
roadmap; items move off it only when the cited evidence changes.

## 1. Not built

| Limitation | What happens today | Evidence |
|---|---|---|
| **Negative evidence** (`not_value`, `not_member`, `enumeration([])`) | The kernel refuses it (`KernelUnsupported`), so the report gets no answer. It is the one miss of `palimem_justified` on the RETRACT-ACT test split (RA-012). The deposited study model also lacks it, so there is no oracle to test against | [`eval/AGENT_BENCHMARK_RESULTS.md`](eval/AGENT_BENCHMARK_RESULTS.md) §5, [`decisions/S-09.md`](decisions/S-09.md), [`KERNEL.md`](KERNEL.md) |
| **`until` and `interval` valid-time cues, partial-date precision** | Not implemented; the study's model raises `NotImplementedError` for them, so they have no oracle | [`decisions/S-09.md`](decisions/S-09.md) |
| **Rule exceptions in an open world** (`Rule.exceptions`, S-10) | The contract has no `exceptions` field and the kernel only does what the compat profile needs (closed-world rule exceptions). Fixture `s10-02` fails | [`decisions/S-10.md`](decisions/S-10.md), [`PIPELINE.md`](PIPELINE.md) (conformance table) |
| **Weighing an authorised `dispute`** | The dispute is applied and logged, but the kernel does not consume `EvidenceSet.disputes`, so the answer stays `established`. Fixtures `sec-41b` and `tb-18` fail for this reason | [`decisions/S-02.md`](decisions/S-02.md), `tests/trust_boundary/status.json`, [`PIPELINE.md`](PIPELINE.md) |
| **Source-scope withdraw** | The contract has no way to withdraw a whole source. The study's source-level retraction is reproduced in the compat profile through an admission side table; expressed as per-report withdraws, 85 of the 30,272 frozen queries differ (all one known class: the paper's retraction also removes assertions made *after* it) | [`KERNEL.md`](KERNEL.md), [`PIPELINE.md`](PIPELINE.md) |
| **Reversible entity merges and entity resolution** | `find` is lexical only; no canonicalisation or merge records. Fixtures that need merges are skipped | [`STORAGE.md`](STORAGE.md), [`EXTRACTION.md`](EXTRACTION.md), `src/palimem/facade.py` |
| **Open-schema extraction, learned schema, learned policy, calibrated confidence, consolidation** | Not built; they are research work packages. `confidence` is always `None`: the policy score is uncalibrated | [`README.md`](../README.md) §2, `src/palimem/policy/policy.py` |
| ~~A restored `Answer` variant for redacted history~~ **resolved 2026-10-05** | `NotReconstructable` is the third `Answer` variant; a snapshot whose version was erased answers with it instead of raising | [`PIPELINE.md`](PIPELINE.md), [`STORAGE.md`](STORAGE.md) |
| **Real MCP clients** | The MCP server is tested against a subprocess and an in-process fake, not against real clients; the `mcp` SDK is not used | [`AGENT_GUIDE.md`](AGENT_GUIDE.md), `tests/test_mcp.py` |
| **Typed package metadata** | The wheel has no `py.typed` marker, so type checkers do not see the package's annotations; the JSON Schemas are repository files, not shipped in the wheel | [`RELEASING.md`](RELEASING.md) §Release candidate audit |

## 2. Contract gaps and open decisions

| Item | State | Evidence |
|---|---|---|
| ~~A `change` cue's previous value has no `Report` field~~ **resolved 2026-10-05** | the optional, additive `Report.change_from` (only for cue `change`) is the carrier; the kernel and the compat converter read it, and logs written with the earlier `raw_ref` carrier are still read | [`TYPES.md`](TYPES.md), [`KERNEL.md`](KERNEL.md) |
| **Attribute kinds the contract cannot express** | Multi-valued *changeable* keys (500 in Setting 1), the cardinality of derived attributes, explicit `error_allowed` and `competing_values`. `inertia=False` on a changeable attribute has no specified semantics and the kernel refuses it | [`KERNEL.md`](KERNEL.md), [`decisions/S-08.md`](decisions/S-08.md) |
| **Attribution-only evidence answers a value query with an `established` attribution** | A consumer reading only `kernel_status` would act on an attribution (RA-018). A conservative fix (answer `unknown`, expose attributions separately) is planned, not done | [`eval/AGENT_BENCHMARK_RESULTS.md`](eval/AGENT_BENCHMARK_RESULTS.md) §5 |
| **Failed cross-source correction** | The product profile keeps it as a competing assertion (the paper's behaviour, decision S-02); design v0.3's literal text says `allege` (no effect). Which the product should do is undecided. Under the alternative `authority_source` gold profile of dev scenario RA-007, `palimem_justified` takes a harmful action (harmful-action rate 0.062 on dev); under the default gold it does not | [`eval/AGENT_BENCHMARK_RESULTS.md`](eval/AGENT_BENCHMARK_RESULTS.md) §5, [`decisions/S-02.md`](decisions/S-02.md) |
| **Default policy on an unresolved key** | The default `justified` policy asks; fixture `tb-12` expects abstain. Undecided | `tests/trust_boundary/status.json` |
| **Joint versus alternative environments** | The kernel returns one joint environment for two agreeing independent reports (an error label needs a dispute); four fixtures expect one environment per independent origin group. Changing it changes where the kernel differs from the oracle | [`PIPELINE.md`](PIPELINE.md) (conformance table), [`KERNEL.md`](KERNEL.md) |
| **Fixture versus decision disagreements** | `ind-20` expects a store-wide dirty marker while the decided design is component-scoped; `ind-22` disagrees with the store's own notes (STORAGE §9.5) | [`PIPELINE.md`](PIPELINE.md), [`STORAGE.md`](STORAGE.md) |
| **Store does not expose** | The erasure requester on a tombstone (`ind-10`) and the keys a completion job stamped or skipped (`ind-21`) | [`PIPELINE.md`](PIPELINE.md) |
| **Provenance criterion** | The product rule (subset-minimal environments over base reports) is *not* what the oracle computes: it equals the oracle's flat set on 19,613 of 27,578 segment queries and differs on 7,965 (all classified). G1's provenance criterion is met through the compat projection; whether that is the right criterion is the author's call | [`KERNEL.md`](KERNEL.md), [`decisions/S-12.md`](decisions/S-12.md) |
| **Erased key text in index columns** | Orphaned entities (the erased report was the only live evidence) are pseudonymised since 2026-10-05; the key text of an entity that still has live evidence stays in the belief index columns, and free-text diagnostics written after a rename may still name an orphaned key. No legal review of the deletion design has been done | [`STORAGE.md`](STORAGE.md) §7 and Q1 |

## 3. Not measured

| What | Why it matters | Evidence |
|---|---|---|
| **The 10^5-report reference size** | No run reached it (the largest completed 10,000 reports in about 90 to 190 s). Every target judged at the reference size is "not shown" or an extrapolation | [`PERFORMANCE.md`](PERFORMANCE.md) §9.3, §10 |
| **Performance targets missed at the sizes reached** | Append p99 240 ms against 100 ms (T2), memory about 5 times over (T3), append-latency growth ratio 4.1 over 1,000 to 10,000 reports (T6, never re-measured at 300), disk about 10 times over (T7). The tail is organisation fan-out, which is the pinning design | [`PERFORMANCE.md`](PERFORMANCE.md) §9.4, §10.2 to §10.4 |
| **Query latency, recovery and the store-versus-replay crossover at scale** | T1 (p99 4.6 ms at 10,000 reports) and T4 (0.13 s, nothing lost) hold at the sizes measured; T5 is not shown at its reference size and the store beats a *warm* replay by only 2.6 times | [`PERFORMANCE.md`](PERFORMANCE.md) §9.3, §10 |
| **Concurrency, multi-process use, multi-tenant isolation, a real agent workload** | One process, one writer; all workloads are synthetic and laptop-bound. The design requires a one-week load characterisation (T-J4), which has not been run | [`PERFORMANCE.md`](PERFORMANCE.md) §6 |
| **The OpenAI-compatible extractor transport** | Tested only with a fake transport; every live extractor run used the Bedrock Converse transport. The `openai-compat` extra is therefore untested against a real endpoint | [`EXTRACTION.md`](EXTRACTION.md), [`eval/EXTRACTION_RESULTS.md`](eval/EXTRACTION_RESULTS.md) |
| **Settings 2 and 3 of the study** | Replayed from the study's cached extracted claims: 0 disagreements with the study's store on 100 of 100 Setting 2 streams, 15 of 15 stronger-backbone streams and 29 of 30 Setting 3 streams. Not replayed: one Setting 3 stream (24 of 714 queries), refused by the kernel (a single-valued derived attribute whose world holds several values). The extraction itself is not re-measured, and provenance is not compared there | [`SETTINGS23.md`](SETTINGS23.md), `tests/test_replay_s23.py` |
| **The study's headline effects in this SDK** | The 20-point retraction effect, the 6-to-12-times latency and the LongMemEval figures come from the study; this SDK reproduces agreement with the frozen gold, not those effects | [`README.md`](../README.md) §1 and §3 |
| **Poisoning with quarantine and confirmation** | The study's 0.78 injected-commit rate is from 78 queries and one attacker model. The declared gate (at most 0.78 now, a target under 0.10 once an admission policy exists, and a behavioural gate for a compromised trusted source) has not been run | [`THREAT_MODEL.md`](THREAT_MODEL.md) §8 |
| **Budget cross-check scope** | The P0cSU half of the reference oracle was extended from the addendum text (not independent); only one key per stream is at 8 to 12 reports; the relaxation ladder was never exercised; a key at 12 costs about 80 ms per recompute | [`BUDGET_CROSSCHECK.md`](BUDGET_CROSSCHECK.md) |
| **The candidate fast kernel above n = 14, and its promotion** | No independent check above 14 (enumeration cannot run there); five promotion criteria are open; it is off by default | [`FAST_KERNEL.md`](FAST_KERNEL.md) |
| **Tamper-evidence strength** | The hash chain detects edits, deletions and reordering for anyone who holds a trusted head hash. It does not stop an attacker who rewrites the whole file and the head, and no external anchoring is configured by default | [`THREAT_MODEL.md`](THREAT_MODEL.md) §5, [`STORAGE.md`](STORAGE.md) |

## 4. Evidence quality

- **One author wrote the oracle, the gold, the kernel and the scenarios, with LLM assistance.** Agreement with the oracle is
  conformance, not proof the semantics are right. The independent fixtures were hand-derived but by the same author; no second
  annotator has reviewed them. External review of the contract has not happened.
  ([`eval/AGENT_BENCHMARK_RESULTS.md`](eval/AGENT_BENCHMARK_RESULTS.md) §6, [`eval/EXTRACTION_GATE.md`](eval/EXTRACTION_GATE.md))
- **The agent-level result is symbolic.** A fixed rule stands in for the agent and the input is typed, so the run checks that
  the kernel reproduces the gold (H0); it is not evidence that an agent behaves better, and extraction error is removed
  entirely. The test split has 20 scenarios (about ±0.17 per system), only 2 abstain points, 1 review point and 0
  `revalidate` points. Two gold entries look questionable (RA-026.d1, RA-007) and were not edited.
  ([`eval/AGENT_BENCHMARK_RESULTS.md`](eval/AGENT_BENCHMARK_RESULTS.md))
- **No third-party baselines have been run.** The benchmark design adapts Mem0 and Graphiti at the memory interface and
  excludes Letta (its memory is itself an LLM agent); none of it has been executed and the adapter APIs are unverified.
  ([`eval/AGENT_BENCHMARK.md`](eval/AGENT_BENCHMARK.md))
- **No LLM-in-the-loop agent benchmark run exists.**

## 5. Natural-language input: the extractor gate has failed

- On the frozen test split, run once, **gpt-oss-20b with prompt revision 3 fails the declared gate on one criterion**: dropped
  change cues 4 of 17 = 0.235 against a maximum of 0.20 (95% interval 0.096 to 0.473). The Ministral models were **not** run on
  test; their dev results with the first frozen prompt all failed, and after three revisions they fail only the injection
  criterion on dev (dev numbers after tuning are optimistic). Whether to judge that criterion by interval overlap is
  undecided. ([`eval/EXTRACTION_RESULTS.md`](eval/EXTRACTION_RESULTS.md) §7 and §9)
- The extraction-quality set is small (72 test items, 17 change claims, 7 injection items), labelled by one author with LLM
  assistance, and its valid-time F1 was 0.08 to 0.53 on dev (the temporal errors are not yet classified).
- The study's fragility points still apply: last-write-wins overtakes justified belief at about 35% wrong extracted values or
  about 50% dropped change cues. The dropped-change-cue upper bound of 0.473 for the best configuration sits close to that.
- **Consequence:** the README makes no claim about natural-language input quality. The typed path (no LLM) is the one the
  measurements above cover.

## 6. Release state

- The only released artefact is `palimem` 0.0.1 on PyPI and npm: a name reservation with no functionality. Everything else is
  installed from a checkout. PyPI Trusted Publishing is documented but not yet configured by hand.
  ([`RELEASING.md`](RELEASING.md))
- Contracts may break at any 0.x release; 1.0 means the gate-G2 acceptance suite passes, which it does not yet
  ([`VERSIONING.md`](VERSIONING.md)).

## How the second annotation was produced

The RETRACT-ACT second annotation (29 decision points; kappa 0.866 against the registered gold, interval [0.630, 1.000]) was instructed and reviewed by the author but **executed by a model (GPT 6 Astra)**. No fully human annotation exists or is planned. It is weaker than an independent human annotator; the extraction-quality labels and the entity-resolution pairs have no second annotation at all. Evidence: [`eval/ANNOTATION.md`](eval/ANNOTATION.md).
