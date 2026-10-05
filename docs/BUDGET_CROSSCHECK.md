# Budget cross-check: the enumeration kernel at 8 to 12 reports per key (S-06)

Lane B10 · 2026-10-05 · code `harness/budget_crosscheck.py` · raw result `bench/budget/results/budget-crosscheck-b12.json`

## The condition (decision S-06)

The default environment budget was 7, the envelope the study generator produces and the differential CI validated.
S-06 allows raising it to 12 **only when** the enumeration kernel's answers at 8 to 12 reports per key are
cross-checked against the brute-force global oracle (`oracle_v2`, cheap at 4,096 subsets) on **freshly generated
streams**, not against the candidate fast kernel (T-B10), which cannot validate what it is a candidate to replace.

## Result: **0 disagreements**

| | |
|---|---|
| Fresh streams | **1,050** (7 shapes x 150), seeds 7,700,000 and up (the frozen set uses seeds from 0; CI smoke uses 90,000) |
| Query comparisons | **80,856** = 40,428 per policy, P0c and P0cSU |
| Disagreements | **0** (status, assertion and alternatives, every comparison) |
| Kernel under test | `palimem.kernel.justify_key` with `budget=12`, through the same `StreamEval` / `answer_query` product path as `harness.kernel_diff` |
| Reference | the study's `gold_for` with the interpretations of `oracle_v2` (global labelling of every admitted assertion of the stream) |
| Runtime | **717.5 s** wall on 6 worker processes (861 s of oracle time), on a machine loaded by other jobs; reproduce with `python -m harness.budget_crosscheck --streams-per-shape 150 --workers 6 --out bench/budget/results/budget-crosscheck-b12.json` |
| Totality ladder | 7,484 base justifications, **all at level 0** |

Keys per report count (a key is one stream's focus key, taken at the latest belief time with exactly n admitted
reports on it; the same key appears at several n):

| n | keys | queries (both policies) | gold `established` / `unresolved` (current slot, P0c) | states with a correction | with a change cue | with >= 3 origins | with >= 2 distinct values |
|---|---|---|---|---|---|---|---|
| 8 | 928 | 22,564 | 346 / 582 | 489 | 534 | 779 | 843 |
| 9 | 850 | 20,556 | 308 / 542 | 467 | 506 | 703 | 782 |
| 10 | 714 | 17,184 | 252 / 462 | 414 | 429 | 575 | 667 |
| 11 | 537 | 12,918 | 191 / 346 | 314 | 345 | 426 | 505 |
| 12 | **317** | 7,634 | 113 / 204 | 195 | 207 | 253 | 303 |

Per slot (queries, disagreements): `current` 6,692 / 0; `asof` 13,384 / 0; `belief_asof` 6,692 / 0; `reported`
6,692 / 0; `yesno:holds` 13,384 / 0; `yesno:changed` 12,400 / 0; `yesno:erroneous` 20,076 / 0; derived
`downstream` 1,272 / 0; derived `current` 264 / 0.

## How the streams are made

* The study's own generator logic (`revise_stream.generator.Gen`: delays, reversals, correlated copies, trusted-source
  errors, genuine low-trust corrections, self-corrections, report and source retraction), restricted to a few keys, with
  the per-key cap lifted from 7 and the focus key **topped up** (re-assertions, competing erroneous reports, same- and
  cross-origin corrections, occasional retractions) on distinct days until it reaches 11 to 14 reports, so every n from 8
  to 12 is reached at some belief time.
* Seven shapes: `employer` (single-valued changeable), `residence` (multi-valued changeable), `birth_date`
  (single stable), `nickname` (multi-valued stable, non-competing), `derived` (`work_city` over employer with up to 9
  reports plus two small `hq_city` keys), `tax` (`local_tax_city` derived from a multi-valued key), and `selfupdate`
  (one origin, press and its copier wire, restating its own changing value: the regime A-SU is written for).
* The **total** number of admitted reports in a stream is kept at most 12, because `oracle_v2` enumerates every labelling
  of all admitted assertions of the stream jointly (2^N).
* Every slot type is asked at each chosen belief time: `current`, `asof`, `belief_asof`, `reported`, yes/no `holds`,
  `changed`, `erroneous`, and the derived `downstream` / `current`.

## Is the sample able to fail? (two self-tests)

* **Sensitivity.** The P0c and P0cSU golds are identical on ordinary multi-source streams (A-SU needs every competitor to
  be an earlier same-origin report) and differ on 35 of about 500 queries of the `selfupdate` shape. That is why that shape
  exists: without it the P0cSU half of the cross-check would be vacuous.
* **Injected bug.** `--inject-bug self-update` runs the kernel with the other policy than the gold. On the `selfupdate`
  shape it reports disagreements at every n (for example 8, 9, 10, 10 and 2 at n = 8 to 12 over 12 streams) and exits
  non-zero; CI runs this self-test.

## Limits of this evidence (read before relying on it)

1. **`oracle_v2` has no P0cSU.** It implements P0c (and P0cc, P1); A-SU lives only in `oracle_v1`. For P0cSU the
   brute-force oracle was extended in the global-labelling style **from the text of Addendum A** of the pre-registration
   ("an admitted report o may not be labelled ERR when every accepted competitor of o is a strictly-earlier report from o's
   own origin and no accepted correction targets o"; single-valued changeable keys only), not from the kernel. It is a second
   implementation of A-SU, written by the same lane that wrote the harness, not an independent oracle of it. The **P0c**
   half is independent.
2. **Single focus key.** With at most 12 admitted reports per *stream*, the focus key carries the weight and the rest of the
   stream is small. Interaction between several keys that are each at 8 to 12 reports is not covered (the oracle cannot be run
   there). The study's frozen set covers many keys but at most 7 each.
3. **The ladder was not exercised.** All 7,484 justifications were at level 0; the relaxation ladder (no admissible
   interpretation) is unvalidated at n above 7.
4. **Generator, not the study's distribution.** The top-up makes counts reach 8 to 12; real agent logs will differ (more
   re-assertions from one origin, longer valid-time structure). Valid time uses integer-day `since` or no cue; `until`,
   `interval` and negative evidence have no oracle (S-09).
5. **Not independent samples across n.** The same key at n = 8..12 is correlated; the independent unit is the stream (1,050).
6. **Cost.** The enumeration is exact at any n but 2^n: a median justification takes about 3 ms at n = 8, 78 ms at n = 12
   and 2 s at n = 16 (`bench/kernel/results/fast-kernel-diff.json`). A key at 12 reports therefore costs about 80 ms per
   recompute, which is most of the declared p99 append target (100 ms). Raising the default is correct, not free.

## Outcome

The condition in S-06 is met on this sample, so the default budget was raised from 7 to 12 in a separate commit
(`src/palimem/types/limits.py`). Explicit budgets (`Memory(budget=...)`, `justify_key(budget=...)`) still apply, and a key
above the budget still answers `ResourceLimited(environment_budget)`.
