# Storage (T-C1, T-C2, T-C3, T-C10)

Code: `src/palimem/store/`. Tests: `tests/store/` (every contract test runs on both backends).

## 1. Shape

```
Backend (Protocol)  ◄── Engine ──► Storage (internal Protocol)
  InMemoryBackend  = Engine + MemoryStorage      (reference; transactions = snapshot/restore)
  SQLiteBackend    = Engine + SqliteStorage      (default; WAL, BEGIN IMMEDIATE, stdlib sqlite3)
```

`Engine` implements the backend semantics **once** (append, replay, verification, erasure, recovery, as-of
resolution). The two backends differ only in the ~35 storage primitives, so the contract, the chain, the
idempotency and the crash tests exercise the same algorithm on both and cannot drift. A new backend
(MongoDB, later) implements `Storage`, not the semantics.

The store **decides nothing about meaning**. Two stages are injected for each append:

| Stage | Protocol | Lane | What the store does with the result |
|---|---|---|---|
| Admission | `Admitter.admit(AdmissionContext) -> Sequence[AdmissionRecord]` | D | stores each record in the admission log (chained). Must include a record for the appended report; may include more (e.g. a later report confirming an earlier quarantined one) |
| Revision | `Reviser.revise(RevisionContext) -> Sequence[Belief]` | B | validates and stores each belief version, pins, dependencies, and moves the current-version index |

`Reviser.recompute(key, view)` rebuilds one key's belief from the log alone, for `verify_beliefs` (SEC-25).
`StoreView` (read-only) is passed to both stages and, inside an append, includes the not-yet-committed
rows of that append.

## 2. Operations

| Operation | Notes |
|---|---|
| `append(report, idempotency_key=, admitter=, reviser=) -> AppendResult` | one atomic transaction (§4); `report.id` must be unassigned (the log assigns a monotone ULID) |
| `current_belief(key)`, `belief_version(key, v)`, `belief_at(key, as_of)` | `as_of` is an **LSN (int) or a timestamp** (S-05) |
| `lsn_at(t)` | greatest LSN with `recorded_at <= t` (0 if none) |
| `scan(from, to)`, `entries_for_key(key, to_lsn)`, `get_entry(id)`, `admissions_for_report/_key` | replay; erased rows come back as `Tombstone` from `scan`/`get_entry` and are skipped by `entries_for_key` |
| `key_dependents(key)`, `attr_dependents(attr, as_of)` | dependency lookup: current beliefs that depend on `key`; derived attrs that read `attr` under the schema in force |
| `put_schema`, `put_input(kind, version, payload)`, `schema(as_of)`, `input_at(kind, as_of)` | versioned inputs (§6) |
| `erase(report_id, reason)` | tombstone (§7); needs a host-supplied `store_secret` |
| `verify_log(from, to, anchor=)`, `export_head()`, `verify_beliefs(reviser)` | optional capabilities (§5) |
| `recover()` | invariant check after an interruption (§8) |

`capabilities` is a frozenset: `verify_log`, `export_head` (the chain; absent on `chain=False`), `erase` (when a
`store_secret` was supplied). A backend without them is still contract-conformant (tested).

### Belief axis (S-05)

The **LSN** (log sequence number, 1, 2, 3, … with no gaps) is the canonical belief axis. `Belief.lsn` is the LSN that
produced a version; `belief_at(key, L)` returns the version with the greatest `lsn <= L`. A timestamp maps to the last
LSN whose `recorded_at` is at or before it. To keep that mapping well defined, `recorded_at` is **clamped non-decreasing**
along LSNs: if the clock goes backwards, a row takes the previous row's `recorded_at` (a tie resolves to the later LSN).

## 3. SQLite schema (format version 1, `PRAGMA user_version`)

| Table | Purpose | Append-only guard |
|---|---|---|
| `meta` | `generation` counter (and future flags) | – |
| `log` | evidence log: `lsn` PK, `report_id`, `recorded_us`, `generation`, plain key index (`key_entity`, `key_attr`), `content` (canonical Report JSON without `id`), `salt`, `commitment`, `prev_hash`, `entry_hash`, `idem_key` UNIQUE, `tomb` | no DELETE; UPDATE only into a tombstone |
| `admissions` | admission log: `seq`, `lsn` (log head at decision), `report_id`, `record`, `report_entry_hash`, own `prev_hash`/`entry_hash` | no DELETE/UPDATE |
| `beliefs` | one row per (key, version): `lsn`, `required_generation`, `completed_generation`, `belief` (canonical JSON), `reconstructable` | no DELETE; UPDATE only the `reconstructable` flag |
| `belief_pins` | (key, version) → report ids pinned (also answers "which versions rest on this report?") | – |
| `belief_deps` | (key, version) → (dependency key, version); index on the dependency | – |
| `current_belief` | **index 1**: key → current version | – |
| `inputs` | versioned inputs: `kind`, `version`, `effective_lsn`, `payload` | – |
| `attr_dependents` | **index 2**: (schema version, attr) → derived attrs that read it | – |
| `completion_jobs` | T-C4 (table only): durable jobs keyed by generation | – |
| `subscriptions`, `outbox` | T-C5 (tables only): event id = hash(key, old version, new version) | – |

The guard triggers are defence in depth against bugs; an attacker with file access can drop them. Tamper
*evidence* is the hash chain (§5). The pragma set is `journal_mode=WAL`, `synchronous=FULL`, `foreign_keys=ON`,
`busy_timeout`. Opening a store with an unknown `user_version` raises `StoreError`.

## 4. The append transaction

One `BEGIN IMMEDIATE` … `COMMIT` (memory backend: snapshot/restore). Everything below becomes visible together or not at all:

| # | Step | `fault` hook name after it |
|---|---|---|
| 0 | idempotency lookup; if the key exists → **replay** (no writes) | `begin` |
| 1 | assign `lsn`, ULID, clamped `recorded_at`, salted commitment and chain hashes; insert the log row | `after_log_insert` |
| 2 | `Admitter.admit`; insert admission rows (each chained to the report's `entry_hash`) | `after_admission` |
| 3 | bump `generation`; `Reviser.revise`; validate (`lsn == entry.lsn`, `version == current + 1`, one version per key) | `after_revision` |
| 4 | insert belief rows, pins, dependencies | `after_beliefs` |
| 5 | move the current-version index | `after_index` |
| 6 | (about to commit) | `before_commit` |
| 7 | `COMMIT` | `after_commit` |

A fault (exception or process death) at any step before `after_commit` leaves **no trace**: no row, no admission, no
belief, no generation bump, and the idempotency key is not burnt. At `after_commit` the append is durable and a retry
returns the stored result with `replayed=True`. Tested for every step on both backends, plus a real `os._exit` kill of a
separate process at every step on SQLite.

**Idempotency.** Every append carries a client key. A retry with the same key and the same report replays; the same key
with a different report raises `IdempotencyConflict`. Equality is checked through the row's salted commitment (no extra
content hash is stored, so nothing guessable survives an erasure). `InvalidRevision` (bad admitter/reviser output) writes nothing.

**Concurrency.** SQLite has one writer. `BEGIN IMMEDIATE` takes the writer lock up front; a second connection (thread,
process, or another `SQLiteBackend` on the file) waits up to `busy_timeout` (default 5 s), then raises `StoreBusy` having
written nothing. Readers never block (WAL). LSNs stay contiguous and the chain intact whichever connection wins
(tested with two connections × 25 appends, and 4 threads on one backend). An `Engine` also serialises its own threads with an `RLock`.

## 5. Hash chain and verification (T-C10; S-13 amendment, THREAT_MODEL §5)

The chain is a **storage-layer property**, not a `Report` field; `LogEntry` exposes `prev_hash`/`entry_hash`.

```
commitment_i = SHA-256(salt_i ‖ content_i)          content_i = canonical Report JSON, id excluded
entry_hash_i = SHA-256("palimem.log.v1\n" prev_hash_i "\n" {lsn, recorded_us, report_id} "\n" commitment_i)
admission:     SHA-256("palimem.adm.v1\n" prev "\n" seq "\n" record "\n" report_entry_hash)
```

The chained metadata contains no key, actor or value, so a tombstone can keep `entry_hash` without leaking anything.

`verify_log(from, to, anchor=)` checks contiguity, `prev_hash` links, the recomputed `entry_hash`, the salted commitment,
the plain-key index against the committed content, and the whole admission chain, and returns per-row
`RowStatus(linked, content_verified, tombstoned)`. Problem kinds:

| kind | meaning |
|---|---|
| `commitment_mismatch` | content edited |
| `entry_hash_mismatch` | row metadata (lsn, id, time) or commitment edited |
| `chain_break`, `missing_lsn` | row deleted, reordered or inserted |
| `index_mismatch` | indexed key differs from the committed content |
| `content_missing`, `erasure_incomplete`, `tombstone_mismatch`, `malformed` | structural damage |
| `admission_*` | admission log edited, reordered or detached from its report |
| `anchor_ahead_of_head` | **older copy restored** (SEC-26): the log ends before the exported head |
| `anchor_mismatch` | **history rewritten** (SEC-26): the row at the anchored LSN differs |

`export_head()` returns `Head(lsn, entry_hash, admission_seq, admission_hash, generation)`; keep it somewhere the
attacker cannot rewrite (a commit in a repo, an append-only bucket). **Without an anchor, an attacker who recomputes
every hash is not detected** (T-24, accepted): the chain is tamper-*evidence*, not signing.

`verify_beliefs(reviser, keys=None)` (SEC-25) recomputes each current belief with `Reviser.recompute` and compares
`segments`, `pinned`, `depends_on`, `invalidated_by`; it also flags a current-version index that is behind the newest
version (`index_mismatch`) and a stored belief that names another key or version (`belief_row_mismatch`). The problem
carries the key and the version. Full recomputation is practical only under the environment budget; pass `keys=` to sample.

## 6. Versioned inputs (T-C3 / design §Storage layout)

`put_schema` and `put_input(kind ∈ {semantic, admission, policy}, version, payload)` store inputs append-only; versions
only increase per kind. A version's `effective_lsn` is `head + 1`: it governs **appends from the next one on**, so
`schema(as_of=L)` returns the schema under which LSN `L` was decided, and historical queries can be evaluated under the
versions current at `belief_as_of`. The authority grant table is part of the **admission** input (S-07: a grant change is an
admission version). `put_schema` also maintains the attr → dependents index from each derived attribute's `rule.reads`.

## 7. Deletion (S-13) — what is done and what is not

`erase(report_id, reason)` (reason is a coarse class, no free text) replaces the row's `content`, `salt` and plain key
index with NULL and stores a **tombstone**: `report_id`, `lsn`, the **original `entry_hash`**, `key_ref` and `actor_ref`
(HMACs under the host-supplied `store_secret`, never written to the database), `reason_class`, `erased_at`, and
`affected_versions` (belief versions that pinned the report, pseudonymised). Those versions get `reconstructable = 0`.

* The chain still verifies across the tombstone (`linked: true, content_verified: false`).
* The salt is erased with the content, so `commitment`/`entry_hash` cannot confirm a guessed content.
* Tested: no plain key, value, actor, source or `raw_ref` text remains in the log or admission rows (SEC-30).
* **Not done here (T-C8):** *repair* of dependants (recomputing beliefs that rested on the report), backup-restore
  procedure, and redaction of values already materialised inside `belief` JSON. Until T-C8, `erase` removes the evidence
  and flags the affected versions but the stored belief JSON still contains values derived from it.

## 8. Recovery

Appends are atomic, so an interruption leaves the whole append or nothing; `recover()` proves it instead of assuming it:
contiguous LSNs, an admission record for every report, no belief beyond the log head, `generation >= head`, and a valid
current-version index. It returns a `RecoveryReport`; nothing is repaired automatically. The "replay from the last committed
generation" of the design is the job of the completion jobs (§9), which resume from `completion_jobs`.

## 9. Generation barrier — design for T-C4 (NOT implemented)

What exists: `Belief.required_generation`/`completed_generation` are stored as the reviser gives them; the append stamps a
store `generation` (one per write transaction; `log.generation` records it); `completion_jobs` exists as a table.

What T-C4 adds, using only those pieces:

1. **Marking.** In the append transaction, after the revision, traverse the dependency closure of the touched key
   (`key_dependents`, transitively) under a traversal budget and set each reached key's `required_generation = g`
   (new belief version with `inference = incomplete`, or a column on `current_belief`; open question below).
2. **Budget exhausted.** Do not mark partially. Write a **dirty marker scoped to the connected component of the attribute
   dependency graph** (known statically from `attr_dependents`), not store-wide (THREAT_MODEL T-13/H4): reads touching a
   dirty component return `ResourceLimited(store_dirty)`; other components keep serving. A store-wide marker remains only
   as the last resort. Add `dirty_components(component, generation)` (format version 2, migration via `user_version`).
3. **Completion jobs.** One durable row per generation, resumed after restart. A job for generation `g` may write a
   version only if the stored `completed_generation < g` and `required_generation >= g`, so it can finish an incomplete
   generation and can never overwrite work completed for a newer one (fixture: job `g` after `g+1` completed the same key).
4. **Reads.** `current_belief` of a key (or of anything in its `depends_on` closure) with `completed < required` →
   `ResourceLimited(stale_dependency, reason_key)`; `belief_at(key, as_of)` applies **its own snapshot's** barrier: a version
   whose `completed_generation` did not cover the generation visible at `as_of` yields `ResourceLimited`, never the later
   completed version.

Open: where `required_generation` lives for a key that has no new belief version yet (a column on `current_belief` is the
simplest and keeps `beliefs` append-only). The `Reviser` interface already allows incomplete beliefs: the store stores what it is given.

## 10. Notes for the other lanes

* **Kernel (B):** implement `Reviser`. Return a version for the touched key **and every derived key that reads it**
  (`ctx.view.attr_dependents(attr)`, then `key_dependents` for cascades). Set `Belief.lsn = ctx.entry.lsn`,
  `version = current + 1`, `required = completed = ctx.generation` unless you are deliberately leaving it incomplete. Read
  evidence through `ctx.view.entries_for_key(key)` and `admissions_for_key(key)`; erased reports do not appear.
  `recompute` must be deterministic and reproduce `segments`, `pinned`, `depends_on`, `invalidated_by` exactly.
* **Admission (D):** implement `Admitter`. `ctx.new_id()` mints ULIDs for `AdmissionRecord.id`; `ctx.view` shows prior
  reports and admissions (including the current report, already in the log). Return a record for the appended report; return
  extra records to change an earlier report's admission (the store appends, never rewrites).
* **Not in this task:** entity merges (T-C7), JSONL export/import (T-C9), outbox/subscription logic (T-C5).
