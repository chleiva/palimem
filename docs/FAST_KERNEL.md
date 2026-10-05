# The candidate fast kernel (T-B10)

Lane B10 · 2026-10-05 · code `src/palimem/kernel/fast.py` · tests `tests/test_fast_kernel.py`, `tests/test_fast_conformance.py` ·
harness `harness/fast_kernel_diff.py`, `harness/fast_pipeline_diff.py` · workflow `.github/workflows/fast-kernel.yml`

**Status: candidate. The enumeration kernel stays the production kernel and the permanent audit oracle** (author decision,
S-06). Nothing here switches the default; promotion is a separate author decision, and the criteria below say where it stands.

## What it is

A typed, productionised port of the R4.1 spike (`research/r41/kernel.py`, `docs/research/R41_MEMO.md`). The enumeration kernel
walks all 2^n TRUE/ERR labellings of a key's reports. Everything the study consumes from a key is a question about the
*family* of admissible TRUE sets T, not the family itself, and for one class of keys those questions are decidable without
listing T:

* the candidate family at a valid day, the status, the segments;
* `erroneous(o)`: can some admissible T contain o, can some omit it;
* `changed(v1, v2)`: does some T have a v1 report followed by a later v2 report, does some not.

Admissibility is a monotone dominating-set condition, a unique greatest solution of the change-from closure, and branching only
over corrections and same-anchor value conflicts. Cost is O(n^3) without corrections or tied anchors and exponential only in
(#corrections + #tied anchors), bounded by a branch budget **checked before running**.

## The proven class and the explicit dispatch

`palimem.kernel.fast.dispatch_key` routes each base key and returns the route and the reason, never hidden:

| route | when |
|---|---|
| `fast` | single-valued, **changeable**, observed key; policy P0c or P0cSU; no relaxation needed (totality-ladder level 0); at most 128 reports; branch bound at most 2^14 |
| `enumeration` | everything else: multi-valued, stable, derived keys, a key with no evidence, ladder level above 0, a branch bound over budget, more than 128 reports |
| `resource_limited` | the enumeration route is itself over its environment budget: `ResourceLimited(environment_budget)`, with the reason the fast path was not used |

## Evidence

**Frozen Setting 1, 500 streams, 30,272 queries** (`python -m harness.fast_kernel_diff --provenance --speed`, 278 s):
fast vs gold **0** disagreements, fast vs enumeration **0**, with provenance (profile projection vs the study oracle 0;
principled flatten vs the enumeration kernel 0). Routing over 54,320 base justifications: **45,567 fast**, 8,753 enumeration
(no evidence 1,788; multi-valued 5,528; stable 1,437). Self-test (`--inject-bug self-update`): 41 disagreements in 40 streams, exit 1.

**Full pipeline with the fast kernel** (`python -m harness.fast_pipeline_diff --backend memory --provenance strict`, 474 s):
500 streams, 30,272 queries, 65,632 appends, **0** disagreements, profile provenance **0** vs the oracle, stored supports vs audit
recomputation 26,182 checked, **0** mismatches.

**Random differential** (`tests/test_fast_kernel.py`): 500 adversarial instances per policy with n up to 9 (ties, repeated values,
cross-origin corrections), compared with the enumeration kernel on candidate families at every valid day, **segments including
per-candidate supports**, `changed` / `erroneous` / `holds` truth sets, `always_err_ids` and the oracle's flat provenance: equal
on every instance in the class. Also the `error_allowed=False` and `competing_values=False` flags, the single-origin
self-update regime, and 10 to 14 reports (statuses and candidates; supports aside).

**Speed vs the number of reports on a key** (median of 5 random agent-style instances, P0cSU, one core):

| n | 6 | 8 | 10 | 12 | 14 | 16 | 24 | 40 | 70 | 100 |
|---|---|---|---|---|---|---|---|---|---|---|
| fast | 0.5 ms | 0.7 ms | 1.2 ms | 2.0 ms | 3 ms | 4 ms | 13 ms | 58 ms | 374 ms | 1.07 s |
| enumeration | 0.5 ms | 2.8 ms | 14.6 ms | 78 ms | 416 ms | 2.04 s | not run | not run | not run | not run |

On the frozen set (every key at most 7 reports) the kernel time is 4.2 s fast-path dispatch against 8.2 s enumeration: the
benefit is for long histories, not for the study's envelope.

## Promotion criteria (S-06) and where they stand

The criterion: identical status, value, alternatives **and provenance** on the frozen sets, and on the independent suite
including the retraction fixtures, with its bounds documented.

| criterion | status |
|---|---|
| identical status / value / alternatives, frozen sets | **holds** (30,272 queries, 0; also through the full pipeline) |
| identical provenance, frozen sets | **holds** (0), but see gap 2: only inside the enumeration envelope |
| the independent suite incl. the retraction fixtures | **holds except four fixtures that assert the budget itself** (below) |
| bounds documented | this document |

`tests/test_fast_conformance.py` swaps the pipeline's single `justify_key` call for the dispatch and runs the whole conformance
suite: the outcome equals the production baseline except `ind-08a`, `s06-01`, `s06-02` and `s06-05`, which assert that a key above
the budget answers `ResourceLimited(environment_budget)`. The fast kernel answers such a key. Every retraction, cascade, derived-key,
quarantine and attribution fixture is identical.

## What is missing for promotion

1. **A contract decision on the budget.** `ResourceLimited(environment_budget)` is today a statement about the enumeration
   envelope. If the fast route answers keys the enumeration refuses, the resource contract has to say what the budget now binds:
   the enumeration route only (the fast route has its own limit) or both (which removes the benefit). Four fixtures encode the
   current meaning. This is the author's call.
2. **Provenance is complete only inside the enumeration envelope.** Per-candidate supports need the interpretations; the fast
   kernel materialises them lazily only when the key has at most `enumeration_limit` reports (default: the environment budget,
   now 12). Above that, segments carry no `support` and the explanation is `truncated`: a bounded explanation, never a different
   status or value. A polynomial algorithm for the subset-minimal environments is the R4.1 open problem.
3. **No shared base type.** `FastJustification` has the `Justification` read interface but no common base class or Protocol;
   two harness modules assert `isinstance(j, Justification)`, and the harnesses work around it. Promotion needs a Protocol in
   `palimem.kernel.justify` and the pipeline typed against it.
4. **Class limits.** Only single-valued changeable keys under P0c and P0cSU at ladder level 0. In Setting 1, 16.1 % of base
   justifications (8,753 of 54,320) went to enumeration (multi-valued 5,528; stable 1,437; no evidence 1,788), so the pipeline needs both kernels.
   The multi-valued answer itself is 2^n - 1 long: a factored contract is needed (R4.1).
5. **Independent correctness above n = 14.** Beyond what the enumeration can run, correctness rests on the algorithm and on the
   random differential up to 14 (and the study's per-key cap of 7); larger n is differentially tested for speed only. A few
   n = 16 to 20 enumeration cross-checks (minutes each) would strengthen it.
6. **Branch bound.** Exponential in corrections plus tied anchors; guarded (2^14, then fall back or `ResourceLimited`), but an
   adversarial key with 20 corrections goes to enumeration.
7. **Not wired into the product path.** The engine still calls `justify_key`; the harnesses swap it in. No performance numbers
   through the pipeline yet (Lane O/P work).
