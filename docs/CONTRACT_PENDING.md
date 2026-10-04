# Contract changes awaiting an explicit author line (before G0 freezes)

Status: 2026-10-04. The author's rule: S-01 to S-13 are accepted except where overridden, **but anything that changes the `Report` or `Answer` contract needs an explicit line from the author before G0 freezes.** Each item below is currently built **as design v0.3 specifies** until a line is given. Reply with the item number and `yes`, `no` or an amendment.

| # | Change | From → to | Recommendation | Source |
|---|---|---|---|---|
| 1 | Remove the `confirm` cue | `Report.cue` loses `confirm`; confirmation is always derived from equivalent asserts by another origin group | Yes: one forgeable path fewer | S-01 |
| 2 | Add admission outcome `excluded` | `admit() → admissible \| quarantined \| excluded`; paper `blocked` maps to `excluded` in the compat profile | Yes | S-03 |
| 3 | Add `environment_budget` to `ResourceLimited.reason` | enum gains one value | Yes. Also decide the default budget: design says 12, validated envelope is 7 (see note) | S-06 |
| 4 | Principal kinds and a declarative grant table | `Attr.authority` becomes typed `AuthorityRule`s; principal ids carry a kind (`agent`, `user`, `connector`, `system`); loading refuses any rule granting an agent withdraw/correct over external evidence | Yes: implements your agent-retraction decision | S-07 |
| 5 | `Proposition` nesting depth ≤ 1 | `belief_of(h, belief_of(h2, P))` disallowed | Yes for 0.x; revisit if a use case appears | S-11 |
| 6 | Provenance and `explain` contract | `explain(key, valid_at, mode, depth)` gains `depth` (default full closure); provenance = all subset-minimal environments over **base** reports; `flatten(environments)` must equal the oracle's provenance exactly (G1 criterion) | Yes | S-12 |

**Already decided (not pending):** `belief_as_of` accepts an LSN or a timestamp (S-05; the LSN is a log-row property); `Attr.inertia` stays a boolean (S-08); status enum has five values with `possible` adapter-only (S-04); the hash chain is a storage-layer property, not a `Report` field.

## Disagreements and risks flagged on the S-records

1. **S-02 premise corrected.** "Every report has one source and that source is its origin" is false for the frozen sets (all 500 streams have several sources sharing an origin). The conclusion holds for authority: 0 of 5,505 corrections cross sources within an origin. The fixture asserts that property. Settings 2 and 3 are unverified.
2. **S-06 "collapse first" conflicts with R4.1.** Collapsing changes historical answers in 41% of instances. Treat it as a current-time optimisation only.
3. **S-06 envelope.** The study generator caps keys at 7 reports. With enumeration as the production kernel, a default budget of 12 is exact in principle (enumeration is the definition) but beyond what the study differential-tested; the T-B10 fast kernel, validated to n = 100, is the independent cross-check that makes 12 defensible.
4. **S-12 exact-equality criterion.** Lane E measured provenance differing between the current store and replay on 2.5% of queries. Reaching `flatten = oracle` exactly is real work (task T-B4), not a formality.
5. **S-02 retracted-correction quirk.** Product semantics (a withdrawn correction restores its target) differ from the paper's behaviour; the compat profile needs a flag if the frozen sets depend on it.
6. **Unimplemented in the deposited model:** `until`, `interval` and negative evidence raise `NotImplementedError` (S-09), so they have no oracle. They are excluded from 0.1.
