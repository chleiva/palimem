# Conformance suite (T-A5)

The acceptance suite every implementation lane must pass. Language-neutral JSON: an implementation in
any language proves conformance through a thin adapter that speaks JSON, without importing palimem.

```
tests/conformance/
  fixture.schema.json       JSON Schema of a fixture (generated)
  fixtures/independent/     the 22 independent acceptance rows of design v0.3 (+ 3 variants)
  fixtures/decisions/       S-06 budgets and collapsing, S-10 rule exceptions, S-12 explain, S-04 compat profile, compat checks
  fixtures/security/        behavioural SEC-xx fixtures + index.json (disposition of every SEC-01..44)
  ../fixtures/trust_boundary/   tb-01..20, loaded by the runner, not copied (run through the agent tool API by `tests/trust_boundary/runner.py`; ratchet in `tests/trust_boundary/status.json`)
  runner.py                 executes fixtures against an Implementation; prints a summary by gate and area
  matcher.py                the subset matcher and reference resolution
  checks.py                 non-scenario checks that read the frozen study data
  build_fixtures.py         regenerates the JSON from dsl.py + fx_*.py (CI fails if the files are stale)
```

```
python -m tests.conformance.runner                     # against the built-in ReferenceStub (every executable fixture is skipped)
python -m tests.conformance.runner --impl pkg.mod:Cls  # against a real implementation
python -m tests.conformance.runner --list              # one line per fixture
python -m tests.conformance.runner --include-pending   # also run pending-decision and shell fixtures
python -m tests.conformance.build_fixtures [--check]
```

## The independent-suite property

The expected answers were derived **by hand** from design v0.3, the decision records and the threat model,
never by running a kernel; each fixture's `why` field states the reasoning so a reviewer can check the
expectation without any code. Whoever writes the kernel must not edit an expectation to make a fixture
pass: change a fixture only through a decision record, and regenerate with `build_fixtures`.

## Fixture format (version 1)

Common fields: `version` (1), `kind` (`scenario` | `harness_check`), `id` (equals the file stem), `title`, `gate`
(`G0` | `G1` | `G2`), `area`, `status`, `status_reason` (required unless `active`), `covers` (design rows, SEC ids,
S-records), `requires` (capabilities), `spec_dependencies` (open decisions the expectation rests on), `why`,
`source`.

`status`: `active` (must pass), `pending-decision` (the behaviour rests on a proposed, not ratified, mitigation or an
undecided point: skipped by default), `shell` (the harness for it does not exist yet: skipped by default).

### Scenario

```jsonc
{
  "setup": {
    "profile": "open-world" | "revise-stream-v1",
    "schema":  { "<attr>": { "class": "single_changeable", "value_type": "entity", "inertia": true,
                              "completeness": "open|declared|by_enumeration",
                              "rule": { "reads": [...], "fn": "...", "exceptions": [...] } } },   // compact Attr
    "sources": { "<source id>": { "class": "trusted", "origin_group": "g_x", "valid_time_bounds": {...} } },
    "authority": [ AuthorityRule ],           // grants beyond the built-in defaults
    "limits":    { "environment_budget": 7 }, // initial limits (the default budget is 7)
    "scopes":    { "<entity>": "<privacy scope>" },
    "expect_load_error": { "reason": "..." }  // the schema must be refused at load; `ops` is then empty
  },
  "ops": [ { "op": "...", "name?": "...", "at?": "<timestamp>", "expect?": <matcher>, "repeat?": N, ... } ],
  "expect": { "equal": [ { "a": "<name>", "b": "<name>", "fields": ["path", ...], "negate?": false } ] }
}
```

The compact `schema` expands to `palimem.types.Attr` (a test decodes every one). `rule.exceptions` is **not** in
`palimem.types.Rule` yet; S-10 needs it. Reports inside `append` are exactly the fields of `palimem.types.Report` minus the
log-assigned ones (`id`; `lsn`, `recorded_at` and the hash chain live on the log row, S-05).

### Operations and their results

An op's result is JSON; `expect` (a matcher) is checked against it right after the op. `at` is the
log's clock for that op (an implementation must let a test control `recorded_at`). `name` makes the
result available to later ops and to `expect.equal`; `ref` (on `append` / `observe_text`) binds `$ref` to the
appended report.

| op | arguments | result |
|---|---|---|
| `configure` | `limits` (`environment_budget`, `traversal_budget`, `revision_budget`, `explanation_budget_max`, `max_value_length`, `max_raw_ref_length`), `collapse` | `{ok}` |
| `append` | `ref`, `report`, `idempotency_key?`, `crash?: {point}` | `{report_id, lsn, generation, recorded_cue, admission: {outcome, reason, admission_version}, duplicate, notes?}` or `{crashed: true}` or `{error: {code}}` |
| `observe_text` | `connector`, `text`, `extractor_stub: [report...]` (deterministic stand-in for the extractor) | `{reports: [{report_id, lsn, generation}]}` |
| `delete` | `target`, `actor`, `reason` | `{tombstone: {id, actor, reason, entry_hash?}, erasure_report}` |
| `merge` / `unmerge` | `merge_id`, `entities`, `canonical`, `actor` | `{merge_id, recomputed_keys: [Key]}` or `{error}` |
| `subscribe` | `plan_id`, `keys`, `as?` | `{subscription_id}` or `{error}` |
| `deliver` | | `{events: [{event_id, plan_id, key, old_version, new_version}]}` |
| `ack` | `from: <name of a deliver result>` | `{acked}` |
| `crash` / `recover` | `point` | `{crashed: true}` / `{recovered: true}` |
| `checkpoint` / `restore_backup` | `checkpoint_name` | `{name}` / `{restored}` |
| `tamper` | `tamper: {target: log_row|belief_row, ...}` (test hook) | `{ok}` |
| `complete_jobs` | `generation?` | `{completed: [Key], skipped: [Key]}` |
| `query` | `query: Query` (`valid_at`, `belief_as_of` as LSN or timestamp, `profile`, `explanation_budget`), `as?` | an `Answer` (Resolved or ResourceLimited) plus the virtual fields below |
| `explain` | `key`, `mode`, `depth`, `valid_at?`, `belief_as_of?`, `as?` | an `Explanation` (`state`, `environments`, `segment`) |
| `reports` | `filter` (`id`, `cue`, `key`, `actor`, `target`) | `{count, rows: [{id, lsn, cue, key, actor, origin, source, proposition, target, withdrawn, tombstone, entry_hash, admission: {outcome, reason, confirmed_by, admission_version}, valid_from, extractor}]}` |
| `verify_log` | `from_lsn?`, `to_lsn?`, `expected_head?` | `{ok, first_bad_lsn?, rows?: [{lsn, linked, content_verified}]}` |
| `verify_beliefs` | | `{ok, keys?}` |
| `export_head` | | `{head_hash, lsn}` |
| `find` | `text`, `as?` | `{keys: [Key]}` |
| `resolve_raw_ref` | `raw_ref` | `{...}` or `{error}` |
| `subscriber_effects` | | answered by the **runner**, not the implementation: `{p1: {events_seen, effects_applied}}` from its reference idempotent subscriber |

A generation number (`generation` on an append result) is the store generation the append produced.
`repeat: N` runs an op N times with `{i}` (1..N) substituted in every string.

### Matchers

A matcher is a JSON value compared with the actual JSON value. Objects match as **subsets** (keys absent from the
matcher are not checked); lists match element by element and by length. `true` is not `1`.

| form | meaning |
|---|---|
| `"$any"` / `"$absent"` | present (any value, `null` included) / missing or `null` |
| `"$r1"`, `"$r1.lsn"`, `"$r1.generation"`, `"$name.path.0.field"` | the id / field of a bound report ref or named op result |
| `"$eq:$ref"`, `"$not:$ref"`, `"$gt:$ref"`, `"$after:<timestamp>"` | comparison with a reference or timestamp |
| `{"$unordered": [...]}` | same length, any order (perfect matching) |
| `{"$contains": m}`, `{"$none": m}` | some / no element matches `m` |
| `{"$len": n}`, `{"$lt"/"$lte"/"$gt"/"$gte": n}` | length / numeric comparison |
| `{"$oneof": [m1, m2]}` | any alternative matches |

**Virtual fields** the runner adds to every `query` result: `_candidates` (every candidate the Answer carries,
deduplicated by id; match on `{"form": {...}}`, the id being derived), `_environments` (the `provenance`
environments, each sorted, the list sorted) and `_support_max_len` (the longest support list in the embedded justified view).
An unresolved `$ref` in a matcher is a failure, never a silent pass.

Names `any` and `absent` are reserved; an op `name` or `ref` may be bound only once per fixture.

### Harness check

`kind: harness_check` has no `ops`: the runner calls a Python function registered in `checks.py`
(`compat-01-authority-coincide` reads the frozen Setting 1 streams). It returns pass, fail, or skip when its data is absent.

## Capabilities

An implementation declares what it supports; a fixture whose `requires` is not met is skipped (the
reason names the capability). `budget_control`, `crash_injection`, `completion_jobs`, `outbox`, `merge`, `delete`, `extractor`,
`hash_chain` (a backend without the chain stays conformant), `tamper_hook`, `backup_restore`, `collapse_toggle`,
`principal_scopes`, `quotas`, `raw_ref_resolver`, `profile_revise_stream_v1`.

## Writing an adapter

```python
class Implementation:
    name = "palimem-sqlite"
    def capabilities(self) -> set[str]: ...
    def start(self, setup) -> None | dict:   # fresh store; return {"error": {...}} to refuse the setup
    def execute(self, op) -> dict:           # one op in, one JSON result out; return NotImplemented for unknown ops
    def close(self) -> None: ...
```

Run it with `--impl yourpkg.adapter:Implementation` or `PALIMEM_CONFORMANCE_IMPL=...`. `NotImplemented` (from `start`
or `execute`) is reported as *skipped with the reason*, never as a failure, so the suite can grow ahead of the code.

## Gates

| Gate | Fixtures |
|---|---|
| G0 | contract-level refusals at schema load (`s10-05`, `s12-03`) |
| G1 | kernel, store, admission, budgets, barriers, recovery, hash chain: every independent row except 9 and 17 (and the extraction variant of 19), plus the decision and security fixtures |
| G2 | rows that need phase-2 components: merges (`ind-09`, E2.1), outbox (`ind-17`, E2.5), extraction (`ind-19b`, E2.1), scoped subscriptions/finds |

Design v0.3's G1 text says "every independent test green" but rows 9 (merges), 17 (outbox) and the extraction half of 19 need
phase-2 components. They are tagged G2 here; the design should say so.

## Known underspecified points (each fixture names them in `spec_dependencies`)

1. **Segment-local statuses** (`ind-12`): is A-ERR evaluated per segment (the design's intent) or per key (the paper)?
2. **How to ask for an attribution** (`ind-15`, `ind-19`): `Query` has no selector for "the attribution" versus "the content".
3. **Several compatible negative candidates** (`ind-18`): `established_false` or `unresolved`?
4. **Member-only evidence under open completeness** (`ind-13b`): established set, or weaker?
5. **"Visited"** in design row 14 means recomputed, whereas row 20's marker concerns traversal; the budgets are not named by the design.
6. **Store-wide versus component-scoped dirty marker** (`ind-20` vs `sec-22`, H4).
7. **`Rule` has no `exceptions` field** (S-10), and **merge is not a `Power`** (S-07; `ind-09`, `sec-18/19`).
8. **A trusted injection with a `change` cue** (`sec-40b`): the decided gate's wording does not say whether an earlier-anchored report "contradicts".
9. **`verify_log` covers the log only**: a belief-recomputing verify (`sec-25b`) is proposed, not decided.
