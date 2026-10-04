# Items for the author's review (running list)

Started 2026-10-05 while the author was away. Each item is something decided by default, a finding that touches a decision, or a risk. Nothing here changes a decision the author already made.

## Needs a decision or an action

1. **Dated pre-registration note (outside this repo).** The compat profile must set `acting_reports_must_be_live = false` (S-02): the frozen sets contain 387 retracted corrections that still withdraw their target in the paper, and 84 of 314 comparable streams differ otherwise (Lane D). The note belongs in the study repo's `docs/PREREGISTRATION.md` addendum (`~/palimpsest`), which I did not touch.
2. **Trusted Publishing setup on pypi.org** (once, manual): see `docs/RELEASING.md` when Lane K lands. The `.env` PyPI token was account-wide: revoke it.
3. **GitHub repo settings** to enable by hand: private vulnerability reporting, Dependabot alerts.

## Defaults I chose (change any of them)

| # | Default | Where |
|---|---|---|
| 1 | `recorded_at` lives on `LogEntry` with `lsn` and the hash chain, not on `Report` (v0.3 lists it on `Report`) | `docs/TYPES.md` deviation 1 |
| 2 | Paper source-level retract is expanded in the compat converter into per-report withdraws of already-ingested asserts; no source-scope withdraw in the contract | Lane B converter |
| 3 | Compat profile sets `inertia: true` on **all** attributes (the deposited code persists every attribute) | S-08, Lane D finding 3 |
| 4 | `required_generation` is a column on `current_belief`, keeping `beliefs` append-only | STORAGE.md §9, Lane C2 |
| 5 | `AdmissionDecision` carries the effective cue and withdrawals beside `AdmissionRecord` (the record has no field for "failed `correct` kept as an assert") | Lane D |
| 6 | `Who.any` means any non-agent principal; the agent invariant for wildcard grants is enforced at evaluation time | Lane D |

## Risks and open points

- Deletion in the first store release only flagged versions and left derived values in stored belief JSON (privacy hazard until T-C8 lands; Lane C2 is on it).
- Settings 2 and 3 are not covered by the differential harness or the authority-coincidence check (T-J5).
- Agent benchmark: only 2 abstain and 1 revalidate scenarios; no second annotator for gold actions (Lane J).
