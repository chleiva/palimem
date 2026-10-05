# Contract types (T-A3) and JSON Schemas (T-A4)

`palimem.types` is the executable form of the data contract of the design document (v0.3) §Data and API, with
the author's decisions applied. Standard library only. Every record is a frozen, keyword-only dataclass
that validates in `__post_init__`, has `to_dict` / `to_json` (canonical) / `from_dict` / `from_json`, and
decodes strictly (unknown fields are rejected; nullable fields may be omitted).

**Canonical JSON:** sorted keys, no whitespace, UTF-8 (no ASCII escaping), no NaN/Infinity, timestamps as
UTC `YYYY-MM-DDTHH:MM:SS[.ffffff]Z` (any zone offset is accepted on decode and normalised), arrays for
tuples, `null` for absent optionals. `bool` is distinct from `int` (`true` ≠ `1`), so candidate ids differ.

**Schemas:** `schemas/*.schema.json` (draft 2020-12, self-contained) and `schemas/examples/*.json` are
**generated** by `python -m palimem.schemas --write` from `src/palimem/schemas.py`; CI runs
`python -m palimem.schemas --check` and fails on drift. The tests validate 250 random valid instances of
every type, and the examples, against the schemas, and check that bad records are rejected by both the
types and the schemas.

## Type map

| Type (module) | Design v0.3 section | Notes |
|---|---|---|
| `Key`, `Proposition` = `ValueProp` / `MemberProp` / `NotMemberProp` / `EnumerationProp` / `NotValueProp` / `BeliefOfProp` (`values`) | Report, Proposition | `EnumerationProp` is a set (deduplicated, canonically ordered); `[]` is explicitly empty |
| `Candidate` + `CandidateForm` = `ValueForm` / `SetForm` / `EmptyForm` / `NotValueForm` / `NotMemberForm` / `BeliefOfForm` (`values`) | Belief record | `id` = SHA-256 of canonical (key, form), derived and verified on decode; negative forms keep their content |
| `Report`, `Source`, `Extractor` (`report`) | Report | free of lsn / hash-chain / `recorded_at`; `id` is `None` until the log assigns it |
| `LogEntry` (`report`) | Storage layout | `lsn`, `recorded_at`, `report`, optional `prev_hash` / `entry_hash` |
| `AdmissionRecord` (`admission`) | Admission model | outcome, reason code, `admission_version`, `confirmed_by` |
| `Attr`, `Completeness`, `CompletenessScope`, `Interval`, `Rule`, `Schema` (`attr`) | Schema entry | `check_proposition_for_attr` is the pure form-vs-class check |
| `Principal` helpers, `KeyScope`, `Who`, `AuthorityRule`, `AuthorityTable`, `DEFAULT_RULES`, `REVISE_STREAM_V1_RULES` (`authority`) | Schema entry (authority), Write API | see decisions below |
| `Support`, `Segment`, `Belief`, `BeliefView`, `Pin`, `Dependency`, `InvalidatedBy`, `Versions`, `SemanticConfig`, `Inference` (`belief`) | Belief record | `Belief.lsn` added (S-05) |
| `Query`, `ExplainQuery`, `Explanation`, `Resolved`, `ResourceLimited`, `Answer`, `Inquiry`, `PolicyInfo`, `SegmentBounds`, `LastComplete` (`answer`) | Query and answer, Read API | output contract v2 |
| enums (`enums`), limits (`limits`) | throughout | `DEFAULT_ENVIRONMENT_BUDGET = 12`, `MAX_BELIEF_NESTING = 1` |
| `Query`, `ExplainQuery`, `Explanation`, `Resolved`, `ResourceLimited`, `Answer`, `Inquiry`, `PolicyInfo`, `SegmentBounds`, `LastComplete` (`answer`) | Query and answer, Read API | output contract v2. **No contract change from Lane Q:** an attribution-only answer keeps `kernel_status` and its `belief_of` candidates; the policy asks and gives no `assertion` (see `docs/PIPELINE.md`). Attributions are read apart through `Memory.attributions` (a host-level `AttributedClaim`, not a contract type). |
| enums (`enums`), limits (`limits`) | throughout | `DEFAULT_ENVIRONMENT_BUDGET = 7`, `MAX_BELIEF_NESTING = 1` |

## Decisions applied (all decided; there are no pending markers)

| Decision | Where it shows in the types |
|---|---|
| **S-01 (decided):** no `confirm` cue; confirmation is derived and is an admission record | `Cue` has no `confirm`; `AdmissionRecord(reason=confirmed, confirmed_by=…)` |
| **S-02 (decided):** compat profile origin-based; product authority source-based; `origin_group` never authority | `WhoKind.TARGET_SOURCE` (default) vs `TARGET_ORIGIN_GROUP` / `ORIGIN_GROUP`, which `validate_rules_for_profile` rejects under `open-world`; `default_authority_rules(profile)` |
| **S-03 (decided):** outcomes `admissible \| quarantined \| excluded` | `AdmissionOutcome`; paper `blocked` ↔ `excluded` / `source_blocked` |
| **S-04 (decided):** five statuses, `possible` adapter-only | `KernelStatus` has five values; `Segment` enforces the status ↔ candidate shape |
| **S-05 (decided):** LSN is the belief axis; `belief_as_of` is an LSN or a timestamp | `LogEntry.lsn`, `Belief.lsn`, `Query.belief_as_of: int \| datetime` (JSON integer vs string) |
| **S-06 (decided):** `environment_budget`; default budget 7, raised to 12 on 2026-10-05 after the cross-check | `ResourceLimitedReason.ENVIRONMENT_BUDGET`, `limits.DEFAULT_ENVIRONMENT_BUDGET` |
| **S-07 (decided):** principal kinds in the id prefix; typed grants; agent invariant; grant table versioned with the admission version | `PrincipalKind`, `check_principal`, `AuthorityRule`, `AuthorityTable.successor`; `AuthorityRule` raises on any agent grant of withdraw/correct over external evidence |
| **S-08 (decided):** `Attr.inertia` stays a boolean | `Attr.inertia: bool` |
| **S-11 (decided):** `belief_of` nests at most once, deeper nesting reserved | `limits.MAX_BELIEF_NESTING = 1`; schema `x-palimem-reserved` on `BeliefOfProp` |
| **S-12 (decided):** `explain(key, valid_at, mode, depth)`, default full closure; provenance = all subset-minimal environments over **base** reports | `ExplainQuery.depth: int \| None`; `Explanation`, `Resolved.provenance` |
| **S-13 / hash chain (decided):** storage-layer property, not a Report field | `LogEntry.prev_hash` / `entry_hash`, both optional |
| **Agent retraction (decided):** agents act only on their own agent-class reports | `DEFAULT_RULES[1]`: `who=target_actor`, `over_origins` ⊆ agent-class; no `AuthorityRule` can widen it for an agent principal |

## Deviations from design v0.3's field lists

1. `recorded_at`, like `lsn` and the chain, is on `LogEntry`, not `Report` (it is log-assigned metadata).
2. `Report.id` is `Optional` (`None` before `append`); `LogEntry` requires it.
3. `Candidate` carries its `key` (the id is a hash of key and form; the design's `{id, form}` could not be verified).
4. `Attr.authority` is a flat tuple of typed `AuthorityRule` grants, replacing the per-cue lists
   `{correct: […], withdraw: […], dispute: […]}`. Each rule names its powers. A global `AuthorityTable`
   sits below it (S-07).
5. `ResourceLimited.stale_dependency(key)` is the field `reason_key`, also used by `environment_budget`.
6. `Inference` is `{complete, reason}`; `Versions` holds three integers; `SemanticConfig` is a separate
   record that a semantic version number refers to.

## Choices where the design was ambiguous

* **Cue ↔ target ↔ proposition** (enforced in `Report`): assert/change take no target and need a proposition; correct needs both
  (the corrected value is asserted in the same notice); withdraw needs a target and no proposition;
  dispute/allege need a target and may carry a proposition.
* `origin = attributed` requires a `belief_of` proposition.
* `Segment` shape per status: `established` → value/set/belief_of candidate, no alternatives;
  `established_empty` → the `empty` candidate; `established_false` → a `not_value`/`not_member` candidate;
  `unresolved` → at least two alternatives and no established candidate; `unknown` → no candidates.
  `set` is non-empty (the empty set is `empty`).
* `Resolved`: an assertion is present exactly when `decision = commit` and must be a candidate of the
  justified segment; an inquiry is present exactly when `decision = ask`; `kernel_status` and the segment bounds must equal the
  justified view's.
* `Belief`: `inference.complete` iff `completed_generation = required_generation`, and completed ≤ required;
  segments are ordered and non-overlapping (half-open `[from, to)`); segment candidates belong to the belief's key.
* Wildcard `Who` kinds (`any`, `origin_group`, `target_*`) cannot be checked against principal kinds at load time.
  By contract they never match an `agent` principal for withdraw/correct over non-agent-class origins; admission (T-D2) must enforce it.

## API surface for the other lanes

`from palimem.types import …` exposes every name above. Pure helpers: `canonical_json`, `parse_json`,
`candidate_id`, `proposition_from_dict`, `candidate_form_from_dict`, `answer_from_dict` / `answer_from_json`,
`check_proposition_for_attr`, `parse_principal` / `check_principal` / `principal_kind`,
`default_authority_rules`, `validate_rules_for_profile`. `ValidationError` (a `ValueError`) is the only
exception types raise. No type touches a store, a clock, or a hash chain.

## Modules that build on the types

The types are the contract; these packages produce and consume them and are described in their own documents:

| Module | What it does with the types | Document |
|---|---|---|
| `palimem.extract` | `Extractor` protocol (text plus host context in, typed `Report`s out), `TypedPassthrough` (the no-LLM path), `LLMExtractor` over Bedrock and OpenAI-compatible transports (every call through `palimem.costs`), `build_reports` (the only place `source`, `origin`, `actor` and targets are bound: from the host's `ExtractionContext`, never from model output), strict claim parsing into the types. The output grammar has no field for source, origin, actor, authority, origin group or target ids | [`EXTRACTION.md`](EXTRACTION.md), [`eval/EXTRACTION_GATE.md`](eval/EXTRACTION_GATE.md) |
| `palimem.admission` | `LogEntry` in, `AdmissionRecord` out (outcome, reason, `admission_version`); evaluates `AuthorityRule`s | [`API_TRUST_BOUNDARY.md`](API_TRUST_BOUNDARY.md) |
| `palimem.kernel` | admitted reports in, `Belief` segments and `Support` environments out | [`KERNEL.md`](KERNEL.md) |
| `palimem.store` | persists `LogEntry`, `AdmissionRecord` and `Belief` versions behind the `Backend` protocol | [`STORAGE.md`](STORAGE.md) |
| `palimem.policy` | `Belief` view in, `decision` and `Resolved` fields out | [`PIPELINE.md`](PIPELINE.md) |


## Segment `unknown` and negative constraints (ruling 4 of 2026-10-05)

A `Segment` of status `unknown` names no established candidate but may list `alternatives` consisting only of negative candidates (`not_value` / `not_member`): denials that narrow the value without determining it (two compatible denials). `unresolved` still needs at least two alternatives and `established_*` still forbids alternatives. See `docs/decisions/S-04.md`.
