# The pipeline: `Memory`, admission, kernel and store as one system (Lane M)

Code: `src/palimem/engine/` (`Pipeline`, `StoreAdmitter`, `KernelReviser`, `ViewLog`), `src/palimem/memory.py`
(`Memory`), `src/palimem/compat/revise_stream_v1.py` (the v1 adapter and profile). Harness: `harness/pipeline_diff.py`.
Tests: `tests/test_pipeline.py`, `tests/test_compat_adapter.py`, `tests/test_pipeline_diff.py`,
`tests/conformance/impl_memory.py` (+ `test_impl_memory.py`).

## 1. Shape

```
append(Report)  ──►  Backend.append (one transaction)
                       1 log row (LSN, salted chain)
                       2 StoreAdmitter ── palimem.admission (incremental; whole-log Admitter.evaluate is the audit oracle) ──► AdmissionRecords
                       3 KernelReviser ── palimem.kernel (justify_key / justify_derived)    ──► Belief versions
                       4 barrier marks, completion jobs, outbox events
query(Query)    ──►  Backend.read_belief (generation barrier) ──► palimem.policy.decide ──► Resolved | ResourceLimited
```

`Memory` is the **host API** (`docs/API_TRUST_BOUNDARY.md`): the caller supplies `source`, `origin` and `actor`. The
agent tool API binds them itself and is a separate task (T-F2). The three-call facade sits above `Memory`.

## 2. How a revision works

* **Admission is a pure function of (log prefix, admission config), computed incrementally.** The definition is the
  whole-log evaluation (`Admitter.evaluate`): the audit oracle. The append path does not run it. `IncrementalAdmission`
  (`src/palimem/admission/incremental.py`) keeps the evaluation's state for the committed head and updates it by the
  reports an append can change: the new report's own-merit decision (memoised), the withdrawal effects (recomputed over the
  *actors* only, and only when the append is an actor or a source a standing source-level withdrawal covers), the derived
  confirmations (only for keys that hold quarantined evidence), and the direct evidence of the keys whose status changed.
  A plain append therefore does no work that grows with the log. The record for the new report comes first, and an earlier
  report whose decision changed (a confirmation lifting a quarantine, a lapsed confirmation) gets a fresh record, so
  admission history stays append-only. Every mutation goes through an undo journal, because the store's append is one
  transaction that can roll back after admission ran; the state settles against the committed log before each use and is
  rebuilt from the log after a history rewrite (erasure, restore) or an admission change.
  * **Modes** (`Pipeline(admission=...)` or `PALIMEM_ADMISSION`): `incremental` (default), `whole-log` (the audit path, one
    full evaluation per append, also forced by `exhaustive=True`), `crosscheck` (incremental, compared with the whole-log
    evaluation decision for decision after every append; used by the tests and available as a debugging aid).
  * **One rule stays on a bounded slow path:** withdrawal effects. They depend on the order of the actors (newest first under
    `acting_reports_must_be_live`) and on source-level extents, so an actor append recomputes the effects over the actors
    (O(actors), plus the reports of a covered source), never over the log. A subclass that overrides the whole-log internals
    (`CompatAdmitter`'s source retraction) must provide `incremental_overlay` / `incremental_overlay_trigger` or it runs on the
    whole-log path (`supports_incremental`).
  * **Completion jobs** recompute from the same state (`KernelReviser.recompute`), so a job costs the key it recomputes. (The
    first incremental draft routed `recompute` through a whole-log evaluation, which the old evaluation cache had hidden; the
    work-count test `tests/test_pipeline_incremental.py` now fails if an append path runs `Admitter.evaluate`.)
* **Touched keys.** A key is touched when its admitted evidence set differs between the two evaluations, plus the key of the
  appended report. One rule covers an assertion, a withdrawal, a self-correction, a derived confirmation and the compat
  source-level retraction.
* **Base keys** are justified by `justify_key` from the admitted entries and stored as a new belief version. A key over the
  environment budget (default **12** since the budget cross-check, S-06; it was 7) gets an *incomplete* belief (`inference = incomplete(environment_budget …)`) and
  reads answer `ResourceLimited(environment_budget)` naming the key; it never degrades to another answer.
* **Derived keys** are rebuilt from the **stored base beliefs** (segments -> candidate families, the inverse of the
  kernel's classification), with this append's new versions overlaid. `depends_on` lists exactly the base versions whose
  candidates were read; `pinned` is the union of their pins. A derived version is written when its content changed, or when
  the store's own dependency closure will mark it (an unreturned marked key would be stale). Derived keys are revised in
  derivation-depth order, so a `revision_budget` keeps the shallow ones and leaves the deep ones stale until a completion job
  (the design's inference budget).
* **Supports (T-B4, S-12).** Every belief version stores `Segment.support`: per candidate, the subset-minimal
  environments over **base** reports, each stamped with the segment's valid interval. A base key stores the kernel's own
  supports. A derived key stores the **join of the stored base supports** of the worlds its rules consumed
  (`_BeliefProvider.world_envs` reads them from the stored base beliefs, so no log replay): the environment of a derived
  world is a set of base reports, never of derived beliefs. The empty world has no entry (a `Support` needs a report).
  `Resolved.provenance` is read from the stored segment. The stored provider cannot answer `reports` (the per-report
  values the oracle's flat rule needs): that projection is computed on the audit path (below).
* **Explanation budget.** `Query.explanation_budget = n` cuts the whole response to the first `n` supports (candidate-id
  order): `provenance` **and** the embedded `justified` view, and the answer says `explanation: truncated`. The cut is
  applied *after* `decide` on the full supports, so `kernel_status`, `decision`, `assertion`, `alternatives` and `policy`
  are exactly those of the unbudgeted query (a policy that reads the supports, such as the single-origin marking, cannot
  be changed by a budget). The belief record does not store whether the kernel's own environment cap (256 per candidate)
  was hit, so an answer says `truncated` only for a budget cut.
* **`explain(key, valid_at, mode, depth)`.** With `depth = None` (the full derivation closure) it is read from the stored
  supports of the segment; `mode = one` is the canonical environment (lexicographically least by sorted report ids). A
  `depth` limit asks for fewer derivation levels, which a stored belief cannot answer, so it is recomputed on the audit
  path from the admitted evidence at the snapshot and marked `truncated`.
* **Audit path cache.** `Memory.justification` re-justifies a key from the admitted evidence at a snapshot (the yes/no
  slots, `explain` with a depth, the profile's flat provenance). Base justifications are cached per (log position,
  admission version, key) and the cache is cleared by an erasure and by an admission change.
* **Attributions.** A key with admissible attributed reports and no direct evidence has `belief_of(holder, P)` as its
  candidate (established, or unresolved for several different claims); the inner `P` is never a candidate (T-D5, S-11).
  Its support is **one environment per origin group** (that group's earliest report): independent groups each suffice, a
  second report of one group is a copy. The kernel has no semantics for attributions, so this is the pipeline's own rule
  (see section 5, item 1).
  **Attribution safety (Lane Q).** Because the kernel status of such a key is about the *attribution*, the content the key
  is asked about is unknown, so `policy.decide` **never commits** to a `belief_of` candidate: the decision is `ask` (rule
  `ask`), `assertion` is `None`, the attributed candidates stay in `alternatives` and `inquiry.competing`, and the key itself
  is `inquiry.missing`. `kernel_status` and the candidates are unchanged (design rows 15 and 19 and the conformance fixtures
  read them there), so there is **no `Answer` contract change**. The attributions are read through the separate host call
  `Memory.attributions(key, as_of)`, which returns `AttributedClaim(holder, proposition, origin_groups, report_ids,
  supports)` records. Direct evidence beside attributions behaves as before (it commits to the direct value).
* **Authority in the product profile (Lane Q; default pending author confirmation, S-02).** In `open-world`, a `correct`
  whose actor fails the authority check is recorded as an `allege` (excluded, `authority_failed`) with no effect: it does
  not withdraw its target and its claimed value is not a rival report. `revise-stream-v1` keeps the paper's behaviour (a
  cross-origin correction stays a competing assertion carrying a correction cue). The switch is
  `AdmissionConfig.failed_correction_is_allege` (`None` = profile default; `False` = the paper's behaviour in the
  product profile; ignored by `revise-stream-v1`). Setting it to `False` reproduces every stored RETRACT-ACT run; the
  default changes RA-006 (test) and RA-007 (dev), whose golds disagree on this one situation. See
  `docs/decisions/S-02.md`.
* **`recompute`** (verify, completion jobs, erasure repair) is the same code path reading the head's log and the base keys'
  current beliefs from the view.

Reads use `read_belief` only. A key with no history at the snapshot is answered from an empty evidence set and labelled with a
*virtual* `BeliefView` (`ref = virtual:<entity>:<attr>`, version 1, never stored). A snapshot whose version an erasure
redacted raises `NotReconstructableError`: the contract has no `Answer` variant for it yet (STORAGE.md Q2).

### Per-query profile

`Query.profile` is a read-time projection (S-04): the stored segment's candidate family is reclassified under the requested
profile (an empty multi-valued family is `unknown` under `open-world`, `established_empty` under `revise-stream-v1`). It
never recomputes beliefs.

### Static checks at load

`Pipeline` refuses a kernel schema that violates the exactness condition (`check_schema`) or whose derivation chain is deeper
than the kernel's rule limit (`RuleDepthError`, depth > 8): never at query time.

## 3. The `revise-stream-v1` profile and adapter (T-E3)

`palimem.compat` projects a v2 `Answer` onto the paper's contract and defines the profile configuration. Profile-specific
conventions (none of them product contract), each carried **in the evidence log** so a store stays replayable, exportable and
verifiable:

| Paper feature | Compat convention |
|---|---|
| source-level `retract(source)` (also removes assertions the source makes later) | a marker report `source_status(<source>) = "retracted"` on the reserved attribute `__source_status__`; `CompatAdmitter` withdraws every report of a marked source from that position on, and leaves admission records and A-SELF effects as the paper does |
| `change` cue's `from` value (contract gap 1) | `Report.raw_ref = "palimem:compat:change_from:<json>"` |
| attribute kinds the contract classes cannot express (multi-valued *changeable*, derived cardinality, `error_allowed`, `competing_values`) | a full `KernelSchema` passed next to the contract `Schema` |
| `blocked` | `excluded / source_blocked` |
| withdrawn actors keep acting | `acting_reports_must_be_live = false` |
| per-slot-type closed world | kernel classification under the profile; `possible` and the yes/no slots exist only in the adapter |
| inertia on every attribute | `inertia = true` everywhere (differs from the S-08 decision text; flagged) |

Yes/no slots (`holds`, `changed`, `erroneous`) and `reported` have no v2 query form. They are answered from the audit paths of
`Memory` (`justification`, `evidence`, `admitted`: kernel replay over the admitted evidence at the snapshot), because the
interpretation sets they need are not part of a stored belief. The stored-belief path serves the four value slots.

## 4. Results

### G1 parity through the full pipeline (`python -m harness.pipeline_diff`)

Frozen Setting 1: 500 streams, 30,272 queries, **66,000-odd appends per backend**, every query answered at its own log
position (`belief_as_of` = LSN) from stored belief versions. Compared with `s1_NNNN.gold.json` on status, assertion and
alternatives, under the paper's exact source-level retraction (the compat marker) and `acting_reports_must_be_live = false`.

| slot | queries | in-memory backend | SQLite backend |
|---|---|---|---|
| `current` | 8,782 | 0 disagreements | 0 disagreements |
| `downstream` (derived keys, pinned through the store) | 14,643 | 0 | 0 |
| `asof` | 1,339 | 0 | 0 |
| `belief_asof` | 1,418 | 0 | 0 |
| `reported` | 1,452 | 0 | 0 |
| `yesno:holds` | 1,396 | 0 | 0 |
| `yesno:erroneous` | 744 | 0 | 0 |
| `yesno:changed` | 498 | 0 | 0 |
| **all** | **30,272** | **0** (65,632 appends, 0 resource-limited, 373 s) | **0** (65,632 appends, 0 resource-limited, 418 s) |

(One full run, both backends, 791 s on a laptop; the pull-request job runs every 10th stream, the nightly job all 500.)

* `--source-retract expand` (the contract-expressible per-report withdraws) disagrees on exactly the queries the kernel alone
  does (the known `source-retract:late-assert` gap): admission, store and revision add no disagreement of their own
  (`tests/test_pipeline_diff.py`).
* **Provenance, strict (`--provenance strict`, T-B4, decision S-12).** Two checks, both on every query and both backends:
  1. the **profile projection** (`palimem.compat.flat_provenance_v1`, `key_provenance_v1`, `erroneous_provenance_v1`: the
     oracle's flat set reproduced from the kernel's own structures on the audit path) must equal the study's own
     `eval.scorer.supporting_ids` (the frozen gold files only record `provenance` for `reported` queries);
  2. the **stored supports** of the answered segment (what `Resolved.provenance` is read from; for derived keys, joins of
     stored base supports) must equal, candidate by candidate and environment by environment, the supports recomputed by
     replaying the admitted evidence at the same snapshot. This is what shows that a derived belief built from stored base
     beliefs carries the replay's supports.
  Result, frozen Setting 1 (2026-10-05):

  | run | queries | answer disagreements | profile provenance vs study oracle | stored supports vs replay |
  |---|---|---|---|---|
  | in-memory backend, **all 500 streams** (65,632 appends, 1,189 s) | 30,272 | **0** | **0** | **0** of 26,182 checked |
  | SQLite backend, every 5th stream (100 streams, 12,944 appends, 229 s) | 6,046 | **0** | **0** | **0** of 5,196 checked |

  Every slot type is at 0 (`current` 8,782, `downstream` 14,643, `reported` 1,452, `asof` 1,339, `belief_asof` 1,418,
  `yesno:holds` 1,396, `yesno:erroneous` 744, `yesno:changed` 498). The "checked" counts are the value slots whose key is
  not over the environment budget. The SQLite backend runs the same engine above the storage primitives, so it is run
  bounded here (the full SQLite run of answers, without provenance, is above); CI runs both backends bounded on every
  push and every 5th stream nightly.
* Self-test: `--inject-bug {mutate-answer, no-source-retraction, self-update}` must fail the run, and
  `--inject-bug drop-provenance` (with `--provenance strict`) must fail the strict gate; CI checks both.

### Conformance suite against `Memory` (`tests/conformance/impl_memory.py`)

*Snapshot taken when supports were wired in (89 fixtures). The current run (90 fixtures, after the budget fixtures were updated for the default of 12) is: 27 pass, 10 fail, 13 skipped, 18 pending a decision, 2 shells, and the 20 trust-boundary fixtures are executed by their own runner, `tests/trust_boundary/runner.py` (18 pass, 2 fail with recorded causes). Refresh with `python -m tests.conformance.runner --impl tests.conformance.impl_memory:MemoryImplementation`.*

```
implementation: palimem.Memory

gate               total              pass              fail              skip  pending-decision             shell           not-run
------------------------------------------------------------------------------------------------------------------------------------
G0                     2                 2                 0                 0                 0                 0                 0
G1                    80                24                10                10                14                 2                20
G2                     7                 0                 0                 3                 4                 0                 0
------------------------------------------------------------------------------------------------------------------------------------
all                   89                26                10                13                18                 2                20
```

Of the 80 G1 fixtures: **24 pass** (it was 17 before supports were wired in), 10 fail, 10 are skipped (an op or capability the
pipeline does not have), 14 are pending a decision, 2 are shells, 20 are the trust-boundary fixtures (now run through the agent tool API by
`tests/trust_boundary/runner.py`, not by this runner). Seven fixtures that failed for lack of supports now pass (`ind-02`, `ind-05`, `ind-15`, `s12-01`, `s12-02`, `sec-39a`,
`sec-41a`). The 10 failures and their causes (all recorded in `tests/conformance/memory_status.json`):

| cause | fixtures |
|---|---|
| **agreeing independent reports: joint vs alternative environments** (section 5, item 1): the fixtures expect one environment per independent origin group (`{r1,r3}` and `{r2,r3}`; `{r3}` alone when q1 and q2 were admitted only because r3 confirmed them; a same-group copy collapsed), the kernel returns the one joint environment | `ind-01`, `ind-08b`, `ind-16`, `sec-39b` |
| fixture and a recorded decision disagree: row 20 store-wide dirty marker vs H4 component scope | `ind-20` |
| fixture and a recorded store behaviour disagree: STORAGE §9.5 (completion at the same LSN answers a snapshot read) | `ind-22` |
| the store does not expose it: erasure requester on the tombstone; keys stamped/skipped by a completion job | `ind-10`, `ind-21` |
| not implemented in the kernel: open-world rule exceptions (S-10); semantics of an authorised `dispute` (S-02 open point) | `s10-02`, `sec-41b` |

`tests/conformance/memory_status.json` is a **ratchet**: a fixture that passed must keep passing and a new failure must be
listed with its cause. Fixtures are never edited to pass. (One shape defect was corrected, not weakened: `s12-01` expected
bare id lists in an `Explanation`, whose contract shape is `Support` objects; it now reads the same `_environments` virtual
field that answers have, with the same expected environments.)

The ratchet has **three** states for a check that reads the frozen study data (today only `compat-01-authority-coincide`),
so the baseline is the same with and without the data: `pass_needs_data` (it passes because the data is present),
`needs_data` (skipped because the data or the study checkout is absent: CI's plain `test` job) and `fail`. A machine with the
data demands a pass; a machine without it accepts the skip and never a failure. The baseline is regenerated with the data
(`python -m tests.conformance.impl_memory`); the whole suite is green in both conditions (1,013 passed, 49 skipped with no
study checkout, no frozen cache and no deposit zip, on Python 3.11 and 3.13).

## 5. Contract and decision points this work surfaced

1. **What is an environment of agreeing independent reports? (author's decision, blocks four fixtures.)** Supports are
   wired and exact against the kernel (stored = replayed, profile = oracle). The independent fixtures were written from the
   design: two independent origin groups reporting the same value give **alternative** environments, one per group
   (`ind-01`: `{r1,r3}` and `{r2,r3}`; `sec-39b`: a second group "raises it above single-origin" by making two environments;
   `ind-08b`: three groups, three environments, so a budget of 1 truncates; `ind-16`: q1 and q2 were admitted only because
   r3 confirmed them, so `{r3}` alone). The kernel returns **one joint environment** (`{r1,r2,r3}`), because under A-ERR an
   error label needs a dispute, so every admissible interpretation has both reports TRUE (Lane B2's finding). Both are
   defensible: the design's "survives the loss of one" is delivered by recomputation after the withdrawal under either, but
   alternative environments (an ATMS reading, with admission dependencies added to a report's environment) are what the
   single-origin marking of SEC-39 and the explanation budget are written against. The pipeline does **not** pre-empt the
   decision: base keys keep the kernel's validated joint environments; only attributions (no kernel semantics exist for
   them) use per-origin-group alternatives, which is what `ind-15` expects. Choosing the ATMS reading means changing the
   kernel's support definition (and then the product rule differs from the oracle's flat set in different places), not the
   pipeline.
2. **Row 20 versus H4.** Fixture `ind-20` expects a store-wide `store_dirty` marker (design row 20); the store implements the
   author's component-scoped marker (H4, SEC-22 pending). The fixture and the decision disagree.
3. **Row 22 versus STORAGE §9.5.** A completion that runs while the head is still the append that made a key stale stamps its
   version with that same LSN, so a read at that snapshot is answered by it. `ind-22` expects `ResourceLimited`.
4. **Erasure requester.** `ind-10` expects the tombstone to name who requested the erasure; the store's `Tombstone` carries a
   pseudonymised `actor_ref` of the *report's* actor and `Memory.delete` takes no requester.
5. **Completion report.** `CompletionReport` has counts, not the keys stamped or skipped (`ind-21`).
6. **Open-world rule exceptions** (S-10: an unknown exception follows the exception attribute's completeness) are not implemented
   in the kernel (`s10-02`); `Rule` also has no `exceptions` field yet.
7. **Authorised `dispute`** has no kernel semantics (S-02 open point): `EvidenceSet.disputes` is not read (`sec-41b`).
8. **`inertia = false` on a changeable attribute** is specified (the value holds only within its stated valid interval, no extension;
   S-08, ruling 14 of 2026-10-05) but not implemented; the kernel refuses it and the adapter reports `ind-11` / `ind-12` as skipped.
9. **Negative evidence, crash points other than the store's steps, tamper/restore hooks, extraction** are not in
   the pipeline; the adapter returns `NotImplemented` and those fixtures are skipped with a reason. (Entity merges and
   `find` are in the entity layer, `docs/ENTITIES.md`: three small hooks in `Pipeline`/`KernelReviser`/`Memory`.)
10. **Source-scope withdraw.** The contract still has none; the compat marker is the only representation. Whether the product
    wants one is the author's decision (see `docs/MORNING_REVIEW.md`).

## 6. Notes for the facade / MCP lane

* Use `Memory` as the host core; give the agent tool API its own binding layer that fixes `source`, `origin` and `actor`.
  `Memory.append` generates a random idempotency key unless one is passed: pass one wherever a retry is possible.
* `Memory.append(..., complete=True)` runs `complete_pending` afterwards so a read never sees a key the host could have
  repaired; pass `complete=False` only to observe the barrier.
* Serving reads: `Memory.query` / `Memory.explain`. `Memory.read(key, as_of)` is the raw barrier result.
* `Memory.with_policy(preset)` re-reads the same state under another preset; the kernel's answer does not change (invariance
  under policy).
* Configuration changes are versioned inputs: `set_admission(config)` (a grant or source-status change is an admission version
  and governs appends from the next one), `set_policy(policy)`.
* The audit paths (`admitted`, `evidence`, `justification`) are for differential testing and for questions a stored belief
  cannot answer; they replay admitted evidence and are not the serving path.
* A `Memory` over SQLite is reopened by constructing a new `Memory` on the same file with the same schema and configuration:
  inputs already stored are adopted, a different one needs a higher version.


## Disputes and denials in the evidence (rulings 3 and 4 of 2026-10-05)

The kernel's per-key evidence is `direct` (kernel views of the admitted reports) plus, in the product profile, the denials of the active authorised disputes (`palimem.admission.disputes`). Both the whole-log path (`direct_entries(ev, disputes)`) and the incremental state (`IncrementalAdmission._direct_list`) build it with the same function, so they agree append by append; a key that holds a dispute re-derives its evidence whenever a report arrives on it (a new report may confirm the target). The compat profile never merges denials. See `docs/decisions/S-02.md` and `docs/decisions/S-04.md`.
