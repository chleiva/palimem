# Gate status

Where every gate of the project stands, with the criterion as written, the evidence in this repository, and what is missing.
Derived on 2026-10-05 from `main` at `e20e6ac` (first derived at `ab7774c`; refreshed by the release-preparation pass, which re-checked every figure the commands in section 11 produce and brought the rest up to date with the author's rulings of 2026-10-05, [`decisions/RULINGS-2026-10-05.md`](decisions/RULINGS-2026-10-05.md)), including the Settings 2/3 replay ([`SETTINGS23.md`](SETTINGS23.md)). Numbers marked
**(re-run today)** were produced by running the command listed in section 11; the others are quoted from the document that holds
the method and the caveats, which you should read before relying on them. Nothing here is a release decision.

Status words: **met** (criterion satisfied by evidence in the repo), **partly met**, **not met** (tried, criterion not satisfied),
**not started**, **blocked** (waiting on a decision only the author can make; the decision is named).

## 1. Summary

| Gate | What it decides | Status | The evidence in one line | What is missing |
|---|---|---|---|---|
| **G0** semantic contract | contracts written, reviewed, frozen as versioned fixtures | **partly met** | types, 19 JSON Schemas, 13 decision records, 90 + 20 conformance fixtures | no freeze tag, no external review, two open contract gaps |
| **G1** kernel and store parity | identical answers to the oracle on every frozen query, provenance identical, independent tests green, recovery green | **partly met** | 30,272 of 30,272 queries, 0 disagreements; provenance 0 through the compat projection; recovery tests green | 5 independent fixtures fail, 8 are skipped and 16 pending a decision; the provenance criterion is **blocked** on S-12 |
| **G2** service parity, integration, targets | Setting 2 inputs re-run through the service, declared targets met on a load week, integration fixtures, packaging | **partly met** | Setting 2 replay 6,737 of 6,737 queries, 0 disagreements | no extractor re-run, targets T2 T3 T6 T7 missed, no load week, no `inquiry.resolvers`, the published package is the 0.0.1 placeholder (0.1.0 prepared, not released) |
| **G3** policy learning | learned policy beats P0cSU on a sealed held-out stratum | **not started** | three presets exist, nothing is learned | everything |
| **G4** consolidation | keys above the enumeration budget answered with oracle agreement | **not started** | a research spike and a candidate fast kernel | the shared representation and the above-budget stratum |
| **G-S** security | threat model, poisoning gate, trust boundary, log integrity, policy files | **partly met** | 40 threats written; agent trust-boundary fixtures 19 of 20; chain and `verify_log` tested | no poisoning re-run, 12 of 14 security conformance fixtures pending a decision |
| **G-X** extraction quality | extractor meets declared per-model thresholds on a frozen split | **not met** | gpt-oss-20b, single test run: fails one criterion (0.235 against 0.20) | the Ministral models were not run on test |
| **G-A** agent-level | lowers harmful actions against last-write-wins and a third-party system | **partly met** | harmful-action rate 0.040 / 0.000 against 0.547 / 0.520 (exploratory) | no third-party baseline, not a confirmatory analysis |

## 2. G0, the semantic contract

**Criterion as written.** The status ladder with the open-world default and the compatibility profile, the three-stage
admission / semantics / policy boundary, the two-axis query, the per-candidate support mapping, the authority rules and the
resource contract are written down as contracts, reviewed, and frozen as versioned fixtures. Nothing in phase 1 starts until G0
passes.

**Evidence.**

* Executable contract: `palimem.types` and 19 generated JSON Schemas with 32 examples, drift-checked in CI ([`TYPES.md`](TYPES.md)).
* 13 decision records, all decided; the six contract changes that needed an explicit author line were decided on 2026-10-04
  ([`decisions/README.md`](decisions/README.md), [`CONTRACT_PENDING.md`](CONTRACT_PENDING.md)).
* Fixtures: 90 conformance fixtures (3 tagged G0, 80 G1, 7 G2) plus 24 trust-boundary fixtures ([`CONFORMANCE.md`](CONFORMANCE.md)).
  **(re-run today)** the 3 G0 fixtures pass.
* Versioning and the RFC process: [`VERSIONING.md`](VERSIONING.md).

**Missing.**

* **Not frozen.** [`VERSIONING.md`](VERSIONING.md) §9 says G0 freezes the contracts as `contracts-v2.0-rc1`; the repository has no
  tags. Work proceeded past G0 on the decision that 0.x may break contracts, so G0 blocks a 1.0, not the code.
* **No external review** (one author, with LLM-assisted reviews; the same weakness as the oracle).
* **Contract gaps.** Decided by the author on 2026-10-05 and implemented: an optional `change_from` on `Report`; a `merge`
  power with a `MergeRecord`; a `NotReconstructable` answer variant; `verify(scope=log|beliefs)`; no attribution selector on
  `Query` (value queries are content-only, `Memory.attributions(key)` is the attribution path); two compatible `not_value`
  candidates answer `unknown`; A-ERR is evaluated per key; no source-scope withdraw (an `exclude_source` admission operation
  instead); `Rule.exceptions` reserved in 0.x. **Still open:** the cardinality of derived attributes (the one Setting 3 stream the
  kernel refuses, [`SETTINGS23.md`](SETTINGS23.md) §4.1) and explicit `error_allowed` / `competing_values`
  ([`MORNING_REVIEW.md`](MORNING_REVIEW.md) has the running list).

**Status: partly met.** Contracts are written, executable and fixtured; they are not reviewed externally or frozen.

## 3. G1, kernel and store parity with the oracle

**Criterion as written.** Identical kernel status, values and alternatives to replay on every frozen query under the
compatibility profile, provenance identical, every independent test green, recovery test green.

**Evidence.**

* **Answers.** The full pipeline (log, admission, kernel, store, policy, through the v1 compat adapter) answers all **30,272**
  queries of the **500** frozen Setting 1 streams with **0 disagreements** on status, value and alternatives (in memory over all
  streams, 65,632 appends; SQLite over every second stream, 15,149 queries): [`PERFORMANCE.md`](PERFORMANCE.md) §10.5.
  **(re-run today, sample)** 25 streams (every 20th), both backends, strict: 1,522 queries, 0 disagreements, 0 provenance
  disagreements; the kernel alone, strict: PASS, 1,522 queries, 0 disagreements, 0 unexplained.
* **Provenance.** Through the compat projection (the oracle's flat set of report ids), 0 disagreements on the same 30,272 queries
  (same document). The product's own rule, subset-minimal environments over base reports, is **not** what the oracle computes:
  over 27,578 segment queries it equals the oracle's set on 19,613 and differs on 7,965, all classified with 0 unexplained
  ([`KERNEL.md`](KERNEL.md) provenance section). Stored supports equal the environments recomputed from the admitted evidence
  (**re-run today**: 1,295 checked, 0 mismatches on the sample).
* **Recovery.** The store test suite kills a separate process at every step of the transaction and a retry converges with no
  duplicate and no partial revision (`tests/store/test_crash.py`, `test_crash_wave2.py`); a whole-pipeline crash test exists
  (`tests/test_pipeline.py`). **(re-run today)** `tests/store`: 214 passed, 2 skipped.
* **Independent tests.** **(re-run today)** against `palimem.Memory`, of the 114 fixtures the runner lists (3 G0, 104 G1 including the 24 trust-boundary
  ones, 7 G2): **57 pass, 5 fail, 8 skipped, 16 pending a decision, 4 shells, 24 not run by this runner** (the 24 trust-boundary
  fixtures have their own runner: 24 pass, below).
* Static exactness check, the {A,B,∅} counter-example as a regression test, and the equivalence suite of incremental admission
  against the whole-log oracle (2,040 random streams, compared after every append): [`KERNEL.md`](KERNEL.md), [`PIPELINE.md`](PIPELINE.md).

**Missing.**

* **The 5 failing fixtures, each with its cause.** `ind-01`, `ind-08b`, `ind-16`, `sec-39b`: they expect independent origin groups
  to give *alternative* environments (and, for `ind-08b`, an explanation that truncates at the budget); the kernel returns one
  *joint* environment (an error label needs a dispute); **blocked** on an author decision. `ind-22`: the fixture disagrees with
  the store's own design notes (STORAGE §9.5). The earlier failures `ind-20` (reconciled to the component-scoped dirty marker),
  `s10-02` (rule exceptions are reserved in 0.x) and `sec-41b` (authorised dispute is implemented) no longer fail.
* **8 skipped and 16 pending** (of all 110 listed fixtures). Skips: crash-injection, tamper and restore capabilities the `Memory`
  adapter does not expose (the store tests cover them), `inertia=false` semantics (specified, refused until implemented), an
  extractor. Pending: fixtures waiting on an open decision (most are security fixtures whose mitigation is only proposed).
* **Provenance criterion is blocked.** Your S-12 line said `flatten` equals the oracle's provenance by definition and G1 requires
  exact equality on the frozen sets. That holds through the compat projection (met) and cannot hold for the product rule (above).
  Decision needed: G1's provenance criterion means equality *through the compat profile*.
* Settings 2 and 3 are G2 evidence, not G1.

**Status: partly met.** The answer criterion is met on every frozen query; the provenance criterion is met only through the
compat profile and is blocked on a confirmation; "every independent test green" is not met.

## 4. G2, service parity, integration fixtures and targets

**Criterion as written.** The frozen Setting 2 inputs re-run through the service give an empty report-by-report diff against the
deposit through the adapter; the declared p99 latency, memory and recovery targets are met on the load week; the integration
fixtures pass (a name resolved through canonicalisation, an attribution that does not establish its content, an inquiry naming
its resolver, a notification delivered after a crash between commit and send). Packaging (E2.7): a fresh environment can
`pip install` the package, run the quickstart against the frozen Setting 1 streams and reproduce the deposited numbers through the
v1 adapter, and the README contains the six obligations.

**Evidence.**

* **Setting 2 and 3 through the pipeline.** Replayed from the study's cached extracted claims, no model call: Setting 2 **100 of
  100** streams, **6,737** queries, **0** disagreements with the study's store, on both backends; the stronger-backbone subset 15
  of 15, 994 queries, 0; Setting 3 **29 of 30** streams, 690 of 714 queries, 0 ([`SETTINGS23.md`](SETTINGS23.md)). In Setting 2 the
  claims, not the pipeline, carry the extraction error: gold agreement 6,285 of 6,737 for palimem and for the study's store alike.
* **Integration fixtures.** *Canonicalisation*: `find` and reversible merges are built, `ind-09` passes, 116 entity tests on both
  backends ([`ENTITIES.md`](ENTITIES.md)). *Attribution*: `ind-15` and `ind-19a` pass; `ind-19b` (the extraction half) is skipped.
  *Notification after a crash*: the outbox is at-least-once with stable event ids and its crash tests pass at the store level
  (`tests/store/test_outbox.py`); `ind-17` is skipped in the `Memory` adapter for lack of a crash hook.
* **Targets declared before any run** ([`PERFORMANCE.md`](PERFORMANCE.md) §3): at the sizes reached (up to 10,000 reports) query
  latency (T1, p99 4.6 ms) and recovery after a kill (T4, 0.13 s, nothing lost) hold; append p99 (T2) is 240 ms against 100;
  memory (T3) 5.0 KiB per report against 1; the p99 ratio over scale (T6) 4.1 over 1,000 to 10,000 reports against 4 (it was 21.8
  over 300 to 10,000 before incremental admission, and no 300 point was measured after); disk (T7) 48 to 59 KiB per report against
  5. After incremental admission the plain append is about 1.0 ms at 10,000 reports and the p99 tail is organisation fan-out, the
  pinning design ([`PERFORMANCE.md`](PERFORMANCE.md) §10).
* **Packaging.** A wheel built from this tree installs into a clean virtual environment with zero dependencies; the quickstart
  and the CLI run from it, `twine check` passes, and `py.typed` is in the wheel ([`RELEASING.md`](RELEASING.md), Python 3.13 only).
  The README carries the six obligations in the required order, with the LongMemEval note before any table.

**Missing.**

* **The report-by-report extraction diff.** The criterion as written re-runs the *extraction* and diffs the reports. That needs a
  model call and, with a cheap model, cannot be empty by construction (a different extractor makes different claims; the extractor
  measurements are in [`eval/EXTRACTION_RESULTS.md`](eval/EXTRACTION_RESULTS.md)). What has been shown is the other half: the same
  cached claims through the pipeline give an empty diff of *answers*. The criterion needs rewording or a decision on which extractor
  it binds.
* **Load week and declared targets.** No real-workload run; T2, T3, T6 and T7 are missed and T1, T4, T5 are not shown at the
  reference size (10^5 reports; no run reached it). T5 holds against a cold replay and not against a replay that reuses the
  cached admission evaluation.
* **`inquiry` naming its resolver.** `Inquiry.competing` and `missing` are populated; `resolvers` (the source classes that could
  decide) is never populated: generation from the rule schema (T-D4) is not built.
* **Packaging.** The only published artefact is the 0.0.1 name reservation, an empty package; `pip install palimem` today installs
  no functionality (0.1.0 is prepared in this repository and has not been released). Reproducing the deposited Setting 1
  numbers from an *installed* wheel was not done (the harness runs from a checkout). `release.yml` has never run. Setup state
  (author, 2026-10-05; the environments and branch protection verified through the GitHub API): trusted publishers on PyPI
  (environment `pypi`, required reviewer) and TestPyPI (pending publisher, environment `testpypi`), the account-wide token
  revoked, branch protection requiring the `harness` and `test` jobs.
* One Setting 3 stream (24 queries) is not replayed (kernel gap, [`SETTINGS23.md`](SETTINGS23.md) §4.1).

**Status: partly met.**

## 5. G3, learn the schema and the policy

**Criterion as written.** On the sealed held-out stratum only, under a declared comparison rule so that an abstention-rate change
cannot masquerade as improvement: at each of three fixed risk limits, coverage no lower than P0cSU's and higher at one of them, or at
matched coverage lower risk; the retraction-propagation effect on warranted downstream queries must not fall by more than its
bootstrap half-width on any setting; every number produced by the harness.

**Evidence.** None for the learning itself. Three hand-set policy presets (`justified`, `recency`, `lww`) exist and are measured
on the agent benchmark. The calibration hypothesis failed in the study; no new calibration method was tried. No held-out stratum
has been generated or sealed. Schema induction (R3.1) is not started; assisted schema drafting is not built.

**Status: not started.**

## 6. G4, consolidation

**Criterion as written.** Keys above the enumeration budget are answered under the shared representation with oracle agreement on
status, values, alternatives **and the withdrawal cascade** (the retraction fixtures run above the budget), and the explanation is
returned within its budget.

**Evidence.** The R4.1 spike ([`research/R41_MEMO.md`](research/R41_MEMO.md), verdict PARTIAL): the set of interpretations is
inherently exponential (n distinct values give 2^n − 1) even when the answers are not, so storing enumerated environments is a
dead end, and an exact existence-query kernel exists for single-valued changeable keys. That kernel is built as a candidate
([`FAST_KERNEL.md`](FAST_KERNEL.md)): it matches the gold and the enumeration kernel on all 30,272 frozen queries, is 2 ms against 78
ms at n = 12, and runs n = 100 in about 1 s. It is **not** the shared representation: it covers one key class, 16.1% of base
justifications on Setting 1 still need enumeration, provenance above the enumeration budget is truncated, and there is no
independent validation above n = 14. The environment budget is 12 after a cross-check against the brute-force oracle
([`BUDGET_CROSSCHECK.md`](BUDGET_CROSSCHECK.md)); no above-budget benchmark stratum exists.

**Status: not started** (a research spike and a candidate, neither the gate's deliverable).

## 7. G-S, security

**Criteria as written** ([`THREAT_MODEL.md`](THREAT_MODEL.md) §8, with the author's decisions of 2026-10-04), and where each stands:

| # | Criterion | Evidence | Verdict |
|---|---|---|---|
| 1 | Threat model published; each threat has a mitigation, a test or an explicit `[accepted]` note | 40 threats written; 37 rows reference a `[proposed]` mitigation, 20 a `[design]` one, 6 `[accepted]` | partly |
| 2 | SEC-01 … SEC-44 pass in the conformance suite | the 44 items map to 11 trust-boundary fixtures, 25 conformance fixtures and 8 marked not expressible; **(re-run today)** the security area has 14 conformance fixtures, 2 passing, **12 pending a decision**; the poisoning area has 9, of which 7 pass, 1 fails (`sec-39b`, the joint-versus-alternative question) and 1 is pending | **not met** |
| 3 | The agent tool API cannot set `origin`, `source`, `actor`, `origin_group` or `authority` (SEC-01 to SEC-04) | `tb-01`, `tb-02`, `tb-03`, `tb-16` pass **(re-run today)** | met |
| 4 | Poisoning re-run (T-H2) with limits declared first: untrusted source at most the measured 0.78, target under 0.10 once an admission policy exists; compromised trusted source a behaviour gate | no re-run exists; the study's 0.78 on 78 queries is the only poisoning evidence | **not started** |
| 5 | The hash chain and `verify_log` exist and SEC-25, SEC-26, SEC-30 pass | store tests `test_sec25_…`, `test_sec26_…` (two forms), `test_sec30_…` pass on both backends (**re-run today**, `tests/store`: 237 passed, 4 skipped); the `Memory` conformance runner skips them for lack of a tamper or restore hook | met at the store level |
| 6 | `SECURITY.md` in force; releases use trusted publishing with 2FA | `SECURITY.md` and `release.yml` exist; the PyPI trusted publisher is configured and the account-wide token used for the 0.0.1 reservation is revoked (author-reported, 2026-10-05; environments, private vulnerability reporting and branch protection verified through the GitHub API); `release.yml` has never run | partly |

Trust-boundary fixtures: 25 tests pass **(re-run today)**; all 24 fixtures pass, nothing is ratcheted as failing. `tb-12` passes since ruling 16 was implemented (the host API abstains with `inquiry` populated; agent sessions default to `ask`), and `tb-18` (authorised dispute) passes since the semantics rulings; `tb-21` to `tb-24` cover ruling 16 and the proposal queue of ruling 17, including an LLM trying to declare an attribute, to auto-declare and to accept its own proposal.

**Status: partly met.** The agent cannot forge identity, the log is tamper-evident and erasable, and the threat model exists; the
poisoning gate has never been run and most security fixtures are waiting on decisions.

## 8. G-X, extraction quality

**Criterion as written.** Per-model thresholds declared before any run ([`eval/EXTRACTION_GATE.md`](eval/EXTRACTION_GATE.md),
`bench/extract/gate.json`, pinned by checksum), judged on the frozen test split, run once per model after the final prompt
revision; a model that fails there fails the gate and there is no second test run. Wilson intervals on every rate; repair reported
separately; `injection_compliance` (the extractor changed behaviour because of the directive) gated, `directive_extraction`
reported only.

**Evidence** ([`eval/EXTRACTION_RESULTS.md`](eval/EXTRACTION_RESULTS.md)). On dev, three revisions on three Bedrock models (claim F1
0.636 / 0.830 / 0.848 / 0.882 for gpt-oss-20b across revisions 0 to 3; Ministral 14B 0.957 and Ministral 8B 0.897 at revision 3, which
fail only the injection criterion). On the **test split, run once, gpt-oss-20b with revision 3 fails the gate on one criterion**:
dropped change cues 4 of 17 = 0.235 against a 0.20 maximum (Wilson 95% [0.096, 0.473]; three drops would have passed). It passes
claim F1 (0.837), cue accuracy (0.926), wrong-value rate (0.045), abstention accuracy, fragmentation and injection compliance (0
of 7); 0 of 72 items needed repair. Spend $0.0196 on test; the shared ledger total across all LLM work so far is $0.457 of the $20 budget.

**Missing.** Ministral 14B and 8B were not run on test: the declared selection rule picks revision 0 for them (they fail F1 widely
there). The author ruled on 2026-10-05 that the rule stands: no Ministral test run this cycle, no fourth revision, no
interval-overlap reading of a criterion after the fact; a new pre-declared cycle may put Ministral 14B at revision 3 forward
later, which would be a first use of the test split by that model. The dev split is small (14 change claims, 7 injection items): too little power for
most rates, and the test split has 17 change claims.

**Status: not met** for the model that was tested; the other two have no test result (by the author's ruling, not run this cycle).

## 9. G-A, agent-level evidence

**Criterion as written.** On the agent benchmark the system lowers the harmful-action rate against last-write-wins and at least one
third-party baseline, at a stated cost in unnecessary asks; registered as H1 (harm against `lww_store`), H3 (cost and unnecessary
asks), H5 (third party, Mem0) in [`eval/AGENT_BENCHMARK.md`](eval/AGENT_BENCHMARK.md), with the claim "reduces harmful actions"
licensed only if H1 and H3 pass after Holm correction on the primary model and the second model agrees in direction.

**Evidence.** Symbolic, no LLM ([`eval/AGENT_BENCHMARK_RESULTS.md`](eval/AGENT_BENCHMARK_RESULTS.md)): test split (20 scenarios, 25
decision points), harmful-action rate 0.000 for `palimem_justified` against 0.560 for scripted last-write-wins, unnecessary-ask
rate 0.000 for both; this checks that the kernel reproduces the gold (H0), not that an agent behaves better. With an LLM in the loop
([`eval/AGENT_BENCHMARK_LLM_RESULTS.md`](eval/AGENT_BENCHMARK_LLM_RESULTS.md)): gpt-oss-20b 0.040 [0.000, 0.130] with palimem against
0.547 [0.361, 0.736] with last-write-wins; Ministral 14B 0.000 against 0.520; the paired risk-stratum difference is 0.633 [0.451,
0.818] and 0.650 [0.455, 0.875], with the lower bounds above 0 and both models agreeing in direction.

**Missing.** The LLM run is explicitly a pilot, not the confirmatory analysis (it used Ministral 14B in place of the registered 8B, 3
seeds not 5, no Holm correction; the second annotation is a model third opinion, not a human one); H3 and H5 are not established; **no third-party system was run** (Mem0 and
Graphiti/Zep need services, Letta is an agent itself), so the criterion's "at least one third-party baseline" is unmet. The memory
text carries its own decision instructions and the models mostly follow them, so the result says little about agents without that
text or about a hostile memory. 20 scenarios give about ±0.17; one author wrote the scenarios and the gold, and two gold
conflicts were adjudicated on 2026-10-05 (RA-006 keeps gold `ask`; RA-023.d1 keeps the P0cSU gold; RA-026.d1 is an erratum applied as a
versioned overlay, [`../bench/agent/gold_errata.md`](../bench/agent/gold_errata.md)). `ministral-14b` replaces the registered
`ministral-8b` as the second model (author's ruling 19, a dated deviation). Registered numbers stay reproducible from a pinned
product behaviour. Under the author's ruling on failed corrections, the current product answers RA-006 with `ask` again (its gold
stands); RA-007 answers `act manchester`, its default gold, and is "harmful" only against the `authority_source` profile gold,
which the ruling says is wrong ([`eval/RA-007_TRACE.md`](eval/RA-007_TRACE.md)).

**Status: partly met** (exploratory evidence against last-write-wins, stated only as the result for a compliant reader of the
kernel's text; the third-party half is unmet and the analysis is not confirmatory).

## 10. Decisions that block a gate

| Decision | Blocks | Source |
|---|---|---|
| G1's provenance criterion means equality through the compat profile | G1 | [`MORNING_REVIEW.md`](MORNING_REVIEW.md) item 0, S-12 |
| Joint versus alternative environments for independent origin groups | G1 (four fixtures) | [`MORNING_REVIEW.md`](MORNING_REVIEW.md), A2 |
| Retire the `authority_source` gold for RA-007 as superseded by ruling 1, or keep it as a documented alternative profile | G-A | [`eval/RA-007_TRACE.md`](eval/RA-007_TRACE.md) |
| Cardinality of derived attributes; explicit `error_allowed` / `competing_values` | G0 | section 2 |
| Wording of G2's "empty report-by-report diff" for a different extractor | G2 | section 4 |

Decided on 2026-10-05 and no longer blocking: the contract additions and semantics rulings ([`decisions/RULINGS-2026-10-05.md`](decisions/RULINGS-2026-10-05.md)), no Ministral test run this cycle, the second annotation accepted as a model third opinion (self-reported blind, not certified; no human annotator exists or is planned; none for the extractor labels or the entity pairs, [`LIMITATIONS.md`](LIMITATIONS.md)).

## 11. What was re-run for this document, and what was not

Re-run on 2026-10-05 on `main` at `e20e6ac` (the release-preparation pass), each from a fresh worktree with the study's frozen
data extracted from the published deposit:

| Command | Result |
|---|---|
| `python -m harness.pipeline_diff --stride 20 --backend both --provenance strict` | 25 streams, 1,522 queries per backend, 0 disagreements, 0 provenance disagreements, 1,295 stored-versus-audit supports checked, 0 mismatches |
| `python -m harness.kernel_diff --stride 20 --source-retract sidetable --strict --provenance strict` | exit 0: PASS, 1,522 queries, 0 disagreements, 0 unexplained |
| `python -m harness.exclusion_diff --stride 20 --backend both` | 0 disagreements |
| `python -m tests.conformance.runner --impl tests.conformance.impl_memory:MemoryImplementation` | 114 fixtures listed (3 G0, 104 G1 including the 24 trust-boundary ones, 7 G2): 57 pass, 5 fail, 8 skipped, 16 pending a decision, 4 shells, 24 not run by this runner |
| `pytest tests/trust_boundary` | 25; all 24 fixtures pass, none ratcheted |
| `pytest tests/store` | 237 passed, 4 skipped |
| `python -m harness.replay_s23 --dataset all` | Setting 2: 6,737 queries, 0; stronger backbone: 994, 0; Setting 3: 690 of 714, 0; both backends |
| `pytest` with study data and the cached Setting 2/3 files | 2,537 passed, 4 skipped, 2 xfailed |

**Not re-run today, quoted from the cited documents:** the full 500-stream pipeline and kernel differentials (30,272 queries; the
lead ran them on the merged tree at the semantics-rulings merge, strict, with 0 disagreements), the SQLite pass over every
second stream, the budget cross-check (1,050 fresh streams, 80,856 comparisons), every performance measurement, the extractor dev
and test runs, the agent benchmark runs, the study's published numbers, and the CI-style run without study data (the plain `test`
job of CI runs it on every push).

**Could not be verified:** that `release.yml` works (never run), the PyPI and TestPyPI trusted-publisher setup (the maintainer's
report; the GitHub environments and branch protection were read back through the GitHub API), wheel installs on Python versions
other than 3.13, the 10^5-report extrapolations (no run reached that size), and the correctness of the gold itself (one author,
with a model third opinion as the second annotation).
