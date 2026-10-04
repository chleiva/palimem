# Contract changes: explicit author lines (all six DECIDED 2026-10-04)

Status: 2026-10-04. The author's rule: S-01 to S-13 are accepted except where overridden, **but anything that changes the `Report` or `Answer` contract needs an explicit line from the author before G0 freezes.** **All six items were answered on 2026-10-04 and are now part of the contract being built.** The table is kept as the record.

| # | Change | From → to | Recommendation | Source |
|---|---|---|---|---|
| 1 | Remove the `confirm` cue | `Report.cue` loses `confirm`; confirmation is always derived from equivalent asserts by another origin group | **Decided yes.** `confirm` removed; "confirmed" is an admission record, not a report cue. | S-01 |
| 2 | Add admission outcome `excluded` | `admit() → admissible \| quarantined \| excluded`; paper `blocked` maps to `excluded` in the compat profile | **Decided yes.** `excluded` carries a reason code and the admission version. | S-03 |
| 3 | Add `environment_budget` to `ResourceLimited.reason` | enum gains one value | **Decided yes; default budget 7** (validated envelope). Raise to 12 only after cross-check vs `oracle_v2` on freshly generated streams, not vs T-B10. Collapsing only for provably interchangeable reports, after an invariance fixture incl. `belief_as_of`. | S-06 |
| 4 | Principal kinds and a declarative grant table | `Attr.authority` becomes typed `AuthorityRule`s; principal ids carry a kind (`agent`, `user`, `connector`, `system`); loading refuses any rule granting an agent withdraw/correct over external evidence | **Decided yes.** Default grant = own source only; the grant table is a versioned admission input. | S-07 |
| 5 | `Proposition` nesting depth ≤ 1 | `belief_of(h, belief_of(h2, P))` disallowed | **Decided yes for 0.x.** Deeper nesting marked reserved in the schema. | S-11 |
| 6 | Provenance and `explain` contract | `explain(key, valid_at, mode, depth)` gains `depth` (default full closure); provenance = all subset-minimal environments over **base** reports; `flatten(environments)` must equal the oracle's provenance exactly (G1 criterion) | **Decided yes.** G1 requires exact provenance equality on the frozen sets. | S-12 |

**Already decided (not pending):** `belief_as_of` accepts an LSN or a timestamp (S-05; the LSN is a log-row property); `Attr.inertia` stays a boolean (S-08); status enum has five values with `possible` adapter-only (S-04); the hash chain is a storage-layer property, not a `Report` field.

## Disagreements and risks flagged on the S-records

1. **S-02 premise corrected.** "Every report has one source and that source is its origin" is false for the frozen sets (all 500 streams have several sources sharing an origin). The conclusion holds for authority: 0 of 5,505 corrections cross sources within an origin. The fixture asserts that property. Settings 2 and 3 are unverified.
2. **S-06 "collapse first" conflicts with R4.1.** Collapsing changes historical answers in 41% of instances. Treat it as a current-time optimisation only.
3. **S-06 envelope (resolved).** Default budget is **7**, the validated envelope. T-B10 is a candidate kernel and cannot validate the enumeration kernel; raising the budget to 12 requires a cross-check against the brute-force global oracle `oracle_v2` on freshly generated streams.
4. **S-12 exact-equality criterion (decided: G1 requires it).** The known 2.5% store-vs-replay difference is on **provenance lists only**; status, values and alternatives are identical on all 40,030 queries. It is not a correctness gap in answers. Closing it is task T-B4 / E1.2.
5. **S-02 retracted-correction quirk.** Product semantics (a withdrawn correction restores its target) differ from the paper's behaviour; the compat profile needs a flag if the frozen sets depend on it.
6. **Unimplemented in the deposited model:** `until`, `interval` and negative evidence raise `NotImplementedError` (S-09), so they have no oracle. They are excluded from 0.1.

7. **Settings 2 and 3 (recorded gap, decided 2026-10-04).** The authority-coincidence check (no correction from a different source of the same origin as its target) is verified for Setting 1 only. When the Setting 2 inputs are re-run through the service for G2, run the same check there and on the Setting 3 Wikidata streams, which have real corrections and shared origins.
