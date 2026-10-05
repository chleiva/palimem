# Second-annotation agreement (2026-10-05, gold profile `default`)

Coverage: 29 of 29 decision points annotated.

| set | n | agreement | Wilson 95% | kappa | bootstrap 95% (valid/total) | value agreement |
|---|---|---|---|---|---|---|
| all | 29 | 0.931 (27/29) | [0.780, 0.981] | 0.866 | [0.630, 1.000] (2000/2000) | 17/17 |
| test_25 | 25 | 0.920 (23/25) | [0.750, 0.978] | 0.849 | [0.603, 1.000] (2000/2000) | 14/14 |
| ruling_items_RA-006_007_026 | 5 | 0.800 (4/5) | [0.376, 0.964] | 0.545 | [0.000, 1.000] (1855/2000) | 3/3 |

With so few items the intervals are wide; report the interval, not only the point estimate.

### Confusion, all (rows: registered gold; columns: annotator)

| gold \ annotator | act | abstain | ask | revalidate |
|---|---|---|---|---|
| act | 17 | 0 | 1 | 0 |
| abstain | 0 | 2 | 0 | 0 |
| ask | 1 | 0 | 8 | 0 |
| revalidate | 0 | 0 | 0 | 0 |

### Confusion, test_25 (rows: registered gold; columns: annotator)

| gold \ annotator | act | abstain | ask | revalidate |
|---|---|---|---|---|
| act | 14 | 0 | 1 | 0 |
| abstain | 0 | 2 | 0 | 0 |
| ask | 1 | 0 | 7 | 0 |
| revalidate | 0 | 0 | 0 | 0 |

### Confusion, ruling_items_RA-006_007_026 (rows: registered gold; columns: annotator)

| gold \ annotator | act | abstain | ask | revalidate |
|---|---|---|---|---|
| act | 3 | 0 | 0 | 0 |
| abstain | 0 | 0 | 0 | 0 |
| ask | 1 | 0 | 1 | 0 |
| revalidate | 0 | 0 | 0 | 0 |

## Disagreements (2)

Each needs a written adjudication. Do not edit the gold silently: record any change in `bench/agent/gold_errata.md`.

### RA-006 / RA-006.d1 (item I-6c1895, split test)

```
  attributes: citizenship: one value; it does not normally change
  source registry (trusted: a source treated as authoritative; origin group registry)
  source blog (low: a low-reliability source; origin group blog)
  day 1 [Report 1] registry reports that gina's citizenship is fr.
  day 7 [Report 2] blog corrects report 1 (made by registry on day 1): gina's citizenship is de.
  > Today is day 7.
  > Task (required): Select the right customs form for Gina.
```

- registered gold: **ask**. Rationale: A correction from a different origin withdraws nothing (A-CORR); the two values dispute each other and the stable key cannot hold both. Do not resolve by recency or by class.
- annotator: **act** `fr`. Reason: The authoritative registry supports fr; a low-reliability blog cannot justify replacing its report with de.
- adjudication (written reason required): 

### RA-023 / RA-023.d1 (item I-ad82dd, split test)

```
  attributes: home city: one value at a time; it can change over time
  source profile_app (standard: an ordinary source; origin group profile_app)
  day 1 [Report 1] profile_app reports that ivan's home city is lisbon.
  day 8 [Report 2] profile_app reports that ivan's home city is porto.
  > Today is day 8.
  > Task (required): Send Ivan the local newsletter edition.
```

- registered gold: **act** `porto`. Rationale: Default semantic configuration (P0cSU, self_update on): the source's later value supersedes its own earlier one.
- gold under profile `self_update_off`: **ask**. With self_update off (P0c) the two reports dispute each other: unresolved.
- annotator: **ask**. Reason: The source reports two different cities without identifying the later report as a change or correction; ask it to resolve which city is current.
- adjudication (written reason required): 

## Gold errata draft

Paste into `bench/agent/gold_errata.md` only for items whose adjudication changes the gold.

| date | item | old | new | reason | adjudicator |
|---|---|---|---|---|---|
| 2026-10-05 | RA-006.d1 | ask  | | | |
| 2026-10-05 | RA-023.d1 | act porto | | | |
