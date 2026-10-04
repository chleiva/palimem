# Changelog

All notable changes are recorded here, following [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The package follows the versioning policy in [`docs/VERSIONING.md`](docs/VERSIONING.md). During 0.x any `0.MINOR`
may change the contract; such changes are marked **BREAKING** with a migration note.

## [Unreleased]

Work in progress on the way to 0.1 (see `docs/TASKS.md`); nothing here is released.

### Added
- Contract types and generated JSON Schemas (`palimem.types`, `schemas/`).
- Admission, authority and policy layers (`palimem.admission`, `palimem.policy`).
- Storage layer: backend protocol, in-memory and SQLite backends with a salted hash-chained log (`palimem.store`).
- Differential harness against the PALIMPSEST study, frozen-set guard and cost ledger (`harness/`, `palimem.costs`).
- Threat model, security policy, decision records S-01 to S-13, and the RETRACT-ACT benchmark design.

## [0.0.1] - 2026-10-04

### Added
- Placeholder release that reserves the package name on PyPI. It contains no functionality.
