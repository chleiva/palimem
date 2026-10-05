# Changelog

All notable changes are recorded here, following [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The package follows the versioning policy in [`docs/VERSIONING.md`](docs/VERSIONING.md). During 0.x any `0.MINOR`
may change the contract; such changes are marked **BREAKING** with a migration note.

## [Unreleased]

Work in progress on the way to 0.1 ; nothing here is released.

### Added
- Contract types and generated JSON Schemas (`palimem.types`, `schemas/`).
- Admission, authority and policy layers (`palimem.admission`, `palimem.policy`).
- Storage layer: backend protocol, in-memory and SQLite backends with a salted hash-chained log (`palimem.store`).
- Differential harness against the PALIMPSEST study, frozen-set guard and cost ledger (`harness/`, `palimem.costs`).
- Threat model, security policy, decision records S-01 to S-13, and the RETRACT-ACT benchmark design.
- `Memory.attributions(key)` and an `attribution_only` / `attributions` field in the agent tool API's `recall`: what a third
  party is reported to believe is read apart from the value.
- `CompletionReport.stamped` / `.skipped` (the keys a completion run wrote or left alone) and `Tombstone.requester_ref`
  with `Memory.delete(requester=...)` / `Memory.tombstone_requested_by` (the erasure requester, stored as a pseudonym
  only; older tombstones still load).

### Changed
- **Attribution safety.** A value query over attribution-only evidence no longer ends in a `commit` to a `belief_of`
  candidate: the policy asks and gives no `assertion` (`kernel_status` and the candidates are unchanged, so the `Answer`
  contract is not changed). The agent tool API says the content is unknown.
- **Product-profile authority (default, pending author confirmation).** In the `open-world` profile a `correct` that
  fails the authority check is recorded as an `allege` with no effect (design v0.3). `revise-stream-v1` is unchanged.

## [0.0.1] - 2026-10-04

### Added
- Placeholder release that reserves the package name on PyPI. It contains no functionality.
