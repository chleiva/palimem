# The pipeline: `Memory`, admission, kernel and store as one system (Lane M)

Code: `src/palimem/engine/` (`Pipeline`, `StoreAdmitter`, `KernelReviser`, `ViewLog`), `src/palimem/memory.py`
(`Memory`), `src/palimem/compat/revise_stream_v1.py` (the v1 adapter and profile). Harness: `harness/pipeline_diff.py`.
Tests: `tests/test_pipeline.py`, `tests/test_compat_adapter.py`, `tests/test_pipeline_diff.py`,
`tests/conformance/impl_memory.py` (+ `test_impl_memory.py`).

## 1. Shape

```
append(Report)  ──►  Backend.append (one transaction)
                       1 log row (LSN, salted chain)
                       2 StoreAdmitter ── palimem.admission.Admitter.evaluate(log prefix) ──► AdmissionRecords
                       3 KernelReviser ── palimem.kernel (justify_key / justify_derived)    ──► Belief versions
                       4 barrier marks, completion jobs, outbox events
query(Query)    ──►  Backend.read_belief (generation barrier) ──► palimem.policy.decide ──► Resolved | ResourceLimited
```

`Memory` is the **host API** (`docs/API_TRUST_BOUNDARY.md`): the caller supplies `source`, `origin` and `actor`. The
agent tool API binds them itself and is a separate task (T-F2). The three-call facade sits above `Memory`.

## 2. How a revision works

* **Admission is a pure function of (log prefix, admission config).** `StoreAdmitter` evaluates it at `lsn - 1` and at
  `lsn`; the record for the new report comes first, and an earlier report whose decision changed (a confirmation lifting a
  quarantine, a lapsed confirmation) gets a fresh record, so admission history stays append-only.
* **Touched keys.** A key is touched when its admitted evidence set differs between the two evaluations, plus the key of the
  appended report. One rule covers an assertion, a withdrawal, a self-correction, a derived confirmation and the compat
  source-level retraction.
* **Base keys** are justified by `justify_key` from the admitted entries and stored as a new belief version. A key over the
  environment budget (default **7**, S-06) gets an *incomplete* belief (`inference = incomplete(environment_budget …)`) and
  reads answer `ResourceLimited(environment_budget)` naming the key; it never degrades to another answer.
* **Derived keys** are rebuilt from the **stored base beliefs** (segments -> candidate families, the inverse of the
  kernel's classification), with this append's new versions overlaid. `depends_on` lists exactly the base versions whose
  candidates were read; `pinned` is the union of their pins. A derived version is written when its content changed, or when
  the store's own dependency closure will mark it (an unreturned marked key would be stale). Derived keys are revised in
  derivation-depth order, so a `revision_budget` keeps the shallow ones and leaves the deep ones stale until a completion job
  (the design's inference budget).
* **Attributions.** A key with admissible attributed reports and no direct evidence has `belief_of(holder, P)` as its
  candidate (established, or unresolved for several different claims); the inner `P` is never a candidate (T-D5, S-11).
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
* **Provenance is informational and not yet comparable.** `Resolved.provenance` is empty until per-candidate supports land
  (T-B4, Lane B2). The frozen gold files carry `provenance` only for `reported` queries (0 of 1,452 differ, which is trivial:
  that slot lists the admitted ids themselves); for the value slots the study's provenance comes from its oracle at scoring
  time, not from the gold file, so strict provenance parity needs the oracle path that Lane B2's `--provenance strict` adds.
  The interim v1 provenance the harness derives for base keys is reported but compares against nothing.
* Self-test: `--inject-bug {mutate-answer, no-source-retraction, self-update}` must fail the run; CI checks it.

### Conformance suite against `Memory` (`tests/conformance/impl_memory.py`)

```
implementation: palimem.Memory

gate               total              pass              fail              skip  pending-decision             shell           not-run
------------------------------------------------------------------------------------------------------------------------------------
G0                     2                 2                 0                 0                 0                 0                 0
G1                    80                17                17                10                14                 2                20
G2                     7                 0                 0                 3                 4                 0                 0
------------------------------------------------------------------------------------------------------------------------------------
all                   89                19                17                13                18                 2                20
```

Of the 80 G1 fixtures: **17 pass**, 17 fail, 10 are skipped (an op or capability the pipeline does not have), 14 are
pending a decision, 2 are shells, 20 are the trust-boundary fixtures that wait for the agent tool API. The 17 failures and their
causes (all recorded in `tests/conformance/memory_status.json`):

| cause | fixtures |
|---|---|
| per-candidate supports not computed yet (T-B4, Lane B2): `_environments`, `provenance`, `explanation: truncated` | `ind-01`, `ind-02`, `ind-05`, `ind-08b`, `ind-15` (environments half), `ind-16` (environments half), `s12-01`, `s12-02`, `sec-39a`, `sec-39b`, `sec-41a` |
| fixture and a recorded decision disagree: row 20 store-wide dirty marker vs H4 component scope | `ind-20` |
| fixture and a recorded store behaviour disagree: STORAGE §9.5 (completion at the same LSN answers a snapshot read) | `ind-22` |
| the store does not expose it: erasure requester on the tombstone; keys stamped/skipped by a completion job | `ind-10`, `ind-21` |
| not implemented in the kernel: open-world rule exceptions (S-10); semantics of an authorised `dispute` (S-02 open point) | `s10-02`, `sec-41b` |

`tests/conformance/memory_status.json` is a **ratchet**: a fixture that passed must keep passing and a new failure must be
listed with its cause. Fixtures are never edited to pass.

## 5. Contract and decision points this work surfaced

1. **Supports.** Eleven fixtures need per-candidate subset-minimal environments (T-B4); `Segment.support` is empty today, so
   `explain` carries no environments, `explanation: truncated` never happens, and the recency/lww presets cannot pick among
   unresolved alternatives (they `ask`).
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
8. **`inertia = false` on a changeable attribute** has no specified semantics (S-08); the kernel refuses it and the adapter reports
   `ind-11` / `ind-12` as skipped.
9. **Negative evidence, merges, crash points other than the store's steps, tamper/restore hooks, `find`, extraction** are not in
   the pipeline; the adapter returns `NotImplemented` and those fixtures are skipped with a reason.
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
