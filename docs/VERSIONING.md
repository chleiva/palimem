# Versioning policy (T-A6)

Status: **draft for G0** · Lane A · Design v0.3 says "semantic versioning on the package follows the contract versions: a contract change is a major release". This document makes that precise and covers the other versioned things the design stores or ships.

## 1. What is versioned

| Thing | Format | Where it lives | Who changes it |
|---|---|---|---|
| **Package** `palimem` | semver `MAJOR.MINOR.PATCH` | PyPI, `pyproject.toml` | maintainers |
| **Output contract** (`Answer`, `Report`, `Proposition`, `Query`, `Belief*` shapes and their meaning) | `contract: "2.<minor>"`; v1 is the paper's contract | every `Answer`; `contracts/` JSON Schemas | RFC |
| **Semantic configuration** `{semantics: v0.3, self_update, profile}` | `semantics` string plus flags | stored with every belief version | RFC; changes what a given evidence set justifies |
| **Store format** | integer | `meta` table in the store; also written by `export` | maintainers; ships with migrations |
| **Backend interface** | semver of its own, with optional capabilities (section 6) | `palimem.backend` | RFC |
| **Schema / rule version** (user data) | content hash plus monotone integer | stored; belief versions pin it | the user |
| **Admission / authority / policy version** (user data) | monotone integer per kind | stored; belief versions pin them | the user, through the host API |
| **Conformance suite** (fixtures) | tag `conformance-<contract>-<n>` | `tests/fixtures/` | RFC for any expected-output change |
| **Compat profile** `revise-stream-v1` | frozen | adapter | never edited; a new profile is a new name |

Rule: *anything a stored belief depends on is stored with it*, so a historical `belief_as_of` query is answered under the versions that were current then (design: Time and Storage).

## 2. Package versions and the contract

- **From 1.0, a breaking contract change and a package MAJOR bump are the same event.** Package `1.y.z` serves output contract `2.m`; package `2.0.0` ships contract `3.0`. (The contract numbering starts at 2 because v1 is the paper's contract.) During 0.x the two are independent: the contract may change at any `0.MINOR`.
- **MINOR** adds backwards-compatible capability: new optional fields, new tools, new backend capabilities, new profiles, new fixtures that existing conforming behaviour already passes.
- **PATCH** fixes bugs without changing any answer for a conforming input, or restores conformance to a published fixture.

## 3. What is a breaking change

| Change | Breaking? | Handling |
|---|---|---|
| Remove or rename a field, enum value or method; change a field's type or meaning | **Yes** | contract major |
| Add a required field to `Report`, `Query` or an API call | **Yes** | contract major |
| Add an optional field or a new enum value that existing consumers can ignore | No (minor) | document; consumers must tolerate unknown fields |
| Add a new `kernel_status` or `decision` value | **Yes** | consumers switch on these; treat as major |
| Change which of `Resolved` / `ResourceLimited` a conforming input yields | **Yes** | contract major |
| Change what the kernel *concludes* from the same admitted evidence (a semantics change) | **Yes**, handled specially | new `semantics` string; old one stays selectable; default flips only at a major |
| Change the default policy preset | **Yes** (decisions change) | major, with release note and migration advice |
| Change an authority default, admission default or quarantine default | **Yes** | major |
| Store-format change that older code cannot read | **Yes** for downgrade only | section 7 |
| Add an optional backend capability | No | section 6 |
| Tighten a limit (cap, budget default) | **Yes** | major, unless the limit is documented as advisory |
| Performance changes, internal refactors, extra audit rows | No | patch/minor |
| Fixing a bug that made a published fixture fail | No | patch |
| Changing the **expected output** of a published fixture | **Yes** | RFC, dated note in the pre-registration addendum if a benchmark number moves (design hard constraint) |

## 4. The 0.x policy

- `0.MINOR` may break the contract; `0.MINOR.PATCH` may not.
- Every breaking change in 0.x is marked `BREAKING` in `CHANGELOG.md` with a migration note.
- The 0.x release train (the release train) is explicit: 0.1 kernel + SQLite + facade, 0.2 admission + two-axis query, 0.3 barrier/outbox, 0.4 extractor + agent tooling, 1.0 = gate G2 passed.
- The conformance suite is versioned independently so a 0.x release can say which suite it passes.
- Stored data written by `0.N` must be readable (by migration) by `0.N+1`, even when the contract moved. The store format is the one thing 0.x tries not to break.

## 5. Deprecation

- After 1.0: a deprecated field, method or default is announced in a MINOR release (with a runtime warning where applicable), kept for **at least two MINOR releases and 6 months**, and removed only in the next MAJOR.
- In 0.x: one MINOR release of warning where practical.
- Deprecations are listed in `docs/DEPRECATIONS.md` with the release in which they will be removed.
- Security fixes may remove behaviour immediately; the changelog says so.

## 6. Backend interface and optional capabilities

The backend interface has five required operations (design: append-with-revision transaction, current version, version at `belief_as_of`, dependency lookup, scan for replay). Everything else is an **optional capability**, discoverable as `backend.capabilities: frozenset[str]`.

- **`verify_log(from, to)`** is the first optional capability (author decision, 2026-10-04). It checks the evidence log's hash chain between two log sequence numbers and returns `{ok, first_bad_lsn?, rows_checked, rows: [{lsn, linked, content_verified}]}`. A tombstoned row is reported `linked: true, content_verified: false` (S-13).
- The hash chain (`prev_hash`, `entry_hash`, salted) is a **storage-layer property on the log row, not a Report field**. The Report contract and the G0 fixtures are unchanged, and a backend without the chain is fully contract-conformant. The SQLite default implements the chain and `verify_log`.
- **Adding an optional capability is not a contract break** and is a MINOR change to the backend interface. Removing one, or changing its result shape, is a break of the backend interface (backend major).
- Fixtures that need a capability declare `"requires": ["verify_log"]` and are skipped by backends that lack it. Required-operation fixtures never carry `requires`.
- A *required* operation may be added only in a backend-interface major, with a migration path for third-party backends.
- Third-party backends declare the backend-interface version and capabilities they implement and the conformance suite they pass.

## 7. Store-format versions and migrations

- Integer `store_format` in the store's `meta`. Opening a store with a *newer* format than the code knows is refused (never silently downgraded).
- Forward migrations run automatically on open, inside a transaction, and are recorded in an append-only `migrations` table. They are MINOR/PATCH-safe.
- Introducing the log hash chain into an existing store: a one-time migration writes a `chain_start` marker; `verify_log` covers rows from `chain_start` onward and reports earlier rows as `unchained`. It never rewrites old rows.
- A change that cannot be read by older code is allowed in a MINOR release only if the migration is automatic and `export`/`import` (JSONL of the evidence log) lets users roll back. Export is the stable escape hatch and is itself part of the contract (version-stamped).
- Downgrade is not supported except by export from the newer version and import into the older one, when the older one can read the export version.

## 8. Semantic versions and benchmark numbers

- A `semantics` version change is defined by *differences in answers on the conformance and frozen sets*, not by code diffs. The differential harness (T-E1) decides.
- Nothing ships that changes a benchmark number without a dated note in the pre-registration addendum (design hard constraint). Changelog entries that affect a number link to it.
- The compat profile `revise-stream-v1` and the frozen `frozen.json` (checksummed against the Zenodo deposit) are immutable; new evaluation material gets a new profile or set name.

## 9. Frozen artefacts and tags

- G0 freezes the contracts and fixtures as `contracts-v2.0-rc1`, then `contracts-v2.0`. After a freeze, changes follow the RFC process below.
- Git tags: `vX.Y.Z` for packages; `contracts-vN.M[-rcK]` for contracts; `conformance-N.M-K` for fixture suites.
- Releases are built by CI from tags with trusted publishing; PyPI uploads are never done from a laptop after 0.0.1.

## 10. RFC process

An RFC is required for: any contract change (including expected fixture outputs), a semantics change, a new or changed authority/admission/quarantine default, a backend-interface change, a new policy preset, or a change to the versioning rules themselves. Not required for bug fixes, docs, internal refactors, or new fixtures that existing behaviour already passes.

**Lifecycle:** `draft` → `discussion` → `final-comment` (FCP, 7 calendar days, announced in the PR) → `accepted` | `rejected` | `withdrawn` → `implemented` (with the release that shipped it).

**Steps**
1. Copy `docs/rfcs/0000-template.md` to `docs/rfcs/NNNN-short-title.md` (next free number) and open a PR.
2. Discussion happens on the PR. The author keeps the **Alternatives** and **Compatibility** sections current.
3. A maintainer proposes FCP when discussion has settled. Objections during FCP restart or extend it.
4. Until a governance document names more maintainers, the **project author decides** (they are the benevolent maintainer); the decision and its reasoning are written into the RFC. Decision records in `docs/decisions/` that predate G0 are folded in as RFCs `0001`–`0013` on acceptance.
5. Accepted RFCs state the target release and, if any, the conformance fixtures they add or change, with the pre-registration note where a number moves.
6. An accepted RFC is immutable except for status and implementation links; changes need a new RFC that supersedes it.

**Security-sensitive RFCs** (trust boundary, authority, deletion, log integrity) require a note from the threat model (`docs/THREAT_MODEL.md`) on whether the change widens an attack surface.

## 11. Changelog conventions

`CHANGELOG.md` in Keep-a-Changelog style, sections `Added / Changed / Deprecated / Removed / Fixed / Security`, with `BREAKING` prefixed on any entry that is breaking under section 3, and `Conformance:` lines naming fixtures added or changed.
