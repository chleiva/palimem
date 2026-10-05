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
