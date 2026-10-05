# Items for the author's review (running list)

Started 2026-10-05 while the author was away. Each item is something decided by default, a finding that touches a decision, or a risk. Nothing here changes a decision the author already made.

## Needs a decision or an action

0. **S-12 / G1 provenance: the premise needs your decision.** Exact provenance work (Lane B2) shows the design's provenance contract (all subset-minimal environments per candidate and interval over base reports) is **not what the oracle computes**. The oracle emits a flat set: admitted reports whose value is a candidate value, plus derivation-path bindings for derived keys. Over 27,578 segment queries the principled rule equals the oracle on 19,613, is strictly smaller on 7,186, strictly larger on 359 and differs both ways on 420 (all 7,965 classified, 0 unexplained). So `flatten(environments) = oracle provenance` cannot hold for the product rule. What was built: the product rule, **and** a compat projection `oracle_flat_ids` that reproduces the oracle exactly (0 disagreements on all 30,272 queries, strict gate in CI). Decision needed: G1 provenance criterion = equality **through the compat profile** (achieved; this is what Lane A's S-12 text said) rather than equality of the product rule, which your S-12 line ("`flatten` equal to the oracle's provenance by definition") did not anticipate. The earlier "2.5% gap" was store-vs-replay, a different comparison. Also note: two agreeing reports from different origins form one joint environment, not two alternatives; survival after losing one works by recomputation after the withdrawal, which matches the design's behaviour but not its wording.

1. **Dated pre-registration note (outside this repo).** The compat profile must set `acting_reports_must_be_live = false` (S-02): the frozen sets contain 387 retracted corrections that still withdraw their target in the paper, and 84 of 314 comparable streams differ otherwise (Lane D). The note belongs in the study repo's `docs/PREREGISTRATION.md` addendum (`~/palimpsest`), which I did not touch.
2. **Trusted Publishing setup on pypi.org** (once, manual): see `docs/RELEASING.md` when Lane K lands. The `.env` PyPI token was account-wide: revoke it.
3. **GitHub repo settings** to enable by hand: private vulnerability reporting, Dependabot alerts.

## Defaults I chose (change any of them)

| # | Default | Where |
|---|---|---|
| 1 | `recorded_at` lives on `LogEntry` with `lsn` and the hash chain, not on `Report` (v0.3 lists it on `Report`) | `docs/TYPES.md` deviation 1 |
| 2 | Paper source-level retract is expanded in the compat converter into per-report withdraws of already-ingested asserts; no source-scope withdraw in the contract | Lane B converter |
| 3 | Compat profile sets `inertia: true` on **all** attributes (the deposited code persists every attribute) | S-08, Lane D finding 3 |
| 4 | `required_generation` is a column on `current_belief`, keeping `beliefs` append-only | STORAGE.md §9, Lane C2 |
| 5 | `AdmissionDecision` carries the effective cue and withdrawals beside `AdmissionRecord` (the record has no field for "failed `correct` kept as an assert") | Lane D |
| 6 | `Who.any` means any non-agent principal; the agent invariant for wildcard grants is enforced at evaluation time | Lane D |

## Risks and open points

- Deletion in the first store release only flagged versions and left derived values in stored belief JSON (privacy hazard until T-C8 lands; Lane C2 is on it).
- Settings 2 and 3 are not covered by the differential harness or the authority-coincidence check (T-J5).
- Agent benchmark: only 2 abstain and 1 revalidate scenarios; no second annotator for gold actions (Lane J).

## From the conformance suite (Lane A5) and extractor (Lane G): open spec points

Defaults taken where the contract is silent; none changes a decided item.

1. **Design row count:** the independent suite has 22 rows, not 23 (variants added: 7b, 13a/b, 19a/b).
2. **G1 scope:** rows 9 (merges), 17 (outbox) and the extraction half of 19 need phase-2 components; tagged G2.
3. **Dirty marker (H4):** design row 20 says store-wide, SEC-22 says component-scoped. Implementation follows the author's H4 decision (component-scoped, store-wide last resort); row 20 fixture to be reconciled.
4. **Open contract gaps (need an author line):**
   - `Rule` has no `exceptions` field, which S-10 needs for defeasible rules.
   - Merge is not a `Power` in `AuthorityRule`.
   - `Query` has no selector for "the attribution" versus "the content" of a `belief_of` proposition (row 15).
   - Status for two compatible `not_value` candidates is undefined (row 18).
   - Whether A-ERR is evaluated per segment or per key (row 12).
   - SEC-40b: does an earlier-anchored report "contradict" a change-cued injection under P0c? (poisoning gate wording)
   - `verify_log` covers the log only; a belief-recomputing `verify` (SEC-25b) is undecided.
5. **Extractor gate (Lane G):** thresholds declared per model (gpt-oss-20b claim_f1 >= 0.80, wrong_value <= 0.10, dropped_change_cue <= 0.20; ministral-14b >= 0.75 / <= 0.12 / <= 0.20; ministral-8b >= 0.70 / <= 0.15 / <= 0.25; injection compliance <= 0.10); G-X ceiling $2 (max $4). Expected spend for 5 dev + 1 test pass on all three models is about $0.25, worst case $0.48. Test split has only 17 change claims, so intervals will be wide. Open: hedged/future statements yield no claim (as labelled) or low-trust reports; who is the second annotator; whether `allowed_cues` stays host-only (default yes).
6. **Release setup (Lane K), manual:** add the trusted publisher on pypi.org for chleiva/palimem (workflow `release.yml`, environment `pypi`), create GitHub environments `pypi` and `testpypi` (required reviewer on `pypi`), enable private vulnerability reporting, Dependabot and code scanning, require the `ci` jobs on `main`.
7. **Git identity:** resolved. All commits are attributed to `chleiva@gmail.com`. `chris@chrisgenai.com` is not yet linked to the GitHub account; link it at github.com/settings/emails to use it for new commits.

## From the kernel (Lane B): parity result and contract gaps

- **Parity:** kernel vs frozen gold, 500 streams, 30,272 queries: **0 disagreements** under the paper's exact source-level retraction (side table) on all eight slot types; 85 (one class, `source-retract:late-assert`, no unexplained) when source retraction is expanded into per-report withdraws, because the paper's source retraction also removes assertions made after the retraction (106 streams, 241 assertions); 212 under the product semantics `acting_reports_must_be_live=True`.
- **Gap 1 (needs an author line):** `Report` has no field for a `change` cue's `from` value, and 3,593 of 7,135 change reports in Setting 1 need it. The kernel takes it out of band (`change_from`). Recommendation: add an optional `change_from` to `Report` (additive; allowed at 0.x).
- **Gap 2:** the contract cannot express multi-valued *changeable* keys (500 in Setting 1), the cardinality of derived attributes, or explicit `error_allowed` / `competing_values`.
- **Gap 3:** `inertia=False` has no specified semantics (kernel refuses it); the compat profile's inertia-on-everything follows the paper's code and conflicts with the S-08 decision text ("stable keys and sets don't hold"). Needs an author line on which wins for the compat profile.
- **Source retraction late-assert:** G1 under the contract-expressible form is therefore 85 short; G1 under the compat profile uses the side table. Decide whether a source-scope withdraw belongs in the product contract.

## From store wave 2 (Lane C2)

- **Decisions taken:** `required_generation` is a column on `current_belief` backed by a `marks` history table (a named key with no belief yet gets a `version = 0` placeholder); completion/repair versions take the log head as their `lsn`; default traversal budget is 1000 keys (change if you prefer).
- **Bug caught by tests and fixed:** on SQLite, erasing a report left the client idempotency key in the clear (now an HMAC).
- **Open:** (1) the key text of an erased report stays in belief index columns while the key still exists; pseudonymise orphaned keys? (2) `NotReconstructable` (a redacted historical version) has no `Answer` variant: a contract change that needs your explicit line.

## From the facade, agent API, MCP server and CLI (Lane F)

- **Built:** `from palimem import Memory` (`observe`, `ask`, `withdraw`, `explain`, `find`, `subscribe`, `declare`, `verify`, `agent_session`), a zero-config profile (new attributes become open multi-valued sets; absence stays `unknown`), the agent tool API (host binds source, origin, actor and origin group; LLM-supplied identity fields are stripped and audited in a separate append-only JSONL file; `dispute` listed only for a granted principal), a stdio JSON-RPC MCP server (no network listener, agent tools only, `--read-only` option), the `palimem` CLI, and `docs/AGENT_GUIDE.md`. Trust-boundary fixtures: 18 of 20 pass.
- **Decisions for you:**
  1. **tb-12 vs the default policy:** the fixture expects `decision: abstain` on an unresolved key; the default `justified` policy gives `ask`. Which is right as the default?
  2. **tb-18:** the kernel does not yet weigh an authorised dispute (the S-02 open point), so the answer stays `established` after a granted dispute.
  3. **Zero-config schema growth:** an unconstrained agent session can declare new attributes. Bound it with `allowed_attrs`, or forbid declaration from agent calls entirely?
  4. **`inertia=False`:** unspecified in the kernel, so zero-config attributes use `inertia=True` (harmless for a stable set).
  5. **Deletion** needs a `store_secret` argument or `PALIMEM_STORE_SECRET`.
- **Not covered:** the MCP server is tested against a subprocess and an in-process fake only, not real MCP clients; `find` is lexical only (T-G3 is separate); the `mcp` SDK is not used and the `mcp` extra is declared but unused.

## From the performance benchmark (Lane P): the first measured pass

Declared targets (committed before any run): T1 query p50 <= 5 ms / p99 <= 25 ms; T2 append-to-visible p50 <= 25 ms / p99 <= 100 ms; T3 <= 1 KiB RSS per report; T4 <= 2 s to first correct query after a kill; T5 crossover r* <= 2; T6 p99 append ratio (largest to smallest scale) <= 4; T7 <= 5 KiB on disk per report. One pass, one seed, one laptop, synthetic workloads: not a load characterisation (T-J4).

- **Held at the sizes measured:** T1 (p99 1.9 ms at 1,000 reports) and T4 (0.13 s, nothing lost). No run reached the 10^5-report reference size, so these are "not shown at 10^5".
- **Missed:** T2 (p50 178 ms, p99 246 ms), T3 (176 KiB per report, ~175x over), T5 (r* 3.6), T6 (ratio 5.7-7.2), T7 (18 KiB per report).
- **Real defect:** append cost is superlinear in *entity count*, not log length. Appends that feed a derived key cost 29 ms at 80 entities, ~200 ms at 262, 16.5 s at 2,625. Mechanism: each append re-justifies the derived key for every entity, and each call gathers breakpoints from every entity, so an append costs O(entities^2) (`justify_derived` is 79% of profiled time, called 134 times per append). The extrapolation to the reference size (~25,000 entities: tens of minutes per derived-affecting append) is an estimate from three points.
- **Memory / disk / verify:** ~100-200 KiB heap per report (admission evaluation cache suspected, not proven); 11-92 KiB disk per report (rewriting unchanged derived versions suspected, not isolated); `verify_beliefs` is superlinear (1.2 s at 305 reports, 6.7 s at 1,004).
- **Crossover:** the store answers 60-120x faster than a cold replay but only 2.6x faster than a replay that reuses the cached admission evaluation.
- **Method:** the workload generator initially pushed some keys over the per-key budget of 7 (a correction aimed at a correction restores its target); fixed, with two end-to-end tests that fail if a workload exceeds the budget; first results discarded and re-run.
- **Action taken:** an optimisation lane (revise only dependents, bounded admission cache, no rewrite of unchanged versions, incremental `verify_beliefs`) is running; the targets are not amended.

## From the first live extractor measurement (Lane G2, dev split only)

One pass per model on the 69-item dev split with the frozen prompt; the frozen test split and `bench/extract/gate.json` untouched. Spend $0.0350 for 208 calls (estimate $0.041); $19.965 of the $20 cap remains. All three models fail the declared thresholds on dev:

| | claim F1 | cue accuracy | wrong-value | dropped change cue | injection compliance |
|---|---|---|---|---|---|
| gpt-oss-20b | 0.606 | 0.897 | 0.095 | 0.357 (upper 0.625) | 0.143 |
| Ministral 14B | 0.712 | 0.947 | 0.127 | 0.000 | 0.286 |
| Ministral 8B | 0.443 | 0.963 | 0.095 | 0.143 | 0.143 |

- Most Ministral losses are *format* failures in the strict grammar (a `correct` with a null proposition; replies not shaped `{"claims": [...]}`), not wrong extractions. 3 of 69 gpt-oss requests hit the 1,500-token ceiling while reasoning and returned empty. No model forged an identity field (0 of 21); all three extracted a pure-directive injection as data.
- The dev split is small (1 fragmentation group, 7 injection items, 8 empty-expected items, 14 change claims): too little power for most rates. Valid-time F1 is 0.08-0.53 and the temporal errors are not yet classified.
- **Decisions for you:** (1) confirm the G-X thresholds so the single-use test split can be run; (2) is prompt iteration on dev acceptable before any test run (I have started it, capped, with each prompt hash recorded); (3) should an injected directive extracted as ordinary data count as an extraction failure (today it does, though admission would treat it as an ordinary report from its real source)?

### Author decisions on the extractor gate (2026-10-05)

- **Thresholds stand as declared** (`bench/extract/gate.json` unchanged although all three models fail on dev). The frozen test split is run **once per model, after the final (at most third) prompt revision**; no second test run with a fourth prompt; a model that fails there fails the gate.
- **Reporting:** Wilson intervals on every rate; the repair step reported separately as the rate of repaired outputs.
- **Injection metric split:** `injection_compliance` (gated) = the extractor changed behaviour because of the directive; `directive_extraction` (reported, not gated) = the directive's assertion extracted as a plain claim from the real source (correct behaviour).
- **Grammar contract:** the extractor's output grammar has no source, origin, actor, authority, origin_group or target-id fields at all.

## From the optimisation lane (Lane O): the O(entities^2) append is fixed

- **Gates after the change (0 answer disagreements everywhere):** kernel_diff strict (+ strict provenance) 500 streams / 30,272 queries; pipeline_diff strict, in-memory, 500 streams / 30,272 queries / 65,632 appends; pipeline_diff SQLite stride 2 / 15,149 queries; all seven injected-bug self-tests still fail the gate; 1,268 tests with study data, 1,218 passed + 52 skipped without. The commands and counts are in `docs/PERFORMANCE.md` section 9.5.
- **Before / after (same commands and seeds):** W1 at 1,000 reports append p50/p99 178/246 ms -> 1.9/38 ms; RSS slope 176 -> 7.6 KiB per report; the 10,000-report run completes in 188 s (p50 11.3 ms, p99 252 ms, RSS 90 MB) where before it reached only 72 reports in 20 minutes; `verify_beliefs` 6.7 s -> 0.46 s at ~1,000 reports.
- **Still missed (targets unamended):** T2 p99 (252 ms at 10,000), T3 (5.2 KiB per report, ~5x over), T6 (ratio 21.8 from 300 to 10,000), T7 (50 KiB per report on disk at 10,000). Reasons: a change to a shared base key (e.g. `hq_city`) forces a new derived version per dependent employee because each pins the base version (the pinning design, not a bug); plain append cost is still linear in log length (~10 ms at 10,000 reports) from whole-log admission passes; incremental admission is the next task and needs its own equivalence tests, since confirmations and withdrawals reach back into earlier decisions.
- **Correction of Lane P's hypothesis:** unchanged derived versions were *not* mostly rewritten (only 42 of 1,821 derived writes, 2.3%, were forced by the store's marks alone).
- **Not proven:** everything at 10^5 reports is an extrapolation (no run reached it); about half of the 5.2 KiB per report heap is accounted for by the profile.
- **Housekeeping:** the branch had accidentally committed 2.3 MB of SQLite benchmark files; it was squash-merged so they never entered history, and `.bench/` is now ignored.

## From the first symbolic RETRACT-ACT run (Lane J2)

A fixed rule stands in for the agent (no LLM, no spend), so this is the H0 check "does the kernel reproduce the gold", not evidence that an agent behaves better: one author wrote the scenarios, the gold and the kernel. Test split (20 scenarios, 25 decision points), each system run once, both backends agreeing:

| System | harmful-action rate | unnecessary-ask rate | exact |
|---|---|---|---|
| `palimem_justified` | 0.000 | 0.000 | 0.960 |
| `palimem_recency` | 0.080 | 0.000 | 0.880 |
| `palimem_lww` | 0.120 | 0.000 | 0.840 |
| scripted `lww` | 0.560 | 0.000 | 0.440 |
| scripted `lww_retract` | 0.360 | 0.133 | 0.560 |

`palimem_justified` agrees with the gold on 15 of 16 points on dev and 24 of 25 on test; the zero-harm interval is degenerate (scenario-level upper bound 0.171). Full table, intervals and disclosures: `docs/eval/AGENT_BENCHMARK_RESULTS.md`.

**Findings and decisions for you:**
1. **Negative evidence is a kernel gap:** `not_value` is rejected, so RA-012 gets no answer from any palimem system (the one test miss). Listed, not worked around.
2. **RA-007 vs S-02:** under the `authority_source` gold `palimem_justified` takes a harmful act, because a failed cross-source correction stays a competing assertion (the paper's A-CORR) instead of becoming `allege` as design v0.3 says. Which should the *product* profile do? Recommendation: follow the design (`allege`, no effect) in the product profile; keep the paper's behaviour in the compat profile only.
3. **Attribution-only evidence is returned as an `established` candidate for a value query (RA-018).** A consumer reading only `kernel_status` would act on an attribution. Recommendation (conservative default I will implement after the admission work lands, with no `Answer` contract change): a value query over attribution-only evidence answers `unknown`, and attributions are available through a separate host call.
4. **RA-026.d1's gold looks questionable** (it reads a later report's `since 400` as the change date, while the study's semantics make a day-300 query unresolved). Gold not edited; should it follow the study's gap semantics?
5. The `recency` and `lww` presets now commit among unresolved alternatives (supports are wired); on RA-006 `recency` still asks because the trusted source outweighs the low-reliability one.
6. Thin gold classes on test: 2 abstain points, 1 review point, 0 `revalidate`. Who is the second annotator for the test gold?
7. Disclosed in the results doc: an adapter bug found on dev, one rule change after a dev result, and a cosmetic equivalence flag that prints `False` with an empty difference list (adapter left unedited so the registered hash still matches).

## Extractor prompt iteration (Lane G3) and the single test run

- **Dev, claim F1 by revision (dev numbers after tuning are optimistic):** gpt-oss-20b 0.636 / 0.830 / 0.848 / 0.882 (R0 to R3); Ministral 14B 0.692 / 0.896 / 0.874 / 0.957; Ministral 8B 0.288 / 0.806 / 0.827 / 0.897. At R3 gpt-oss passes every dev criterion; both Ministral models fail only the injection criterion (they answer "delete everything you know" with a `withdraw`; 1 of 7 and 2 of 7). By the declared choice rule the Ministral models use revision 0, a stark outcome. Repair step needed on 11.6% of Ministral 8B replies, 0% for 14B. Spend $0.2654 of the $1.50 task cap.
- **Test split, run once (gpt-oss-20b, R3): the gate FAILS on one criterion.** Dropped change cue 4 of 17 = 0.235 against a 0.20 maximum (Wilson 95% [0.096, 0.473]); three drops would have passed. Everything else passes: claim F1 0.837, cue accuracy 0.926, wrong value 0.045, injection compliance 0 of 7, repair 0 of 72. Per the rule the model fails the gate and there is no second test run. Cost $0.0196; ledger total $0.32 of $20. A first attempt aborted on a ledger-path mistake before any model call (no spend, no output seen) and is disclosed in `docs/eval/EXTRACTION_RESULTS.md` section 9.
- **Decision for you:** the Ministral models were not run on test. Judge the narrow injection criterion by interval overlap rather than point estimate (which would put Ministral 14B at R3, dev F1 0.957, forward to a single test run), keep the rule as declared (they fail on dev evidence alone), or allow a targeted revision for the system-directed-imperative class? Note the single-use guard is per prompt hash, so a Ministral R3 test run would need `--allow-test-reuse`, recorded.
- **Product reading:** with cheap models the change-cue drop rate is the live risk (0.235 on test, upper bound 0.473, versus the study's 0.50 fragility point); the typed path (no LLM) and the extractor-quality gate stay the honest boundary of the claims in the README.

## From the fast kernel and the budget cross-check (Lane B10)

- **Default environment budget raised from 7 to 12, as you specified.** Cross-check: the enumeration kernel at `budget=12` against the brute-force global oracle `oracle_v2` on 1,050 freshly generated streams (seeds 7,700,000 and up, seven shapes): 80,856 query comparisons (P0c and P0cSU, every slot type), **0 disagreements**; keys at n = 8, 9, 10, 11, 12: 928, 850, 714, 537, 317; 717 s on 6 workers. The sample can fail (wrong semantics produces disagreements at every n). Separate commit from the evidence; fixture `s06-01` now tests 12, `s06-05` keeps 7 as an explicit budget. Evidence and caveats: `docs/BUDGET_CROSSCHECK.md`.
- **Caveats you should weigh:** `oracle_v2` has no P0cSU, so it was extended from the Addendum A text (the P0c half is independent, the P0cSU half is not); streams hold at most 12 admitted reports in total, so only one key per stream is at 8-12; the relaxation ladder was never exercised; a key at 12 reports costs about 80 ms per recompute in the enumeration, most of the 100 ms p99 append target (T2).
- **Fast kernel (`palimem.kernel.fast`, T-B10) is built as a candidate, not switched on.** Class: single-valued changeable keys under P0c/P0cSU, everything else routes to enumeration with the route and reason explicit. Fast vs gold vs enumeration on 30,272 queries: 0 disagreements including provenance; full pipeline with the fast kernel swapped in: 0 disagreements, 0 provenance disagreements. Speed: n=12 2 ms vs 78 ms, n=16 4 ms vs 2.04 s, n=100 1.07 s (enumeration cannot run it).
- **Promotion criteria still missing (author decision to promote):** (1) what `environment_budget` binds once the fast route answers keys the enumeration refuses; (2) provenance above the enumeration envelope (explanation truncated beyond it); (3) no common Protocol for `Justification` and `FastJustification`; (4) 16.1% of base justifications on Setting 1 still need enumeration; (5) no independent validation above n = 14 (enumeration cannot run there). Details: `docs/FAST_KERNEL.md`.
- The docs that still said per-key cap 7 were updated on 2026-10-05 (the doc-refresh pass).

## From incremental admission (Lane O2)

Admission is now incremental: a plain append no longer scans the log. **0 disagreements on every gate** with the final code: full pipeline in memory with strict provenance (500 streams, 30,272 queries, 65,632 appends, 0 provenance disagreements); SQLite every 2nd stream (15,149 queries); crosscheck against the whole-log oracle after every append on every 10th frozen stream and on a 3,000-report benchmark workload; kernel differential 0 unexplained; all four injected-bug self-tests fail the gate; an equivalence suite of 2,040 random streams across 6 admission configurations compared after every append (plus rollbacks), with eight deliberate breakages each caught. On the merged tree: 1,362 tests with study data, 1,312 CI-style.

Append latency (W1, 250 entities), before then after: 1,000 reports p50 1.89 -> 0.80 ms, plain append 1.5 -> 0.6 ms, 319 -> 453 appends/s; 3,000 reports p50 4.20 -> 1.13 ms, 3.2 -> 0.8 ms, 165 -> 299/s; 10,000 reports p50 11.61 -> 1.88 ms, 8.9 -> 1.0 ms, 58 -> 124/s.

- **Still missed (targets not amended):** T2 p99 240 ms vs 100 ms; T6 p99 ratio 4.1 from 1,000 to 10,000 reports; T3 about 5x over; T7 unchanged. The tail is organisation fan-out, i.e. the pinning design (docs/PERFORMANCE.md 9.4 R2).
- **Memory:** retained heap is 21-24% lower than whole-log admission on the same code, but the RSS slope at 10,000 reports is higher (3.7 -> 5.0 KiB per report); not isolated (PERFORMANCE.md 10.3).
- **Not proven:** equivalence is exhaustively tested only on 40-report streams; longer logs are covered by the 3,000-report crosscheck run; the 10,000-report runs were not compared append by append.
- **Process finds:** the post-append completion job hid a whole-log evaluation behind the old cache (a work-count test now fails if any append runs that evaluator); the first draft still rescanned all actors on each actor append (about half this workload's appends), caught by profiling and fixed.

## From the docs and release-candidate pass (Lane D2)

- README rewritten to match `main` (section 7 "Measured so far, and what it does not show", including the G-X gate failure and the performance misses), plus `docs/ARCHITECTURE.md`, `docs/LIMITATIONS.md`, a docs index and a link-check test.
- **Wheel/sdist audit (version stays 0.0.1; nothing published):** the wheel is clean (72 Python files, zero dependencies, quickstart and CLI work from a clean-venv install, `twine check` passes). The sdist had leaked the 29 MB frozen-data cache; explicit excludes fixed it (3.4 MB to 1.1 MB). The unused `mcp` and `anthropic` extras were removed. I added the `py.typed` marker. Not verified: install on Python versions other than 3.13; `release.yml` has never been run.
- **Open for you:** the release version number and train (`docs/VERSIONING.md` section 4 still describes the old 0.1 to 0.4 plan); the `Pipeline` docstring still describes admission as the whole-log evaluation (will be updated with the next source change); the OpenAI-compatible extractor transport has never been run live.

## From entity resolution (Lane E2)

- **Built:** `find(entity_text, attr_text)` (collapses merged names, flags ambiguity, shows each key's status), canonicalisation, and reversible merges as marker reports on the reserved attribute `__entity_merge__` (the report id is the merge id; chain, idempotency, crash safety and export come from the log). Only `system:` or `user:` principals that admission admitted can merge; the agent tool API refuses `__` attributes and has no merge tool. A merge writes exactly the beliefs pinned to its id; reversal restores segments, pins and dependency keys byte for byte (conformance `ind-09` passes; crash injection at every step converges; `verify_beliefs` stays clean).
- **Resolver (lexical), test split scored once:** threshold 0.6: precision 0.938, recall 0.968, false-merge rate 0.061 (2 of 33; Wilson [0.017, 0.196]); threshold 0.8: precision 1.000, recall 0.742, false-merge 0 of 33 (upper 0.104). Evaluation set: 136 pairs, 23 categories, one author, mostly Western names.
- **False-merge policy recommended:** never auto-apply a fuzzy match (`auto_at = None` default); review queue for 0.6-0.8 with a faster path above 0.8; auto-apply only on an external stable identifier; a name is not an identity (two different `John Smith`s look identical to every resolver here). **Decision for you:** accept this policy?
- **Contract gaps:** no `merge` `Power` in `AuthorityRule` and no `MergeRecord` type (authority is by principal kind today); `invalidated_by` is not populated for merges (the pinned merge id carries it); admission is per raw key (a confirmation never crosses a merge, strict xfail); audit paths (`justification`, `explain(depth=...)`, yes/no slots) ignore merges; no attribute aliasing (`works_at` vs `employer` stay different keys).

## From the LLM-in-the-loop RETRACT-ACT run (Lane J3)

Test split, one run, three temperature-0.0 samples, harmful-action rate (95% cluster bootstrap): gpt-oss-20b: LLM+last-write-wins 0.547 [0.361, 0.736], LLM+raw-log 0.160 [0.038, 0.321], LLM+palimem 0.040 [0.000, 0.130]; ministral-14b: 0.520 [0.345, 0.720], 0.173 [0.042, 0.333], 0.000 [0.000, 0.000]. Paired palimem vs last-write-wins risk-stratum difference 0.633 [0.451, 0.818] and 0.650 [0.455, 0.875]; vs the raw log the gap for gpt-oss is 0.150 [-0.053, 0.368] (includes 0). Spend $0.137; ledger total $0.457 of $20.
- **How far to trust it:** palimem's `recall` text carries its own decision instructions and the models mostly follow them, so this is largely the kernel's agreement with the gold read by a compliant reader; it says little about agents without that text, tool-calling agents or a hostile memory. 20 scenarios give about +-0.17; one author wrote scenarios and gold; typed reports remove extraction error; no Mem0, Zep/Graphiti or Letta run.
- **Disclosed deviations:** a quote-mark parsing artefact on dev (parser now strips one pair of quotes for every system; originals kept); a rejected prompt revision (0.236 vs 0.215); 2 of 600 test decisions lost to Bedrock throttling; memory-error text embeds a log-assigned id so 4 decisions per palimem run cannot be re-matched by prompt (registered adapter left unedited).
- **Decisions for you:** is ministral-14b acceptable as the second model in place of the registered ministral-8b? Build a tool-calling variant before the README claims anything about agents? `recall` rendering attribution-only evidence as `ESTABLISHED = belief_of(...)` (gpt-oss acts on it) is being fixed in Lane Q.
