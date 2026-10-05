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
j = justify_key(ks, key, admitted_entries, semantic, budget=12, change_from={...})
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
* **Budget:** default `DEFAULT_ENVIRONMENT_BUDGET = 12` admitted reports per key (raised from 7 on the
  cross-check against the global oracle: `docs/BUDGET_CROSSCHECK.md`).
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

## Provenance (T-B4, decision S-12)

### What the oracle's provenance really is

The frozen gold files record provenance only for `reported` queries. Everywhere else the oracle's provenance
is computed by the study's scorer, `eval.scorer.supporting_ids`, which calls `revise_stream.gold.support_ids`.
It is a **flat set of observation ids** with no notion of environments, per slot:

| slot | oracle set |
|---|---|
| `reported` | the admitted assertions on the key |
| `erroneous` (yes/no) | the admitted assertions on the key of the named observation; empty if that observation is not admitted |
| `changed` (yes/no) | the admitted assertions on the key |
| `holds`, `current`, `asof`, `belief_asof`, `downstream` (observed key) | the admitted assertions whose **value is a candidate value** at the valid day (the union over every candidate world) |
| same, derived key | the admitted assertions of every `(base key, value)` binding used on **any** derivation path yielding a candidate value: exception bindings included, sub-traces folded in |

Correction to the S-12 record: the "all admitted ids when `unresolved|possible|unknown`" fallback it describes
belongs to the incremental store (`BeliefStore._prov_ids`), not to the oracle. The oracle's rule is the value
match above, and that is what the profile reproduces.

### Two rules, kept apart

* **Product (principled).** `Segment.support[candidate id]` lists the candidate's **subset-minimal environments**
  over **base** reports, each stamped with the segment's valid interval (`Support`). For one interpretation,
  the environment of a candidate world `S` at day `t` is the set of TRUE reports in the value runs that
  *contribute* `S` at `t` (runs of a value in `S` that cover `t`, definitely or at an unknown change point);
  the support is the antichain of minimal ones over all interpretations that yield `S`. Reports that merely
  license an interpretation (a competitor that explains an ERR label, the predecessor an `A-CHG` cue
  presupposes) are not evidence for `S`; neither are reports supporting the same value only in another stretch
  of valid time (supports are *per interval*; neighbouring segments merge only if both candidates and supports
  are equal). A derived world's environments are the joins of the environments of the base worlds it consumed
  (`EnvEngine`, a mirror of the rule engine that carries antichains). Segments carry the support map; the empty
  world has no support entry.
* **Profile `revise-stream-v1`.** `oracle_flat_ids(t)` reproduces the table above from the kernel's own
  structures (`Justification` / `DerivedJustification`; the derived case is a port of the study's traced rule
  engine, `OracleTrace`). `flatten(environments)` for this profile is therefore that set by definition.

### API

```python
j.segments()[i].support          # {candidate id -> (Support, ...)}, per segment, per candidate
j.explain(day, mode=ExplainMode.ALL, depth=None, env_cap=256)   # contract Explanation
j.oracle_flat_ids(day)           # profile projection, a frozenset of report ids
palimem.kernel.flatten(envs)     # union of ids
```

* `depth` counts derivation levels: the queried derived key is level 1, the keys its rules read are level 2
  (a base key's own reports are level 1). Deeper evidence is left out and the explanation is `truncated`.
  Rule chains deeper than 8 are refused, as in the oracle.
* `env_cap` (default 256) bounds the minimal environments kept **per candidate**, in a deterministic order
  (size, then ids). Above it the explanation is `truncated`; status and candidates never change.
* `mode=one` returns the lexicographically least minimal environment by sorted report ids (deterministic across
  versions, S-12 open question 1).
* **A finding about agreement.** Two reports of the same value from different origins cannot be separated into
  two alternative environments: under A-ERR neither can be labelled ERR without a dispute, so every admissible
  interpretation has both TRUE and the only minimal environment is the pair. "Survives the loss of one" is
  delivered by recomputation: withdrawing one report (admission) re-justifies the key and its environment is the
  survivor (tested). Independent *alternative* environments appear only where the semantics allows either
  report to be erroneous (a competing value exists).

### Parity with the oracle (frozen Setting 1, 500 streams, 30,272 queries; side-table source retraction)

| check | result |
|---|---|
| profile provenance (`oracle_flat_ids` and the slot rules above) vs the study's `supporting_ids` | **0 disagreements**, every slot type (current 8,782 · downstream 14,643 · reported 1,452 · asof 1,339 · belief_asof 1,418 · holds 1,396 · erroneous 744 · changed 498) |
| the environments' worlds equal the candidate family at every queried day (`support_inconsistent`) | **0** |
| status / assertion / alternatives vs gold (unchanged gate) | **0** |

The product rule against the oracle's flat set, over the 27,578 queries that are about a segment (informational,
never gating; `reported`, `erroneous` and `changed` are the key's admitted ids by definition):

| relation of `flatten(support)` to the oracle set | queries |
|---|---|
| equal | 19,613 (71.1%) |
| strictly smaller (product ⊂ oracle) | 7,186 |
| both have ids the other lacks | 420 |
| strictly larger (product ⊃ oracle) | 359 |

All 7,965 differences are classified, **0 unexplained**. Every report the oracle lists that the product does
not is in exactly one of four categories (counts are queries; a query can involve several), and every report the
product lists that the oracle does not is an environment of the *empty* candidate:

| cause | base | derived |
|---|---|---|
| the oracle lists a **redundant supporter**: a report of the value that some non-minimal environment contains but no minimal one needs | 2,359 | 4,757 |
| the oracle lists **exception evidence of other worlds**: it accumulates the bindings of every candidate world of an exception key, including worlds that block the rule | none | 888 |
| the oracle lists **reports of other intervals or worlds**: a report of a candidate value that is TRUE only in interpretations yielding other candidates or in a run not covering the day | 319 | 436 |
| the oracle lists a report that **every interpretation labels ERR** (a corrected target that repeats the value) | 47 | 30 |
| the **product lists the empty alternative's environment** (an unresolved derived key whose empty world is a candidate) | none | 779 |

### Gates and self-tests

`python -m harness.kernel_diff --source-retract sidetable --provenance strict --strict` is the gate (bounded
every 5th stream in CI, full nightly); `--inject-bug drop-provenance` must exit 1. `--provenance strict` fails
on any profile disagreement or any `support_inconsistent`. The cause classification is part of the report
(`provenance.principled_difference_causes`) and `tests/test_provenance_oracle.py` fails if any difference is
`UNEXPLAINED`. `tests/test_provenance.py` holds the unit and property tests, including a brute-force
re-derivation of the environments on 1,500 random (instance, day) pairs per policy and the design's
`two independent supports`, `disjoint intervals`, `last support withdrawn` and `depth-3 derivation, leaf
withdrawn` rows.

### Limits

* The environments are exact over the interpretations the kernel enumerates (default budget 12 reports per
  key since 2026-10-05, 7 before; see `docs/BUDGET_CROSSCHECK.md`); they inherit the enumeration's 2^n cost (R4.1). `EnvBudget(minimal=False)` is a diagnostic mode and
  never the product.
* For derived keys the number of environments is a product across the keys read; the 256 cap keeps it bounded.
* `always_err_ids` (used by the classification) is computed for base keys only; the harness aggregates it over
  every base key at the belief point for derived queries.
* Observed multi-valued keys: a candidate world is a set; its environment is the union of the runs of every
  value in it.

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
python -m harness.kernel_diff --source-retract sidetable --provenance strict --strict   # provenance gate (T-B4)
python -m harness.kernel_diff --limit 100 --source-retract sidetable --provenance strict --strict --inject-bug drop-provenance   # must exit 1
```

`harness/convert.py` (study stream -> `LogEntry`s, LSN = arrival index; `CompatAdmission` is the temporary
admission stub until T-D1/T-D2) and `harness/kernel_diff.py` are wired into the CI `harness` job.
