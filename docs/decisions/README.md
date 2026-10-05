# Decision records (T-A1)

One record per spec ambiguity from `the project review` §3 H2. Status legend: `accepted` = decided by the author; `amended` = decided with changes (see the record's Decision block); `accepted*` would mean accepted by blanket rule but still awaiting an explicit line on a contract change; none remain (all six were decided 2026-10-04, see `../CONTRACT_PENDING.md`).

Records are grounded in the deposited study code (`~/palimpsest`, v1.0): `revise_stream/{model,timeline,gold,oracle_v1,generator}.py`, `palimpsest/core.py`, `docs/SEMANTICS.md` v0.3.

| ID | Topic | Recommendation (short) | Blocks G1? | Status |
|---|---|---|---|---|
| [S-01](S-01.md) | `confirm` cue vs derived confirmation | Remove `confirm`; confirmation always derived | no | accepted |
| [S-02](S-02.md) | Authority: origin vs source; paper's retract rules | **Amended:** compat profile origin-based; product authority = source/principal, `origin_group` for corroboration only; agents act only on their own agent-origin reports | **yes** | amended |
| [S-03](S-03.md) | `blocked` vs `quarantined` | Two classes: `excluded` and `quarantined` | no (unexercised) | accepted; **ruling 2 (2026-10-05): `exclude_source` is an admission operation** |
| [S-04](S-04.md) | One status enum; paper's closed-world asymmetries | Five statuses; `possible` is adapter-only; profile defined per key class | **yes** | accepted; **ruling 4 (2026-10-05): negative evidence in the product kernel** |
| [S-05](S-05.md) | Belief axis | LSN is canonical; `recorded_at` is an index; τ→LSN batch map | **yes** (adapter) | accepted |
| [S-06](S-06.md) | Above the environment cap | `ResourceLimited(environment_budget)`; default budget 12 after the `oracle_v2` cross-check (was 7); collapsing only for provably interchangeable reports | no | accepted |
| [S-07](S-07.md) | Principal/authority model | Typed principal ids + declarative grant table; agents can never be granted withdraw/correct on external evidence | no | accepted |
| [S-08](S-08.md) | Inertia | **Amended:** keep the `Attr.inertia` boolean; profile sets it per the paper | **yes** | amended |
| [S-09](S-09.md) | Partial dates, `valid_to`, negative evidence | Staged; extended oracle written separately; none in 0.1 | no | accepted |
| [S-10](S-10.md) | Rule exceptions in open world | Exceptions follow the exception attribute's completeness | no | accepted, **narrowed by ruling 10 (2026-10-05): exceptions are reserved, strict rules only** |
| [S-11](S-11.md) | Attributed reports | Uniform rules; nesting depth ≤ 1 | no | accepted |
| [S-12](S-12.md) | `explain` depth / provenance contract | Depth parameter, default full closure; flatten = oracle set | **yes** | accepted |
| [S-13](S-13.md) | Tombstone contents | Minimal pseudonymised tombstone; preserves the log hash chain with the original entry hash | no | accepted |

## Author decisions folded in (2026-10-04)

1. **Agent authority** (S-02, S-07, `API_TRUST_BOUNDARY.md` R6): an agent may withdraw or correct only reports it authored with agent-class origin, via the host-bound actor; never an `external_observation`; dispute only with a host grant, else `allege`; dispute-triggered quarantine deferred (default no).
2. **Log hash chain** (S-13, S-05, `VERSIONING.md` §6): a storage-layer property (`prev_hash`, `entry_hash` on the log row, salted), not a Report field; `verify_log(from, to)` is an optional-capability backend operation; tombstones keep the chain and carry the original entry hash.

## Findings that go beyond the proposal's description

- **S-02:** wholesale source retraction (by `registry`, ~35% of generated streams) has no counterpart in the design; A-SELF also fires for retracted or blocked corrections; cross-origin corrections have an effect (A-CORR) that `allege` would erase.
- **S-04:** the paper's closed world is per slot type and cardinality (single no-evidence → `unknown`; multi no-evidence → `established []`; yes/no absence → `established false`), so a blanket `declared(all)` profile is wrong.
- **S-06:** the generator caps keys at 7 reports; the design's default of 12 was beyond anything validated, and replay is exponential too (since cross-checked against `oracle_v2` on fresh streams: `docs/BUDGET_CROSSCHECK.md`; the default is now 12).
- **S-09:** `until`/`interval` cues and negative polarity are *not implemented* in the deposited model (`NotImplementedError`), so they have no oracle at all.

## How to decide

Edit the record's `Status:` to `accepted`, `rejected` or `amended`, add `Decided: <date>` and any amendment text at the end of the file, and update the table. Contract-affecting decisions also need an RFC under `docs/rfcs/` once G0 is frozen (see `docs/VERSIONING.md`).

The author's rulings of 2026-10-05, with the lane that implements each, are in [`RULINGS-2026-10-05.md`](RULINGS-2026-10-05.md). Lane S2 implemented rulings 1-3 and 10-15 (semantics); the amendments are at the end of S-02, S-03, S-04, S-08 and S-10.
