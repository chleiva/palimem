# Trust-boundary conformance fixtures

Language-neutral JSON. Each file is one scenario for the host/agent API split in `docs/API_TRUST_BOUNDARY.md`. They are specifications: no runner exists yet (it lands with the facade, T-F2). `tests/test_trust_boundary_fixtures.py` only checks that every fixture is well-formed.

## Shape

```jsonc
{
  "id": "tb-NN-slug",
  "title": "...",
  "covers": ["R6", "S-07"],          // rule ids in API_TRUST_BOUNDARY.md and/or decision ids
  "requires": ["verify_log"],        // optional backend capabilities; skip the fixture if absent
  "spec_dependencies": ["..."],      // assumptions the expectation rests on (open decisions)
  "notes": "...",
  "setup": {
    "profile": "open-world",
    "schema":      { "<attr>": { "class", "inertia", "completeness" } },
    "connectors":  { "<connector id>": { "source_id", "source_class", "origin_group", "kind", "principal?" } },
    "authority":   [ AuthorityRule ],                    // S-07; [] = built-in defaults only
    "session":     SessionContext,                       // agent_principal, allowed_attrs, policy_version, ...
    "events":      [ { "event_id", "connector", "principal?", "raw" } ],   // host-held event ledger
    "extractor_stub": { "<event_id>": [ extractor output records ] },     // deterministic stand-in
    "log":         [ prior reports, each with "ref" (a symbolic name) and a recorded_at ],
    "now":         "<timestamp the steps run at>"
  },
  "steps": [
    { "tier": "agent" | "host", "call": "<method>", "args": { ... },
      "as_session": { "agent_principal": "..." }         // optional per-step session override
    }
  ],
  "expect": {
    "steps":        [ { "result": Matcher } | { "error": { "code": "..." } } ],   // one entry per step
    "log_delta":    [ Matcher ],     // log rows appended by all steps, in order
    "audit_delta":  [ Matcher ],     // audit rows appended, in order
    "answers":      [ { "query": Query, "match": Matcher } ],   // evaluated after all steps
    "equal_results":[ [i, j] ]       // step results i and j must be identical
  }
}
```

## References

- `"ref"` names a prior log report. `"$r1"` in later fields means "the id of report `r1`". `"$r1.lsn"`, `"$r1.entry_hash"` read fields of that row.
- Queries in `answers`: `{"kind": "belief", "key": [entity, attr], "valid_at?", "belief_as_of?"}`, `{"kind": "reports", "filter": {...}}` (returns rows, `count`, and for a single-row filter its fields such as `withdrawn`, `admission`), `{"kind": "source_class", "source_id"}`.

## Matchers

A matcher is a **subset match**: every field present in the matcher must equal the actual value; fields absent from the matcher are not checked. Lists match element-wise in order and by length. Special string values:

| Value | Meaning |
|---|---|
| `"$any"` | any value, but present |
| `"$absent"` | the field must be missing or null |
| `"$not:X"` | present and not equal to `X` |
| `"$eq:$r1.entry_hash"` | equal to the referenced value |
| `"$gt:$r1.lsn"` | greater than the referenced value |
| `"$after:<timestamp>"` | timestamp strictly later |

## Conventions

- A rejected or ignored field produces a `field_ignored` notice and a `trust_downgrade` audit row; the call itself still succeeds (R2).
- Denial of an agent operation returns `{"effect": "logged_only"}` and, where the target resolves, an `allege` log row (R6, R7).
- Fixtures never contain secrets or real personal data.
