# The kernel (Lane B: T-B1, T-B5, T-B8, T-B9 core)

`palimem.kernel` justifies keys from **admitted** evidence. Standard library only. It is the production
kernel through G1 and the permanent audit oracle (author decision 2026-10-04): a line-for-line port of the
study's per-key enumeration (`revise_stream.oracle_v1`) over contract types. T-B10 (the fast existence-query
kernel) is a candidate, not part of this work.

## API

```python
from palimem.kernel import (KernelSchema, justify_key, justify_derived, JustificationProvider,
                            check_schema, ResourceLimitedResult)

ks = KernelSchema.from_schema(schema, entities=[...])      # contract Schema -> kernel flags (see "Gaps")
check_schema(ks)                                           # static exactness check, at schema load
j = justify_key(ks, key, admitted_entries, semantic, budget=7, change_from={...})
#   -> Justification | ResourceLimitedResult(environment_budget)   (never a silent fallback)
j.segments()            # contract `Segment`s: one status per valid-time interval, five-status ladder
j.segment_at(day)       # the segment containing a valid day
j.candidates_at(day)    # candidate family (the audit-oracle layer)
j.interpretations       # (ERR report ids, timeline) per admissible interpretation
d = justify_derived(ks, derived_key, JustificationProvider({base_key: j, ...}), semantic)
```

* **Input** is the admitted `LogEntry`s of one key (withdrawals and A-SELF already applied by admission). The
  kernel never reads the log. When Lane D's `EvidenceSet` lands, `EvidenceSet.direct` for the key is the
  argument; no kernel change is expected (not verified: `palimem.admission` was not on `origin/main` when
  this was written).
* **Output** is a `Justification`, not a `Belief`: a `Belief` also carries store metadata (version, lsn,
  generations, pins). The store builds it from `segments()` and `admitted_ids`.
* **Semantic configurations:** `SemanticConfig(self_update=False)` is P0c, `True` is P0cSU (A-SU). The profile
  selects classification only (below).
* **Time** is day-granular: anchor = day of `valid_from`, else of the log's `recorded_at`. Precision other than
  day, `valid_to`, and negative evidence raise `KernelUnsupported` (no oracle; S-09).
* **Budget:** default `DEFAULT_ENVIRONMENT_BUDGET = 7` admitted reports per key.
* **Totality ladder** of SEMANTICS §6 is implemented; `Justification.relax_level` reports it (0 on all of
  Setting 1).

### Status ladder per slot type (S-04)

| situation | profile `revise-stream-v1` | profile `open-world` |
|---|---|---|
| single-valued, no evidence | `unknown` | `unknown` |
| multi-valued, no evidence | `established_empty` | `unknown` |
| one candidate | `established` | `established` |
| several candidates (the empty world included) | `unresolved` | `unresolved` |

Yes/no slots (`holds`, `changed`, `erroneous`) are not in the v2 `Query`; the kernel exposes truth sets
(`holds_truths`, `changed_truths`, `erroneous_truths`) and the adapter (T-E3) owns the projection to
`possible` / `established false`.

### Derived keys, exactness (T-B5, T-B9 core)

Horn rules with defeasible exceptions are evaluated over per-key candidate families, **closed-world** for
exceptions (the compat profile's behaviour). Open-world exceptions (S-10) are documented, not implemented.
Per-key evaluation is exact only if no base key is reached twice on a derivation path; `check_schema`
refuses a schema where the base-attribute closures of two literals of a rule, or of two rules of one head,
overlap. The reviewer's counter-example is a regression test (`tests/test_kernel_unit.py`): per-key unions
produce `{A},{B},{}` where the global enumeration gives `{A},{B}`.

## Results (frozen Setting 1, 500 streams, 30,272 queries; compat admission stub)

| configuration | disagreements with the frozen gold |
|---|---|
| paper's source-level retraction (side table), compat flags | **0** (every slot type) |
| source retraction expanded into per-report withdraws (contract-expressible; default) | 85, all of one class (below), 0 unexplained |
| same, with `acting_reports_must_be_live=True` (product semantics) | 212 |

| slot | queries | side table | expanded |
|---|---|---|---|
| current | 8,782 | 0 | 22 |
| downstream | 14,643 | 0 | 46 |
| reported | 1,452 | 0 | 10 |
| asof | 1,339 | 0 | 2 |
| belief_asof | 1,418 | 0 | 1 |
| yesno:holds | 1,396 | 0 | 0 |
| yesno:erroneous | 744 | 0 | 2 |
| yesno:changed | 498 | 0 | 2 |

**The one class** (`source-retract:late-assert`): the paper's source-level retraction removes *every*
assertion of the retracted source, including ones it makes **after** the retraction (106 streams, 241 late
assertions). A per-report withdraw cannot reach a report that does not exist yet. A disagreement is classed
this way only when re-running the same query with the exact representation reproduces the gold.

Also: segment path equals candidate-family path at every queried day (0 inconsistencies); the static
exactness check accepts every stream's schema; 0 totality-ladder events; 0 cross-key corrections.

Property test: kernel interpretation sets equal `oracle_v1` on 400 random streams per policy (P0c, P0cSU),
including the admission stub against the study's `admitted_by_key`; mutating the kernel (no cue shielding,
no self-update) makes it fail.

## Gaps reported to the author (contract, not kernel)

1. **`change` cue `from` value.** `Report` has no field for it; A-CHG needs it (3,593 of 7,135 change reports
   in Setting 1). The kernel takes it out of band (`change_from`). Needs an explicit author line
   (suggestion: optional `Report.change_from`, only for cue `change`).
2. **Attribute kinds the contract classes cannot express:** multi-valued *changeable* keys with competing
   values (Setting 1 has 500; the Alex `residence` slot), the **cardinality of a derived attribute**, and the
   explicit `error_allowed` / `competing_values` flags. `KernelSchema.from_schema` maps what it can
   (`multi_set` -> multi, stable, non-competing) and the harness builds the full kernel schema directly.
3. **`Attr.inertia=False` has no specified semantics.** S-08 kept the boolean but did not define the false
   case; `from_schema` refuses it. The compat profile sets `inertia: true` on **all** attributes (the deposited
   code applies persistence to every attribute); this differs from the S-08 decision text, which says stable
   keys and sets do not hold.
4. **Source-scope withdraw** does not exist in the contract (see the one class above).
5. **Admission flag** `acting_reports_must_be_live=false` is required by the compat profile (retracted
   corrections still withdraw their target: 387 in Setting 1); `CompatAdmission` implements it.

## Running

```
PALIMPSEST_STUDY_DIR=~/palimpsest python -m harness.kernel_diff                 # default: expanded withdraws
python -m harness.kernel_diff --source-retract sidetable --strict               # exact paper behaviour
python -m harness.kernel_diff --limit 100 --inject-bug self-update              # must exit 1 (also: ignore-corrections, mutate-answer)
```

`harness/convert.py` (study stream -> `LogEntry`s, LSN = arrival index; `CompatAdmission` is the temporary
admission stub until T-D1/T-D2) and `harness/kernel_diff.py` are wired into the CI `harness` job.
