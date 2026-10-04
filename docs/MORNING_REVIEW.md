# Items for the author's review (running list)

Started 2026-10-05 while the author was away. Each item is something decided by default, a finding that touches a decision, or a risk. Nothing here changes a decision the author already made.

## Needs a decision or an action

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
7. **Git identity:** commits were wrongly attributed to `chris-plorer` (email chris@plorer.ai); history rewrite and force-push are scheduled after the remaining agents finish. `chris@chrisgenai.com` is not yet linked to the chleiva account.

## From the kernel (Lane B): parity result and contract gaps

- **Parity:** kernel vs frozen gold, 500 streams, 30,272 queries: **0 disagreements** under the paper's exact source-level retraction (side table) on all eight slot types; 85 (one class, `source-retract:late-assert`, no unexplained) when source retraction is expanded into per-report withdraws, because the paper's source retraction also removes assertions made after the retraction (106 streams, 241 assertions); 212 under the product semantics `acting_reports_must_be_live=True`.
- **Gap 1 (needs an author line):** `Report` has no field for a `change` cue's `from` value, and 3,593 of 7,135 change reports in Setting 1 need it. The kernel takes it out of band (`change_from`). Recommendation: add an optional `change_from` to `Report` (additive; allowed at 0.x).
- **Gap 2:** the contract cannot express multi-valued *changeable* keys (500 in Setting 1), the cardinality of derived attributes, or explicit `error_allowed` / `competing_values`.
- **Gap 3:** `inertia=False` has no specified semantics (kernel refuses it); the compat profile's inertia-on-everything follows the paper's code and conflicts with the S-08 decision text ("stable keys and sets don't hold"). Needs an author line on which wins for the compat profile.
- **Source retraction late-assert:** G1 under the contract-expressible form is therefore 85 short; G1 under the compat profile uses the side table. Decide whether a source-scope withdraw belongs in the product contract.
