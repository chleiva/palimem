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
