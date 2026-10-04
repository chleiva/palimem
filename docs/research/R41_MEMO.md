# R4.1 spike memo — is the exponential core avoidable?

Task T-I1 · Lane I · 2026-10-04 · code in `research/r41/`, tests in `tests/research/`, raw numbers in `research/r41/results/*.json`

## 1. Recommendation: **PARTIAL**

| Question | Verdict |
|---|---|
| Build storage and the kernel around *enumerated environments / interpretations* (the design's 2^n ATMS plus an n ≤ 12 cap)? | **NO-GO.** The interpretation set is inherently exponential even when the answers are not (§3.4). Storing it, or capping n to keep it small, solves the wrong problem. |
| Is there an exact, fast algorithm for the dominant case (single-valued, changeable key; same-key disputes; change/correction cues; A-SELF/A-SU)? | **GO.** An existence-query kernel, polynomial O(n³) when there are no corrections or same-anchor conflicts and FPT in (#corrections + #conflicted anchors) otherwise. It matched `oracle_v1` on **34,168** instances with **0 disagreements** (§3.2), and is 600× faster at n = 16 (§3.3). |
| Does equivalence collapsing rescue enumeration? | **NO.** It halves n on realistic re-assertion logs (not enough: 30 sessions still leave ~27 nodes), and it is not answer-preserving for historical times (§4). |
| Multi-valued keys (CONT/ENDED)? | **Contract problem, not an algorithm problem.** The *answer itself* (alternatives as a list of sets) is 2^n − 1 long (§5). Needs a factored answer representation. |
| Provenance (all minimal environments)? | **Open.** Not solved here; a compilation route is sketched (§6) but unbuilt and unverified. |

## 2. What the oracle computes and why it looks exponential

`oracle_v1._key_interps` loops over every subset T ⊆ reports (`range(1 << n)`) marking the rest ERR. An interpretation is `(ERR set, timeline)`. For a single-valued changeable key the timeline is a function of T, so **interpretations ↔ admissible sets T, one-to-one.** The replay oracle is therefore also 2^n: the design's "overflow to a per-key replay path" does not escape the exponential.

Everything the study consumes from such a key is a *question about the family of admissible T*, not the family itself:

- `cand(t)`: the union, over admissible T, of the candidate value-sets at valid time t (this is what `gold.key_provider` builds, and what derived keys and `holds` queries read).
- `erroneous(o)`: can some admissible T contain o, can some omit it?
- `changed(v1, v2)`: does some admissible T have a v1 run followed by a v2 run, and does some not?
- provenance ids: derived from the admitted list and the candidate values, so trivial once `cand` is known.

## 3. The algorithm

### 3.1 Structure (this is the "what makes interpretations factor" answer)

For a single-valued, changeable key, T is admissible iff:

- **C1 (explanation, a dominating-set condition).** Every report o ∉ T has an explainer p ∈ T with E(p, o), where
  `E(p,o) = (p is a correction targeting o) ∨ (value(p) ≠ value(o) ∧ competing_values ∧ Q(o,p))` and
  `Q(o,p) = anchor(p) ≥ anchor(o)` if o's cue is shielded (P0c: `change`; P0cc: also `correction`);
  `Q(o,p) = origin(p) ≠ origin(o) ∨ anchor(p) ≥ anchor(o)` under A-SU (P0cSU) for an unshielded o;
  `Q = true` otherwise.
  *(Derivation of Q under SU: the oracle requires (A) some unshielded different-valued competitor and (B) not all different-valued competitors are same-origin-and-earlier. For a shielded o, A's witness has anchor ≥ anchor(o) and so satisfies B; for an unshielded o, B implies A. So the conjunction collapses to the single relation above.)*
- **C2.** No correction in T targets a member of T.
- **C3.** A change-from report p ∈ T has an earlier report of the `from` value in T, whenever any such report exists in the log.
- **C4.** No two different values share an anchor inside T.

Three facts make this tractable:

1. **C1 is monotone in T** (adding reports only adds explainers, and shrinks the set that needs explaining). E depends only on the pair (o, p), never on the rest of T.
2. **C3 has a unique greatest solution inside any allowed set A** (support is a positive existential and the supporter is strictly earlier, so one ascending-anchor pass computes it; unions of C3-closed sets are C3-closed).
3. C2 and C4 are the only *conflict* constraints. They are pairwise and touch only corrections and same-anchor ties.

**Lemma.** Fix which corrections lie in T (`Cin`) and which value wins each tied anchor. Let A be the reports compatible with that choice (and with the query's exclusions X) and T* the greatest C3-closed subset of A. If *any* admissible T ⊆ A with T ⊇ I exists, then T* is admissible and T* ⊇ I.
*Proof.* T ⊆ T* by fact 2. Every o ∉ T* is also ∉ T, so it has an explainer in T ⊆ T*; fact 1. C2 and C4 hold by construction of A. ∎

So "is there an admissible T with I ⊆ T and T ∩ X = ∅?" is decided by computing T* once per branch (O(n), using per-value/per-origin counters and the latest-anchor pair to test E for every o in O(1)), and branching only over `Cin` (2^c) and the winning value at each tied anchor (∏ #values). Dropping a tied anchor entirely is dominated by choosing a value there (T* is monotone in A).

Queries as existence checks:

- **cand(t).** The candidate set at t depends only on the last T report at or before t (value u) and the first one after (value w): `{u}` if the last one is at t, or nothing follows, or u = w; `{u},{w}` otherwise; `{∅}` if nothing precedes. Feasibility of a pair (p, u, q, w) is `feasible(include_any = reports at those anchors with those values, exclude = every report strictly between)`. The table of feasible pairs is computed once (O(m²) pairs) and answers every t. *(Bug found and fixed during the spike: the boundary report must be "at least one of the same-valued reports at that anchor", not a fixed representative; a same-valued sibling can be the only admissible choice. Pinned by the differential tests.)*
- **erroneous(o)**: `feasible(include={o})`, `feasible(exclude={o})`.
- **changed(v1, v2)**: yes iff some pair p(v1), q(v2) with anchor(p) < anchor(q) is jointly feasible; "no" by a cut over anchors.

### 3.2 Correctness evidence (exactness against `oracle_v1`, including its totality ladder)

| Test | Instances | Mismatches |
|---|---|---|
| Random adversarial keys, n ≤ 10, 4 policies (P0, P0c, P0cc, P0cSU), tied anchors, repeated values, cross-origin corrections, change-from cues, `error_allowed=False`, `competing_values=False`; compared on cand(t) for every t, erroneous(o) for every report, changed(v1,v2) for every ordered value pair | 6,000 | **0** |
| REVISE-STREAM generator output (300 streams, 3 belief times each, every observed single-valued changeable key, P0c and P0cSU, n ≤ 7 = the generator's cap) | 28,168 | **0** |
| n = 11, 12 spot checks (`tests/research`) | 12 | **0** |

Ladder coverage in the random set: level 0 in 5,566, level 1 in 6, level 3 (reject everything) in 428, **level 2 in 0**. Level 2 is implemented but not exercised.

### 3.3 Scaling (full cand table + erroneous for every report, P0cSU, distinct anchors, cue-rich, no corrections)

| n | `oracle_v1` | fast kernel |
|---|---|---|
| 12 | 66 ms (measured) | 1.6 ms |
| 16 | 1.99 s (measured) | 3.4 ms (≈ 580×) |
| 24 | ≈ 380 s (**extrapolated** at 2^n; not measured) | 10 ms |
| 50 | ≈ 2.6 × 10^10 s (extrapolated) | 0.10 s |
| 100 | ≈ 3 × 10^25 s (extrapolated) | 1.0 s |
| 200 | n/a | 12 s |

Growth is roughly cubic (pairs × O(n) per check). A faster version is straightforward (incremental window sweeps) but was not needed to answer the question.

Where it breaks (n = 30, median of 3):

| Corrections c | branches (max) | time |
|---|---|---|
| 0 | 556 | 20 ms |
| 8 | 8,450 | 51 ms |
| 12 | 116,727 | 0.30 s |
| 14 | 369,639 | 1.0 s |

Tied anchors g = 12: 19,259 branches, 59 ms. Branches grow exponentially in c and g as designed; the time stays small because success short-circuits. I did not prove NP-hardness for unbounded corrections; the honest statement is **FPT in c + g**, with the exponential visible above. A realistic log has few cross-origin corrections per key.

### 3.4 What is inherently exponential: the interpretation set, not the answers

n reports with distinct values at distinct anchors, no cues: **every non-empty T is admissible**, so the oracle has exactly **2^n − 1** interpretations (checked n = 2…12), while the answer at "now" has n candidates and the fast kernel returns the same n. Any design that stores or enumerates interpretations (or environments per interpretation) pays 2^n for no benefit in the answer.

## 4. Equivalence collapsing (T-B6)

Collapse = merge maximal runs, in anchor order, of reports with the same value and origin, no cue, not corrected and not a correction.

**Effect on n** (simulated agent re-assertion logs, 200 per cell; `burst` = probability a session repeats the statement; mean n):

| sessions | burst | raw | same-origin blocks | same-value blocks (any origin) |
|---|---|---|---|---|
| 10 | 0.35 | 16.1 | 10.2 | 5.3 |
| 30 | 0.35 | 48.9 | 27.1 | 9.3 |
| 60 | 0.35 | 98 | 52 | 14 |
| 120 | 0.35 | 195 | 102 | 27 |

Even ten sessions with some repetition exceed the study's own n ≤ 12 cap before collapsing (mean 16–20 at burst 0.35–0.7). After same-origin collapsing, 30 sessions still leave ~27 nodes: 2^27 enumeration remains infeasible. Collapsing by value across origins is the only variant that brings n near the number of distinct runs, and it merges evidence that A-SU and confirmation distinguish, so it is a semantic decision, not an optimisation.

**Is it answer-preserving?** On 194 instances with at least one multi-report block (n ≤ 11, two origins, P0c/P0cSU), comparing candidate sets at every valid time:

| Variant | identical at every t | identical at "now" (after all reports) |
|---|---|---|
| tie labels (each block entirely TRUE or entirely ERR) | 115 / 194 (59%) | 194 / 194 |
| keep only the first report of each block | 174 / 194 (90%) | 193 / 194 |
| keep only the last report of each block | 86 / 194 (44%) | 194 / 194 |

Collapsing is therefore nearly safe for "now" questions and unsafe for `asof` / `belief_asof` questions, because the partially-labelled blocks are exactly the interpretations that create "unknown change point inside the block" ambiguity. A concrete counterexample is pinned in `test_naive_collapse_is_not_answer_preserving_for_historical_times`. **Conclusion:** collapse is a semantics change (which the design may choose to adopt as "one claim per origin run"), not a tractability fix. The existence-query kernel is the tractability fix and does not need collapse.

## 5. Multi-valued keys (CONT/ENDED)

What breaks:

1. **The answer itself is exponential.** n distinct values at distinct anchors: interpretations = (3^n − 1)/2 (4, 13, 40, 121 … 29,524 at n = 10); the alternatives list at "now" has **2^n − 1** sets (n = 10: 1,023). The contract's `alternatives: [Candidate]` with set-valued candidates cannot be produced in polynomial time by *any* algorithm. The design's own "multi-valued sets list `members`; alternatives are candidate sets" has to change for this class.
2. Derived multi-valued keys inherit it: a strict rule copying a multi-valued body (e.g. `local_tax_city ← residence`) has candidate sets that are unions of exponentially many body sets.
3. Forced ENDED (change-from) only removes labels. The CONT/ENDED labels are otherwise independent per run, so **per-member answers** (value definitely / possibly in the set at t) should be decidable by the same T*-style argument (not implemented, therefore *conjecture*).

Recommendation: contract v2 returns, for multi-valued segments, `{definite_members, possible_members}` and an optional capped set-of-sets expansion; `holds(v, t)` needs only the member-level answer.

## 6. Provenance and knowledge compilation (sketch only; nothing built)

All subset-minimal environments for a candidate can be exponential (2^m for m premises with two independent supports each), so enumeration needs a shared representation. The structure found above suggests one:

- **Ordered decision diagram over report variables in anchor order.** Admissibility is a left-to-right scan whose relevant state is small: the latest value and the two latest anchors (x1, A1, A2) that decide C1 under shielding/SU, counters clipped to {0, 1, ≥2} for the unshielded case, a pending-obligation flag, and, for C3, the set of `from` values already seen TRUE. Width ≈ O(n · |V|² · 2^f) with f = number of distinct `from` values referenced by change cues, polynomial for bounded f. Corrections and tied anchors add a small bounded factor per occurrence.
- **What it buys.** Model counting and output-sensitive enumeration of minimal environments, and, importantly, **conditioning** (set a report false) in time linear in the diagram, which is exactly what a withdrawal does.
- **Composition across keys.** Derived beliefs conjoin per-key circuits over *disjoint* variable sets. The design's static exactness condition (rule-body closures pairwise disjoint) is exactly decomposability, so a d-DNNF is the natural target and the existing check is already the side condition.
- **Caveat.** This is a plan, not a result. The exact environment definition used by the kernel's `Support` record must be fixed in G0 before it can be tested.

## 7. Limitations and what was not covered

- Single-valued **changeable** keys only. Single-valued stable keys (all TRUE values equal) are simpler but not implemented; multi-valued keys are only characterised.
- Policies P0, P0c, P0cc, P0cSU. **P1** (class-prioritised dispute, rank/corroboration) was not implemented; it changes E but looks compatible with the same argument (unverified).
- Admission (A-SELF, retractions, `blocked`) is outside the kernel and was held fixed: instances were built so that admission is the identity (asserted in the bridge).
- Ladder level 2 is not exercised by any test instance.
- Derived-key rule evaluation (`_derive` world products) was not touched.
- Open-world status ladder, negative evidence, attributed claims and partial-date precision from design v0.3 are *not* part of the validated kernel and are not covered here. This memo concerns the kernel the study validated.
- Timings are from one machine, three instances per cell, pure Python.
- NP-hardness of the general problem with unbounded corrections is neither proved nor refuted.

## 8. Impact on tasks

**T-B6 (equivalence collapsing).** Demote from "kernel precondition that cuts n" to a *semantic option*. Do not rely on it for tractability (§4). If adopted, define it as a labelled semantic variant with its own fixtures, and state that it changes `asof`/`belief_as_of` answers. Keep the *measurement* (n before/after) as a telemetry metric.

**T-B7 (above-budget behaviour).** The budget should be on **branch count** (corrections × tied-anchor choices) and on provenance enumeration size, not on n. For the single-valued class the n ≤ 12 cap can go; n of a few hundred is interactive. `ResourceLimited(environment_budget)` becomes `ResourceLimited(branch_budget)`. Suggested default: 2^12 branches per key (≈ 0.3 s at n = 30 in the measurements above).

**T-C2 (storage).** Do not persist interpretations or per-interpretation environments. Persist reports plus a per-key **answer table**: the feasible-pair table, or just the O(n) candidate segments it induces, recomputed on append (≈ 0.1 s at n = 50, ≈ 1 s at n = 100 in the current Python; incremental update is a straightforward optimisation). This also makes write cost polynomial in n, removing the "2^n write cost" row from the design's measured envelope for this class. Multi-valued segments need the member-level representation (§5) in the schema before the table layout is frozen.

**G0 / E1 / E4.** Add this kernel as a **third independent derivation** in the differential harness (oracle_v1, oracle_v2, fast kernel); the harness's random-instance generator in `research/r41/instances.py` is reusable for T-E4.

**T-I5 / G4.** The compilation route in §6 is the concrete starting point for R4.1 proper; the open item is provenance, not status/value/alternatives.
