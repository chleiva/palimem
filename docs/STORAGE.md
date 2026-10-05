# Storage (T-C1 .. T-C10; wave 2: T-C4, T-C5, T-C6, T-C8, T-C9)

Code: `src/palimem/store/`. Tests: `tests/store/` (every contract test runs on both backends).

## 1. Shape

```
Backend (Protocol)  ◄── Engine ──► Storage (internal Protocol)
  InMemoryBackend  = Engine + MemoryStorage      (reference; transactions = snapshot/restore)
  SQLiteBackend    = Engine + SqliteStorage      (default; WAL, BEGIN IMMEDIATE, stdlib sqlite3)
```

`Engine` implements the backend semantics **once** (append, replay, barrier, completion, outbox, erasure, verification,
recovery, export/import, as-of resolution). The two backends differ only in the storage primitives, so the contract, the
chain, the idempotency, the barrier and the crash tests exercise the same algorithm on both and cannot drift. A new
backend (MongoDB, later) implements `Storage`, not the semantics.

The store **decides nothing about meaning**. Two stages are injected:

| Stage | Protocol | Lane | What the store does with the result |
|---|---|---|---|
| Admission | `Admitter.admit(AdmissionContext) -> Sequence[AdmissionRecord]` | D | stores each record in the admission log (chained). Must include a record for the appended report; may include more (e.g. a later report confirming an earlier quarantined one) |
| Revision | `Reviser.revise(RevisionContext) -> Sequence[Belief]` | B | validates and stores each belief version, pins, dependencies, and moves the current-version index |

`Reviser.recompute(key, view)` rebuilds one key's belief from the log alone, as of the head. The store uses it for three
things: `verify_beliefs` (SEC-25), **finishing keys left incomplete** (`complete_pending`, §9) and **repairing beliefs after
an erasure** (§7). In the last two the store stamps `version`, `lsn` and the generations itself, so only the content of
what `recompute` returns matters. It must work for a key that has no stored belief yet and must read the base keys'
**current** beliefs from `view` (the store recomputes dependencies first, ordered by derivation depth).
`StoreView` (read-only) is passed to both stages and, inside an append, includes the not-yet-committed rows of that append.
`RevisionContext.inputs` carries the versions of the schema / semantic / admission / policy inputs in force (§6).

## 2. Operations

| Operation | Notes |
|---|---|
| `append(report, idempotency_key=, admitter=, reviser=) -> AppendResult` | one atomic transaction (§4); `report.id` must be unassigned (the log assigns a monotone ULID) |
| `current_belief(key)`, `belief_version(key, v)`, `belief_at(key, as_of)` | **raw** accessors (no barrier): what a Reviser needs. `None` for a version redacted by an erasure. `as_of` is an **LSN (int) or a timestamp** (S-05) |
| `read_belief(key, as_of=None) -> Belief \| LimitedRead \| NotReconstructable \| None` | **the serving read**, under the generation barrier (§9) |
| `complete_pending(reviser, limit=None) -> CompletionReport` | runs the durable completion jobs (§9); the report names the keys it `stamped` and the keys it `skipped` (left alone: a newer generation had completed them), not only the counts |
| `lsn_at(t)` | greatest LSN with `recorded_at <= t` (0 if none) |
| `scan(from, to)`, `entries_for_key(key, to_lsn)`, `get_entry(id)`, `admissions_for_report/_key` | replay; erased rows come back as `Tombstone` from `scan`/`get_entry` and are skipped by `entries_for_key` |
| `key_dependents(key)`, `attr_dependents(attr, as_of)` | dependency lookup: current beliefs that depend on `key`; derived attrs that read `attr` under the schema in force |
| `put_schema`, `put_input(kind, version, payload)`, `schema(as_of)`, `input_at(kind, as_of)`, `historical_inputs(as_of)` | versioned inputs (§6) |
| `subscribe(plan_id, keys)`, `unsubscribe`, `subscriptions`, `pending_events`, `ack_event`, `deliver(handler)` | notifications (§10) |
| `erase(report_id, reason, reviser=None, requester=None)` | tombstone **and dependency repair** (§7); needs a host-supplied `store_secret`; `requester` is kept as a pseudonym only |
| `pseudonym_of(principal)` | the HMAC the tombstones use for a principal, so the host can test "was this erasure requested by X?" without any plain id stored |
| `export_jsonl()`, `import_jsonl(lines, reviser=)` | portable evidence log (§12) |
| `verify_log(from, to, anchor=)`, `export_head()`, `verify_beliefs(reviser)` | optional capabilities (§5) |
| `recover()` | invariant check after an interruption (§8) |

`capabilities` is a frozenset: `verify_log`, `export_head` (the chain; absent on `chain=False`), `erase` (when a
`store_secret` was supplied). A backend without them is still contract-conformant (tested).

### Belief axis (S-05)

The **LSN** (log sequence number, 1, 2, 3, … with no gaps) is the canonical belief axis. `Belief.lsn` is the LSN that
produced a version; `belief_at(key, L)` returns the version with the greatest `lsn <= L`. A timestamp maps to the last
LSN whose `recorded_at` is at or before it. To keep that mapping well defined, `recorded_at` is **clamped non-decreasing**
along LSNs: if the clock goes backwards, a row takes the previous row's `recorded_at` (a tie resolves to the later LSN).

## 3. SQLite schema (format version 2, `PRAGMA user_version`)

| Table | Purpose | Append-only guard |
|---|---|---|
| `meta` | `generation` counter (and future flags) | – |
| `log` | evidence log: `lsn` PK, `report_id`, `recorded_us`, `generation`, plain key index (`key_entity`, `key_attr`), `content` (canonical Report JSON without `id`), `salt`, `commitment`, `prev_hash`, `entry_hash`, `idem_key` UNIQUE, `tomb` | no DELETE; UPDATE only into a tombstone (which may replace `idem_key` by an HMAC reference) |
| `admissions` | admission log: `seq`, `lsn` (log head at decision), `report_id`, `record`, `report_entry_hash`, own `prev_hash`/`entry_hash` | no DELETE/UPDATE |
| `beliefs` | one row per (key, version): `lsn`, `required_generation`, `completed_generation`, `belief` (canonical JSON), `reconstructable`, **`origin`** (`append` \| `completion` \| `repair`: why the version exists) | no DELETE; UPDATE only to redact a version (`belief` replaced and `reconstructable` 1→0) |
| `belief_pins` | (key, version) → report ids pinned (also answers "which versions rest on this report?") | – |
| `belief_deps` | (key, version) → (dependency key, version); index on the dependency | – |
| `current_belief` | **index 1**: key → current version, plus **`required_generation`**: the newest generation that names the key (§9). `version = 0` is a placeholder: the key is named but has no belief yet | – |
| `marks` | **history** behind `required_generation`: (key, generation, lsn). Needed to answer a historical read under the barrier of its own snapshot | – |
| `dirty_components` | dirty markers: `generation`, `attrs` (the attribute component, or `["*"]`), `set_lsn`, `cleared_lsn` | – |
| `completion_jobs` | durable jobs keyed by generation: `state` (`pending` \| `done`), `payload` (kind, lsn, seeds, keys; keys cleared when done) | – |
| `inputs` | versioned inputs: `kind`, `version`, `effective_lsn`, `payload` | – |
| `attr_dependents` | **index 2**: (schema version, attr) → derived attrs that read it | – |
| `subscriptions`, `outbox` | notifications (§10); outbox rows are unique per `(event_id, plan_id)` | – |

The guard triggers are defence in depth against bugs; an attacker with file access can drop them. Tamper
*evidence* is the hash chain (§5). The pragma set is `journal_mode=WAL`, `synchronous=FULL`, `foreign_keys=ON`,
`busy_timeout`. Opening a store with a format version newer than the build raises `StoreError`; an older one is **migrated** (§12).

## 4. The append transaction

One `BEGIN IMMEDIATE` … `COMMIT` (memory backend: snapshot/restore). Everything below becomes visible together or not at all:

| # | Step | `fault` hook name after it |
|---|---|---|
| 0 | idempotency lookup (plain key, or its HMAC reference if the row was erased); if found → **replay** (no writes) | `begin` |
| 1 | assign `lsn`, ULID, clamped `recorded_at`, salted commitment and chain hashes; insert the log row | `after_log_insert` |
| 2 | `Admitter.admit`; insert admission rows (each chained to the report's `entry_hash`) | `after_admission` |
| 3 | bump `generation`; compute the **dependency closure** of the touched key under the traversal budget; `Reviser.revise` (skipped on overflow); validate (`lsn == entry.lsn`, `version == current + 1`, one version per key) | `after_revision` |
| 4 | insert belief rows, pins, dependencies | `after_beliefs` |
| 5 | move the current-version index | `after_index` |
| 6 | **barrier**: marks and `required_generation` for every key in the closure, completion job and/or dirty marker, **outbox events** | `after_barrier` |
| 7 | (about to commit) | `before_commit` |
| 8 | `COMMIT` | `after_commit` |

A fault (exception or process death) at any step before `after_commit` leaves **no trace**: no row, no admission, no
belief, no mark, no job, no event, no generation bump, and the idempotency key is not burnt. At `after_commit` the append is
durable and a retry returns the stored result with `replayed=True` (the stored beliefs are those with `origin = append`).
Tested for every step on both backends, plus a real `os._exit` kill of a separate process at every step on SQLite.
`complete_pending` and `erase` have their own fault names (`COMPLETION_STEPS`, `ERASE_STEPS`) and are atomic the same way.

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
carries the key and the version. **Stale keys are skipped** (their stored version is outdated by design and is never
served as current); a belief the store fabricated as `unknown` for a key left without evidence matches a `None`
recomputation. Full recomputation is practical only under the environment budget; pass `keys=` to sample.

## 6. Versioned inputs (T-C6, design §Storage layout)

`put_schema` and `put_input(kind ∈ {semantic, admission, policy}, version, payload)` store inputs append-only; versions
only increase per kind (rule versions travel inside the schema). A version's `effective_lsn` is `head + 1`: it governs
**appends from the next one on**. The authority grant table is part of the **admission** input (S-07: a grant change is an
admission version). `put_schema` also maintains the attr → dependents index from each derived attribute's `rule.reads`.

* `schema(as_of=L)` / `input_at(kind, L)` return what was in force when LSN `L` was decided; `historical_inputs(as_of)`
  returns all four at once as `InputsAt` (schema, semantic, admission, policy and the LSN). **A `belief_as_of` query is
  evaluated under these**, which is the design's requirement that historical answers use the versions current then.
* `as_of=None` means "what the next append would see" (`lsn = head + 1`).
* `RevisionContext.inputs` hands the versions in force to the Reviser, which records them in `Belief.versions`. The store
  does not overwrite what a Reviser stamps; a Reviser that ignores `inputs` produces beliefs whose `versions` lie, and the
  conformance fixtures (not the store) are what catches that.

## 7. Deletion (S-13, T-C8)

`erase(report_id, reason, reviser=None)` (reason is a coarse class, no free text), in one transaction:

1. **Tombstone.** The row's `content`, `salt`, plain key index **and client `idem_key`** are removed; the tombstone keeps the
   **original `entry_hash`**, `key_ref` and `actor_ref` (HMACs under the host-supplied `store_secret`, never in the
   database), `reason_class`, `erased_at` and `affected_versions` (pseudonymised). `actor_ref` is the *author* of the
   erased report; when the caller passes `requester=`, `requester_ref` records *who asked for the erasure*, the same way
   (an HMAC, absent on tombstones written before it existed, so older ones still load): the host asks
   `Memory.tombstone_requested_by(tombstone, principal)` and no plain principal id is ever stored. The idempotency key is
   replaced by an HMAC reference, so a retry of the erased append still finds the row and gets the tombstone back.
2. **Redaction.** Every belief version that **pinned** the report is replaced by `{"redacted": true}` and flagged
   `reconstructable = 0`; outbox events that mention those versions lose their embedded views. *Conservative:* a version
   that pinned the report is redacted whole, because "which values rested only on it" cannot be decided cheaply for derived
   beliefs. Versions that did not pin it are untouched.
3. **Repair**, exactly like a withdrawal: the touched key and its dependency closure are recomputed **without** the report
   (`Reviser.recompute`, dependencies first) and stored as new versions (`origin = repair`, `lsn` = log head, required =
   completed = the erase's new generation). A key with no remaining evidence gets a fabricated `unknown` belief. If the
   closure exceeds the traversal budget the repair is deferred: a dirty marker plus a `repair` completion job.
4. **Failure is loud.** A `Reviser` failure rolls the whole erasure back (the report is still there); `erase` without a
   reviser raises when any version pinned the report, instead of leaving derived values behind.

**Which historical answers can no longer be reconstructed** is recorded twice: in the tombstone (`affected_versions`) and
as `reconstructable = 0` on the versions. `read_belief(key, as_of)` returns `NotReconstructable(key, version, lsn)` when the
version in force at that snapshot was redacted. (A snapshot at an LSN where the repair itself sits is answered by the
repaired version: that is what the log now justifies.)

**Residuals, stated plainly.**

* The **key text** of the erased report remains in the belief index columns (`beliefs`, `belief_pins`, `current_belief`,
  `marks`, `outbox`, `subscriptions`) *of a key that still exists*. It is removed from the log, the admissions, the
  tombstone and finished jobs. If the erased report was the only evidence about its key, the key's name is still visible in
  those index columns. Pseudonymising an orphaned key across derived tables is possible but invasive (primary keys); it is an
  open decision (§14, Q1).
* **Backups** are outside the engine: erasure from a backup needs the host's backup retention procedure. An export made
  before the erasure still contains the content and salt.
* `recorded_at`, LSNs, generation numbers and the chain hashes stay (they carry no content).

## 8. Recovery

Appends are atomic, so an interruption leaves the whole append or nothing; `recover()` proves it instead of assuming it:
contiguous LSNs, an admission record for every report, no belief beyond the log head, `generation >= head`, a valid
current-version index, `required_generation` equal to its marking history for every marked key, and no pending job ahead
of the store generation. It returns a `RecoveryReport`; nothing is repaired automatically. What "replay from the last
committed generation" means in practice is the completion jobs (§9): they are durable rows and `complete_pending` resumes
them after a restart.

## 9. Generation barrier (T-C4) — implemented

**Vocabulary.** The store `generation` increases by one per write transaction (an append or an erasure). A key's
`required_generation` is the newest generation that *names* it; the `completed_generation` of its current version is the
generation that version was fully computed for. A key is **stale** while `completed < required`.

1. **Marking, inside the append transaction.** The dependency closure of the touched key is a breadth-first traversal over
   `key_dependents` (current beliefs whose `depends_on` names a reached key, which covers cross-entity dependencies) plus the
   same entity's derived attributes (schema). It runs under the **traversal budget** (`traversal_budget`, default 1000 keys).
   Each key in the closure, and each key the Reviser returned, gets a `marks` row and `required_generation = g`.
2. **Budget exhausted.** Nothing is marked partially and the Reviser is **not** called. A **dirty marker scoped to the
   connected component of the attribute dependency graph** (known statically from the schema; concern H4) is written, plus a
   `revise` completion job with no key list. Reads of any attribute in the component answer `ResourceLimited(store_dirty)`;
   other components keep serving. A store-wide marker (`attrs = ["*"]`) is written only when no schema bounds the component.
3. **Completion jobs.** One durable row per generation that left work behind: either the keys still stale after the
   revision, or "recompute the closure" after an overflow. `complete_pending` runs them in generation order, each in its own
   transaction. A job for generation `g` writes a version for a key **only if the stored `completed_generation < g` and
   `required_generation >= g`**, so it finishes an incomplete generation and **cannot overwrite work completed for a newer
   one**. Versions are computed with `Reviser.recompute` at the log head (dependencies first, by derivation depth) and carry
   that head as their `lsn` (`origin = completion`); `required` and `completed` are both the key's newest required
   generation. A key the Reviser cannot finish (it returns an incomplete belief and the current version is already
   incomplete) is left alone and the job stays pending: no version pile-up. A finished job clears its key list and its dirty
   marker (`cleared_lsn` = the head).
4. **Reads (`read_belief`).** In this order: a dirty marker covering the key's attribute → `store_dirty`; no belief but a
   mark → `stale_dependency` on the key itself (a named, never-computed key is stale, not "no belief"); a redacted version →
   `NotReconstructable`; the version's own inference incomplete → `inference_incomplete`; `required > completed` →
   `stale_dependency`; a stale key in the version's `depends_on` closure → `stale_dependency` naming it. Otherwise the
   belief. Every `LimitedRead` may carry `last_complete`: an older version, complete when it was written, to be labelled with
   its own `lsn` (`LimitedRead.to_answer(valid_at)` builds the contract `ResourceLimited`: no segment, no `kernel_status`).
   **The old version is never served as current.**
5. **Historical reads apply the barrier of their own snapshot.** `read_belief(key, as_of=L)` uses the version in force at `L`,
   the generations that named the key at a log position `<= L` (`marks`) and the dirty windows that were open at `L`
   (`set_lsn <= L < cleared_lsn`). A version completed *later* has a later `lsn`, so it cannot answer a snapshot that was
   stale at its time. One consequence worth stating: if a completion runs while the head is still the append that made the key
   stale, its version has that same `lsn`, and a read at that snapshot is answered by it (no later evidence exists, so what
   the log through `L` justifies is exactly that belief).

`required_generation` lives **as a column on `current_belief`** (keeps `beliefs` append-only and gives O(1) current reads),
with the `marks` table as its history; `recover()` checks they agree.

## 10. Notifications (T-C5)

`subscribe(plan_id, keys)` is durable and idempotent. When a belief version is written for a subscribed key (an append, a
completion job or an erasure repair), an `outbox` row is written **inside the same transaction** for every subscribed plan:
`event_id = SHA-256(canonical {key, old version, new version})` (stable across redelivery), the old and new `BeliefView`
(the segment current at the time, a bounded view; the full record is behind `ref`), the LSN. `deliver(handler)` hands every
pending event to the handler and then records the acknowledgement: **at-least-once**. A crash after the handler and before the
acknowledgement redelivers the same `event_id`; subscribers must process idempotently on it. A crash after a belief change
commits and before anyone is told leaves the event in the outbox; it is delivered after recovery. Events of redacted versions
are delivered with `redacted = True` and no views. Staleness does not notify; the new version does.

## 11. Mapping to the design's acceptance table

| Row (design v0.3) | Where it is tested |
|---|---|
| Inference budget exceeded → `ResourceLimited`, no `kernel_status`, never `unresolved` | `test_barrier::test_inference_incomplete_is_resource_limited_not_unresolved` |
| Budget exhausted on A → B → C before C is visited → reads of C `resource_limited` until stamped; old C never current | `test_skipped_key_is_stale_…`, `test_a_dependent_of_a_stale_key_is_stale_too` |
| Traversal budget exhausted mid-cascade → dirty marker, every read `ResourceLimited(store_dirty)` until the job clears it | `test_traversal_overflow_sets_a_dirty_marker_scoped_…` (and the store-wide fallback) |
| Completion job for g after g+1 completed the same key | `test_a_job_for_generation_g_never_overwrites_…` |
| `belief_as_of` into a snapshot that was stale at that time | `test_completion_job_stamps_the_stale_key_and_history_keeps_its_own_barrier` |
| Crash after a belief change commits, before the subscriber is notified | `test_outbox` (restart, after-commit crash, after-handler crash) |
| Deletion of a report with dependants | `test_erasure` |
| Crash between append and index update, then retry | `test_crash` (existing) and `test_outbox::test_no_event_without_the_belief_change` |

## 12. Export, import and migrations (T-C9)

**JSONL export** (`export_jsonl()`, line formats in `palimem.store.portable`): a header (format, version, `store_format`,
chain flag, head), the versioned inputs, every log row (erased rows as tombstones: no content, no salt), every admission row,
a footer with the counts and the head. Beliefs are **not** exported. Salts are exported with the content because
`verify_log` needs them: treat an export like the database itself.

**Import** (`import_jsonl(lines, reviser=)`) loads into an **empty** backend and verifies while reading: contiguous LSNs,
`prev_hash` links, recomputed `entry_hash` and salted commitment per row (tombstones must carry the original entry hash), the
admission chain and its link to each report, the footer counts and the replayed head. Then it replays the revision stage with
the same Reviser. Everything runs in one transaction: a failed import leaves the target empty. Line order carries no meaning;
rows are placed by their own `lsn`. **Identity guarantee:** for a store with no erasure and no deferred completion, the
imported store has identical heads, identical chain hashes and byte-identical belief versions. With erasures the current
beliefs agree but version numbers may differ (the importer has no repair versions).

**Format versions and migrations.** `STORE_FORMAT_VERSION = 2`. `MIGRATIONS` maps `n` to a function upgrading an open SQLite
database from `n` to `n + 1`; all steps run inside one `BEGIN IMMEDIATE` together with the `user_version` bump, so a
migration is applied entirely or not at all, and raising `StoreError` leaves the file at `n`. The 1 → 2 migration adds the
barrier column, `origin`, `marks` and `dirty_components`, replaces the (empty) format-1 `completion_jobs` and `outbox`, and
refreshes the two triggers; a migrated file has exactly the shape of a fresh one (tested). A newer format is refused.

## 13. Notes for the other lanes

* **Kernel (B):** implement `Reviser`. `revise` returns a version for the touched key **and every derived key that reads it**
  (`ctx.view.attr_dependents(attr)`, then `key_dependents` for cascades), `Belief.lsn = ctx.entry.lsn`,
  `version = current + 1`, `required = completed = ctx.generation` unless you are deliberately leaving it incomplete
  (`completed < required` and `inference = incomplete`). Anything you do not return stays stale (marked) and is finished by a
  completion job through `recompute`. `recompute(key, view)` must work from the log alone for any key (including one with no
  stored belief), read base keys' **current** beliefs from `view`, be deterministic, and reproduce `segments`, `pinned`,
  `depends_on`, `invalidated_by` exactly (that is what `verify_beliefs` compares). Pin **base** report ids in `Belief.pinned`
  for derived beliefs: erasure repair finds the versions to redact through the pins. Stamp `Belief.versions` from
  `ctx.inputs`.
* **Admission (D):** unchanged: return a record for the appended report, extra records change earlier admissions.
* **Facade / query layer:** serve reads with `read_belief`, not `current_belief`. `LimitedRead.to_answer(valid_at)` is the
  contract `ResourceLimited`. `NotReconstructable` has **no counterpart in the output contract** (see §14, Q2).
  `BeliefView.ref` resolves with `Engine.get_belief_by_ref`. Run `complete_pending` from the host (after a restart, on a timer,
  or when a read returns a limited result); it is cheap when nothing is pending. Subscribers get events through `deliver`.

## 14. Decisions recorded, and open questions

Recorded by this work (conservative defaults; change any of them by editing here and the code together):

| # | Decision |
|---|---|
| D-C1 | `required_generation` is a column on `current_belief` (lead instruction), with a placeholder row (`version = 0`) for a key that is named but has no belief yet; the `marks` table is its append-only history for historical reads |
| D-C2 | A dirty marker is scoped to the connected component of the attribute dependency graph; `["*"]` only when no schema bounds it |
| D-C3 | The traversal budget defaults to **1000 keys per append** (the design leaves it to the schema; no measured number exists) |
| D-C4 | Completion and repair recompute with `Reviser.recompute` at the log head; the resulting version's `lsn` is the head (not the original append's LSN), so version numbers stay monotone with LSNs |
| D-C5 | `belief_at` / `current_belief` / `belief_version` stay raw accessors; the barrier lives in `read_belief` |
| D-C6 | An erasure redacts whole belief versions that pinned the report and repairs the closure; the key text of a still-existing key stays in index columns (documented residual) |
| D-C7 | `erase` requires the reviser whenever a version pinned the report; a failed repair rolls the erasure back |
| D-C8 | The client idempotency key of an erased row is replaced by an HMAC reference |
| D-C9 | `beliefs.origin` (`append` \| `completion` \| `repair`) so replay of an append returns only its own versions and an import replays only appends |
| D-C10 | The outbox is unique per `(event_id, plan_id)`; events carry the bounded view of the segment current at the time (the last segment when valid time is unconstrained) |

Open, for the author:

* **Q1.** Should an erasure **pseudonymise the key** of an orphaned key (sole evidence erased) across the derived tables? It
  would remove the last plain trace of a sensitive key name but touches primary keys and makes the key unaddressable.
* **Q2.** `NotReconstructable` (a historical snapshot whose version was redacted) is a store-level result with no
  `Answer` variant. The contract has `ResourceLimited` reasons for the barrier but nothing for "erased". Options: add a reason
  (a contract change, needs your explicit line), or have the facade answer `unknown` for such a snapshot.
* **Q3.** The default **traversal budget** (D-C3) and whether it should be derived from the schema (declared maximum fan-out,
  as the design suggests).
