# Extraction: design and status (T-G1 built; T-G2 and T-G5 design notes)

Lane G · 2026-10-05 · Code: `src/palimem/extract/` · Evaluation: `docs/eval/EXTRACTION_GATE.md`, `bench/extract/`

## 1. Trust model: the model proposes claims, the host binds identity

```
text ──► prompt (data framing) ──► model ──► strict parse ──► build_reports (host binding) ──► Report
                                     │            │                    │
                              ledger authorises    rejects semantics,   source, origin, origin_group, actor,
                              before the call      flags identity       raw_ref, target id, extractor stamp:
                              (hard $20 cap)       fields               all from ExtractionContext, never the text
```

Three rules carry the security properties (docs/THREAT_MODEL.md T-06, T-07, T-09; docs/API_TRUST_BOUNDARY.md):

1. **The output grammar has no identity field.** A claim is `cue, entity, attr, proposition, valid_from, valid_to, target_hint, span`. If a model emits `source`, `origin`, `origin_group`, `actor`, `authority`, a target `id`, etc., the claim is **rejected** (never coerced) and the result is flagged `identity_fields_seen`. Nothing in `build_reports` reads identity from model output.
2. **Authority-bearing cues are off by default.** `correct`, `withdraw` and `dispute` from extracted text are refused (`cue_not_permitted`) unless the host registers the connector for them via `ExtractionContext.allowed_cues`. Targets are named by a *hint* (`entity`, `attr`, `value`) that the host resolves to a report id with `resolve_target`; an unresolved target is rejected, not guessed.
3. **Evidence must be quoted.** Every claim carries a `span` that must occur in the text (whitespace/case-insensitive); an invented span is rejected (`unsupported_span`). That bounds hallucinated evidence and gives `raw_ref` something to point at.

Other host-side checks: first person resolves only to a host-provided `subject_entity` (else `unresolved_pronoun`); declared schema attributes only, with proposition form checked against the attribute class (`undeclared_attr`, `form_mismatch`); an attributed claim (`belief_of`) is forced to origin `attributed` and never upgraded from an agent origin; stated valid time outside the connector's plausibility bounds is **dropped with a note**, not trusted (T-09).

**Repair policy: repair syntax, never semantics.** The only local repair is syntactic (a markdown fence, or prose around one JSON object). Semantically wrong claims are rejected with a reason. An optional single re-prompt (`max_repairs=1`) is available for whole-output failures; it is billed as a separate call and carries only our own validation message, never model text, so it cannot amplify an injection.

## 2. What T-G1 delivered

| Module | Role |
|---|---|
| `claims.py` | `ExtractedClaim`, `TargetHint`, `Rejection`; stated-granularity dates (`YYYY`, `YYYY-MM`, `YYYY-MM-DD`) |
| `parse.py` | strict decode, identity-field detection, span check, syntactic repair |
| `context.py` | `ExtractionContext`: the only way identity and authority enter |
| `build.py` | `build_reports`: host binding and all policy checks |
| `prompt.py` | deterministic prompt, delimiter neutralisation, `prompt_hash` (template + schema) |
| `llm.py` | `LLMExtractor`, `Transport` protocol, `BedrockConverseTransport`, `OpenAICompatTransport`; every call through `palimem.costs` |
| `passthrough.py` | `TypedPassthrough`: the no-LLM path, same strict grammar and host binding |

**Cost safety.** `LLMExtractor` calls `CostLedger.authorize` before any request with a pessimistic input estimate and the per-model output ceiling; an unknown model or a call that would exceed the cap is refused before anything is sent. A transport that definitely did not send (`TransportNotSent`) cancels the reservation; any other failure charges the worst case. The real transports additionally require `PALIMEM_ALLOW_PAID_CALLS=1`, and the optional `boto3`/`openai` imports are lazy so the core stays standard-library only. Tests use fake transports only.

**Per-report stamp.** `Extractor(model, version=PROMPT_VERSION, prompt_hash)` is attached to every report. The hash covers the template version, the system prompt (including the declared schema) and the user template, not the text, so a prompt or schema change shows up as a different hash and can be diffed report by report.

## 3. T-G2 design notes: the production extractor service

- **Default models** (Amazon Bedrock, us-west-2, `converse`): `openai.gpt-oss-20b-1:0`, `mistral.ministral-3-14b-instruct`, `mistral.ministral-3-8b-instruct`. Pin exact ids; never a floating alias (T-38). Temperature 0. gpt-oss returns a `reasoningContent` block that the transport skips; its reasoning tokens are billed as output, hence the 1,500-token ceiling.
- **Replay diff on the Setting 2 inputs (G2).** The study's Setting 2 extractor outputs exist only as cached outputs; they are **not re-runnable** within the $20 cap and the original extractor is not one of the three target models. So the G2 criterion "empty report-by-report diff against the deposit through the adapter" applies to the *cached* study outputs fed through `TypedPassthrough` and the v1 adapter (T-E3), while each target model gets a **separate per-model diff report** (reports gained, lost, changed) against that cache. A model run is never presented as reproducing the deposit.
- **Prompt changes are versioned events.** Any template or schema change bumps `PROMPT_VERSION` or changes `prompt_hash`; both are covered by the G-X procedure (dev iteration, one test run per hash).
- **Concurrency and rate limits:** sequential by default; the ledger is process-safe (file lock), so concurrent workers share one cap. Throttling errors are *not* retried inside `LLMExtractor` (a retry is a new billable call and must be an explicit host decision).
- **Failure taxonomy** returned in `ExtractionResult.rejections`: `invalid_json`, `invalid_shape`, `invalid_claim`, `missing_span`, `unsupported_span`, `identity_field_in_output`, `cue_not_permitted`, `undeclared_attr`, `form_mismatch`, `unresolved_pronoun`, `target_unresolved`, `origin_requires_attribution`, `invalid_report`. Rejections are returned, never dropped silently, so the harness can count them.
- **Context window.** Items are single messages. Long documents need chunking with a stable `raw_ref` per chunk and entity carry-over; not designed here.

**Status of the live transport (2026-10-05).** `BedrockConverseTransport` has been exercised against Bedrock for all three models (smoke call plus one dev pass each; results and cost in `docs/eval/EXTRACTION_RESULTS.md`). What the run established about the transport itself:
- gpt-oss's `reasoningContent` block is skipped and only `text` blocks are the answer, as designed; `TransportResponse.stop_reason` now carries Bedrock's `stopReason` so a truncated answer is visible. **3 of 69 gpt-oss requests hit `max_tokens` (1,500) with an empty answer**, so the ceiling is too low for hard items; any change to it is a cost-model change.
- `RecordingTransport` / `ReplayTransport` (`palimem.extract`) record the raw responses to a JSONL cache (answer text, token counts, stop reason; never prompts or credentials) and replay them offline for free. Re-scoring a recorded run therefore needs no network and no ledger spend (`bench/extract/extract_dev_report.py`).
- The ledger is shared across processes and worktrees through `PALIMEM_LEDGER`; `extract_run.py --cap-usd` applies a lower cap for one run to the shared ledger's total exposure.
- `extract_run.py --retry-once` retries an item once if the *call* raised; it is a runner option, not behaviour of `LLMExtractor` (which still never retries a throttled call on its own). In the first run it never fired.

## 4. T-G5 design notes: temporal expression normalisation

**Recommendation: the model proposes, a deterministic checker verifies.** The prompt already asks for dates at the stated granularity resolved from the observation date. A small rule-based resolver (`palimem.extract.temporal`, to be built) independently re-derives the date for the closed set of expressions it understands: `yesterday`, `N days ago`, `last month`, `last year`, `this year`, `since YYYY[-MM]`, `until YYYY[-MM]`, `from X to Y`, `on D Month YYYY`, `in Month YYYY`, `the end of YYYY`. When resolver and model **disagree, valid time is dropped with a note** (a wrong time is worse than none: it splits segments and fakes retrospective corrections, T-09); when only the model produced a time for an expression the resolver does not know, it is kept but marked unverified.

Conventions to keep fixed (and to decide in S-09): `valid_from` is the **first instant of the stated period** and `valid_to` for an explicit end ("until June 2023") is the stated period at the same precision; one precision per claim; "the end of 2022" is year precision; ambiguous expressions ("last week", "recently", "a while ago", "next spring") produce **no valid time** rather than a guess; the observation date is the source's `observed_at`, never `recorded_at`; no time zones below day precision. The deposited oracle implements neither `until` nor `interval` cues (S-09), so claims with `valid_to` have no oracle in 0.1; they are extracted and stored but excluded from the kernel's temporal reasoning until S-09 lands.

**Evaluation.** The current temporal slice (14 items, 7 per split) is too small to gate anything. T-G5 should add at least 60 temporal items (including the ambiguous ones that must yield no time, and leap/month-end cases) before any temporal threshold is declared, with the same declare-before-run discipline.

## 5. Open points

- Whether hedged, speculative and future statements should be dropped (the current policy, which keeps unresolved claims out of the evidence log) or stored as low-trust reports (more recall, more attack surface).
- Whether a quarantined connector's extracted `change` cue should be recorded as extracted-versus-source-stated (T-07 proposes it); the report type has no field for it today.
- Entity resolution is out of scope here (T-G3): the extractor emits entity surface forms; the evaluation set already carries alias groups for it.

## 6. Prompt-iteration protocol (G3): declared 2026-10-05, BEFORE any revision was run

Why: the first live pass (`docs/eval/EXTRACTION_RESULTS.md`, prompt `palimem-extract/1`) showed all three models failing the declared dev thresholds, with much of the loss in format compliance rather than in wrong extractions. The dev split has 69 items, so tuning on it overfits easily. This section fixes what may change, how revisions are chosen and what is reported, so the result cannot be steered after seeing the numbers.

**Scope.** Dev split only. The test split is NOT run (the author has not confirmed the G-X thresholds), `bench/extract/gate.json`, `gate.sha256` and `TEST_SPLIT.sha256` are not edited, and no gold is edited. The frozen baseline **R0** is `palimem-extract/1` (hash `455707905847…`) with the results already recorded under `bench/extract/runs/2026-10-05/`; it is not re-run. Template `palimem-extract/1` stays byte-for-byte as it is (so the 2026-10-05 cache still replays offline); every revision is a new template version with its own hash.

**At most three revisions (R1, R2, R3).** Each is a new `PROMPT_VERSION` string and a recorded prompt hash. Allowed changes, and only these:

- **(a) Grammar and format section.** Explicit grammar for `correct`, `withdraw`, `dispute` and `change`, the exact reply shape (`{"claims": [...]}`, an empty list when there is nothing to extract), date-granularity rules with examples, and worked examples. **Worked examples must not be drawn from dev or test items**: a test checks that no example sentence, and no (entity, attribute, value) triple used in an example, occurs in either split. No rule may be written to fit a named item.
- **(b) One repair re-prompt.** Triggered only when parsing fails: the whole output is invalid (not JSON, wrong shape) or a claim is rejected as `invalid_claim` (a grammar violation). Never for `unsupported_span`, `missing_span` or `identity_field_in_output` (those are semantic or hostile, not format). The re-prompt carries only our own validation messages and never model text. The repaired reply replaces the original only if it has strictly fewer format rejections; otherwise the original is kept. Both calls are billed. Content is never coerced.
- **(c) A higher output ceiling for gpt-oss** (1,500 to 3,000 tokens), with the cost model updated. Reasoning tokens are billed as output, so this can only add cost on the items that were truncated.
- **(d) Temporal diagnosis.** Classify the valid-time errors and fix only a cause that lies in normalisation, scoring or prompt format (below).

**Not allowed:** tuning to individual dev items, editing gold, touching thresholds or the scorer's definitions, changing the host policy (`allowed_cues`, identity binding), changing the schema.

**Plan.** R1 = (a) + (c). R2 = R1 + (b). R3 (optional) = one further grammar change, justified only by a failure *class* that remains after R2 and costs a model at least 3 claims, never by an individual item. Stop early if a revision does not improve claim F1 for any model.

**Evaluation.** Each revision is evaluated once per model on dev, temperature 0, with the one transport-error retry of `extract_run.py --retry-once` (no other retries).

**Choice rule (declared now).** Per model, among R0 and the revisions actually evaluated, choose the revision with the highest **claim F1**, subject to its **injection-compliance rate and wrong-value rate not exceeding R0's** (point estimates). Ties go to the revision with fewer expected calls. If no revision satisfies the constraint for a model, R0 stays.

**Spend.** This task may spend at most **$1.50** in total on the shared ledger (global cap $20; $0.035 spent before this task). A written estimate precedes every paid batch; a batch that would exceed the cap is not run. Expected spend is below $0.10 per revision across the three models.

**Reporting.** Every revision is reported, not only the best, with confidence intervals and failure classes. **Dev numbers after tuning are optimistic**: the revisions were chosen by looking at dev results, on 69 items, with 14 change claims, 7 injection items and 8 empty-expected items. A gate result needs the single-use test run, which stays the author's decision.

### 6.1 Temporal diagnosis (offline, from the 2026-10-05 cache, no spend)

The 7 dev items with a stated time (x075, x077, x079, x081, x083, x085, x087) were read against the three models' outputs. Valid-time errors fall into four classes:

1. **Granularity padding (the dominant class).** The text states a month or a year and the model returns a full day: `2024-03-01` for "March 2024", `2025-01-01` for "last year", `2026-01-01` for "this year", `2021-06-01` and `2023-08-01` for "June 2021 to August 2023" (gpt-oss), `2022-12-31` for "the end of 2022", `2020-12-31` for "until 2020". Counts: Ministral 8B 4 claims, gpt-oss 2, Ministral 14B 1 (it resolved "last year" to the observation day, `2025-03-15`). The prompt already says "at the granularity the text states", but gives no example, and the scorer compares valid-time strings exactly. **Cause: prompt format. It is not a scoring artefact**: precision determines the interval a segment covers, and a day-precision claim asserts more than the text says, so exact comparison stays. Fix: explicit rule and worked examples, in R1.
2. **"Until X" / "since X" semantics.** "Gus lived in Gothenburg until 2020" and "Jun Park was at Veltran until the end of 2022" are statements of a value with an end, not a change or a negation: Ministral 14B returned `not_value` with a `change` cue (valid time right on x081, inverted on x085), Ministral 8B did the same on x081. Cause: grammar clarity. Fix: a rule "until X → assert the value with `valid_to`; since X → assert with `valid_from`; use `change` only when the text says something changed", in R1.
3. **Cue errors on dated statements** (gpt-oss `change`→`assert` on x083 and `assert`→`change` on x085; Ministral 8B `assert`→`change` on x077). Not temporal; same grammar remedy.
4. **Format and truncation:** one gpt-oss truncation (x081, `invalid_json`) and one Ministral 8B `invalid_claim` (x085). Remedies (b) and (c).

Not a temporal problem and not changed: value strings such as `data team` vs the gold `data` (x087, two models). That is a gold convention about generic suffix words, and tuning to it would be tuning to an item.
