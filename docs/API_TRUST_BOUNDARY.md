# API trust boundary: host API versus agent tool API (T-A2)

Status: **draft for G0** (amended 2026-10-04 for the author's decisions on agent authority and log integrity) · Lane A · Resolves PROPOSAL.md concern C2 · Depends on `docs/decisions/S-01, S-02, S-03, S-07, S-13`

## 1. Why this exists

Design v0.3 shows `m.observe(text_or_report, source=..., origin=...)` and `m.withdraw(report_id, actor=...)`. If the caller is an LLM, it chooses `source`, `origin` and `actor`, so it can:

- label its own hypothesis `external_observation` (defeating "agent-origin reports never admit"),
- name a trusted source (defeating quarantine and class priors),
- act as another principal (defeating the authority check, S-07),
- confirm its own claims (defeating confirmation, S-01).

The admission model is only as strong as the weakest place identity enters. This document makes identity enter **only** from the host.

## 2. Trust zones

| Zone | Who | Trusted to… |
|---|---|---|
| **T0 Host** | The code that embeds palimem (agent runtime, MCP server process) | assign principals, source classes, origin groups, authority rules, policy, budgets |
| **T1 Connector** | Adapters for user messages, tools, documents, feeds, registered by the host | label *their own events* (`source_id`, `origin_group`, `raw`) once registered; cannot change their class |
| **T2 LLM** | The model that calls tools | nothing about identity or provenance; it can *request* memory operations |
| **T3 Content** | Any text inside an event, tool result, document, or LLM output | nothing; it is data. Instructions inside it have no authority |

A compromised T0 is out of scope. Indirect prompt injection that makes a **registered connector** deliver false content (a poisoned web page) is *not* an identity failure: the evidence is legitimately `external_observation` from that connector, and it is handled by source classes, quarantine and confirmation (T-H2), not by this layer.

## 3. The two tiers

### 3.1 Host API (T0 only)

```python
class HostMemory:                                   # full power; never exposed to the LLM
    def append(self, report: Report, *, idempotency_key: str) -> AppendResult
    def ingest_event(self, event: ConnectorEvent, *, extractor: Extractor | None = None,
                     idempotency_key: str | None = None) -> list[ReportId]
    def register_connector(self, c: ConnectorSpec) -> None          # source_id, source_class, origin_group
    def set_authority(self, rules: list[AuthorityRule]) -> AdmissionVersion      # flat typed grants, see §9b
    def set_source_class(self, source_id: str, cls: SourceClass, *, reason: str) -> AdmissionVersion
    def bind_session(self, ctx: SessionContext) -> "AgentTools"      # the only way to get the tool tier
    # read side (also available to the host without session scoping)
    def query(self, q: Query) -> Answer
    def explain(self, key: Key, *, valid_at=None, mode="one", depth=None) -> Explanation
    def find(self, text: str) -> list[KeyRef]
    def subscribe(self, plan_id: str, keys: list[Key]) -> None
```

```python
@dataclass(frozen=True)
class ConnectorSpec:   source_id: str; source_class: SourceClass; origin_group: str
                       kind: Literal["user_message","tool_result","document","feed","system"]
                       auto_ingest: bool = False

@dataclass(frozen=True)
class ConnectorEvent:  event_id: str; connector_id: str; received_at: datetime
                       principal: str | None; raw: str; metadata: Mapping[str, str]
                       # source, class and origin_group are NOT fields: they come from the registered connector.

@dataclass(frozen=True)
class SessionContext:  session_id: str
                       agent_principal: str                           # stable across sessions, e.g. "agent:planner"
                       end_user_principal: str | None                 # attribution of user events only; grants no tool authority
                       allowed_attrs: tuple[str, ...] | None          # key scope for reads and writes in this session
                       policy_version: str; max_explanation_budget: int
                       rate_limits: RateLimits
```

### 3.2 Agent tool API (T2-facing; built by `HostMemory.bind_session`)

```python
class AgentTools:
    def remember(self, text: str, *, kind: Literal["note","hypothesis"] = "note",
                 about: str | None = None, cite_event: str | None = None) -> RememberResult
    def recall(self, query: str | RecallKey, *, valid_at: str | None = None,
               belief_as_of: str | None = None, max_alternatives: int = 5) -> AgentAnswer
    def retract(self, report_id: str, *, reason: str | None = None) -> AuthorityResult   # withdraw
    def correct(self, report_id: str, text: str, *, reason: str | None = None) -> AuthorityResult
    def dispute(self, report_id: str, *, reason: str | None = None) -> AuthorityResult
    def explain(self, query: str | RecallKey, *, valid_at: str | None = None,
                mode: Literal["one","all"] = "one", depth: int | None = None) -> AgentExplanation
```

```python
RememberResult = {"report_ids": [str], "origin": "agent_statement" | "agent_hypothesis" | "external_observation",
                  "admitted": False | True, "notices": [Notice]}
AuthorityResult = {"effect": "applied" | "logged_only", "notices": [Notice]}   # identical shape for denied, out-of-scope and unknown ids (R7)
AgentAnswer    = Answer (output contract v2) + {"notices": [Notice]}          # kernel_status and decision both present
Notice         = {"code": "field_ignored" | "origin_forced" | "event_unknown" | "scope_denied" | ..., "field": str|None}
```

Tool JSON Schemas generated from these signatures set `additionalProperties: false`. A client that sends extra properties is handled by §5 rule R2.

## 4. Who sets what

| Report field | Host `append` | `ingest_event` | Agent `remember` | Notes |
|---|---|---|---|---|
| `id`, `recorded_at`, LSN | log | log | log | never caller-supplied, anywhere |
| `prev_hash`, `entry_hash`, salt | log row (storage layer) | log row | log row | **not Report fields** (section 9a); never caller-supplied |
| `source.id`, `source.class` | caller (T0) | **connector registry** | **forced**: `agent:<principal>` source, class `agent` | the LLM never names a source |
| `origin` | caller (T0) | **connector kind**: `external_observation` for user/tool/doc/feed events | **forced** to `agent_statement` (`note`) or `agent_hypothesis` | the only exception is §5 R5 (`cite_event`) |
| `origin_group` | caller | connector registry | forced: the agent principal id itself, e.g. `agent:planner` (stable across sessions) | the same agent repeating itself, in any session, counts once |
| `actor` | caller | `event.principal` or the connector's principal | **forced** to `SessionContext.agent_principal` | identity is bound by the host, never an argument |
| `cue` | any | extractor *request*, authority-checked | **forced** to `assert` for `remember`; `withdraw`/`correct`/`dispute` only via `retract`/`correct`/`dispute`, each authority-checked (R6) | an agent cannot emit `change` (there is no `confirm` cue, S-01) |
| `target` | any | extractor-supplied, validated | only for `retract` | |
| `key`, `proposition` | caller | extractor output, schema-validated | extractor output from the LLM's text, schema-validated | LLM may give an `about` hint; never authoritative |
| `valid_from/to`, `precision` | caller | extractor output | extractor output | |
| `observed_at` | caller | event metadata | not settable | audit only |
| `raw_ref` | caller | event | log (the LLM's own text) | |
| `extractor` stamp | caller | host | host | |
| `profile`, `policy`, `semantic cfg` on reads | caller | n/a | **not settable** | session-bound |
| `explanation_budget` on reads | caller | n/a | clamped to `max_explanation_budget` | |

## 5. Rules

**R1: Identity is host-bound.** `source`, `source_class`, `origin`, `origin_group`, `actor`, `recorded_at`, `id`, `prev_hash`, `entry_hash`, `admission`, `profile`, `policy` never come from the LLM or from text. Connector metadata is trusted only for a *registered* connector.

**R2: Strip, downgrade, audit; never silently accept.** If a tool call carries any R1 field (a client bug, an LLM emitting extra JSON, an injection), the call is **not** rejected wholesale. The field is removed, the report is written with forced values (§4), the response carries a `Notice` (`field_ignored`), and an audit record `trust_downgrade {session, tool, fields}` is appended. Rationale: hard failures create retry loops in agents; silent acceptance is the vulnerability.

**R3: `remember` is never admissible by itself.** It writes `agent_statement` or `agent_hypothesis` reports. They are stored, queryable by origin, and never admissible or confirming (design: Admission model, "Agent-origin reports").

**R4: Agent cues are bounded.** An LLM can only request operations through the tools: assert (`remember`), withdraw (`retract`), correct (`correct`) and dispute (`dispute`), each subject to R6. Text such as "correcting r1" or "mark that source trusted" inside `remember` does not become a `correct`/`dispute` cue: it is stored as an agent-origin assert and, at most, an `allege` pointing at the target if an extractor recognises a correction intent (an `allege` has no effect on admissibility).

**R5: The LLM may *point at* evidence, never *be* evidence.** `remember(cite_event=<event_id>)` asks the host to ingest an event it already holds in the session's event ledger. The host looks the event up; the resulting report's source, class, origin and `raw_ref` come from the **event and its connector**, and the proposition is extracted from the event's `raw`, not from the LLM's `text`. Unknown or foreign event ids fail with `event_unknown` and write nothing (no report, no audit of content). When the event is a connector with `auto_ingest = true` the host has already ingested it; `cite_event` is then idempotent (same idempotency key derived from `event_id`).

**R6: Agent authority is bound to actor identity and origin class, not to the session.** (Author decision, 2026-10-04.) The host binds the actor (`agent_principal`, stable across sessions); the LLM supplies only a `report_id` (and text for `correct`).

| Operation | Target | Result |
|---|---|---|
| `retract` (withdraw) or `correct` | report whose `actor` equals the bound agent principal **and** whose origin is agent-class (`agent_hypothesis`, `agent_statement`, `plan`) | **applied**; may be from any earlier session |
| `retract` or `correct` | any `external_observation`, or any report authored by another actor | **never applied**: recorded as `allege`; result `logged_only` |
| `dispute` | any report, **only if** the host has granted that agent principal a `dispute` authority rule covering the key scope (S-07) | **applied** as a `dispute` |
| `dispute` | any report, without such a grant | recorded as `allege`; result `logged_only` |

Invariants: (a) an agent principal can **never** be granted `withdraw` or `correct` on an `external_observation`; authority-rule validation rejects such a grant (S-07). (b) An agent's dispute does **not** quarantine the disputed source; whether it ever may is deferred, default **no**. (c) Retraction by a human or system principal (for instance an end user correcting a fact they stated) goes through the **host API** with that principal as actor, never through the agent tool. (d) Whether `simulation` and `counterfactual` origins count as agent-class for self-withdrawal is open (they are never admissible, so the risk is low); default: yes, same as the three named.

**R7: No existence oracle.** `retract`, `correct` or `dispute` of an unknown id, an id outside `allowed_attrs`, or an id the actor lacks authority over returns the *same* result shape (`logged_only`, notice `scope_denied`), with no distinguishing timing or detail, so an LLM cannot probe other scopes' reports. A denied target that resolves is still recorded as `allege`; an unknown target writes only the audit record.

**R8: Text is data.** Instructions inside `text`, events or documents never change behaviour. The extractor receives the text as an untrusted input and its output is schema-validated (§7).

**R9: Read-side tampering is ignored.** `recall` / `explain` cannot choose the profile, the policy, the semantic configuration or an unbounded budget. Supplied values are stripped (R2); `explanation_budget` and `depth` are clamped to host maxima.

**R10: Rate and size limits are host-set.** Per-session limits on writes, text length, `report_ids` returned and `max_alternatives`; exceeding them returns `rate_limited` with no side effects.

**R11: Every downgrade and denial is auditable.** The audit log is append-only, separate from the evidence log, and records `{lsn, session, tool, event, fields}`. It never stores the content of a rejected text beyond a hash.

**R12: Deterministic mapping.** Given the same `SessionContext`, connector registry and call, `AgentTools` produces the same host-level calls. This is what the fixtures test.

## 6. Mapping tools to the host API

| Tool | Host-level effect |
|---|---|
| `remember(text, kind, about, cite_event=None)` | `ingest_event(ConnectorEvent{connector=agent:<session>, principal=agent_principal, raw=text}, extractor)` with origin forced to `agent_statement`/`agent_hypothesis` |
| `remember(..., cite_event=e)` | `ingest_event(ledger[e], extractor)` with the event's own connector provenance (R5) |
| `recall(query, …)` | `query(Query{key|find(query), valid_at, belief_as_of, profile=session.profile, explanation_budget=min(…)})`, projected to `AgentAnswer` |
| `retract(report_id, reason)` | `append(Report{cue: withdraw, target: report_id, actor: <bound agent_principal>})` through the authority check (R6); failure ⇒ `allege` |
| `correct(report_id, text)` | extract `text` as an agent-origin assert, then `append(Report{cue: correct, target, actor})` through the authority check; failure ⇒ the assert is kept as `agent_statement`, the correction is an `allege` |
| `dispute(report_id, reason)` | `append(Report{cue: dispute, target, actor})` through the authority check; failure ⇒ `allege` |
| `explain(query, mode, depth)` | `explain(key, mode, depth=min(depth, host_max))` |

## 7. Extractor boundary

An extractor sees `raw` text and the schema; it returns candidate `{key, proposition, valid_*, precision, cue_request, target_request}` records. The host:

1. discards any `source`, `origin`, `actor`, `class`, `id`, `recorded_at` fields it returns (and audits),
2. validates `key.attr` ∈ schema and `proposition.form` against the attribute's class,
3. treats `cue_request ∈ {correct, withdraw, dispute}` as a request subject to R4/R6 (agent-origin events ⇒ `allege`),
4. stamps `extractor = {model, version, prompt_hash}`.

## 8. What this does not cover

- A malicious or compromised host process.
- A registered connector delivering false content (poisoning): see T-H2.
- Model-side exfiltration of what `recall` returns to the LLM (privacy, T-H4).
- Multi-agent identity federation (S-07 open question 2).

## 9a. Log integrity is a storage-layer property

(Author decision, 2026-10-04.) A hash chain over the evidence log (`prev_hash`, `entry_hash`, salted so contents cannot be confirmed by guessing) lives on the **log row**, not on `Report`. The Report contract and the G0 fixtures do not change; a backend without the chain is still contract-conformant. `verify_log(from, to)` is an optional-capability operation of the backend interface and the SQLite default implements it. Callers, including the host's `append`, cannot supply chain fields (R1); see fixture tb-20. Tombstones preserve the chain and carry the original entry hash (S-13). Versioning treatment: `docs/VERSIONING.md` section 6.

## 9. Conformance fixtures

Format and matcher semantics: `tests/fixtures/trust_boundary/README.md`. Fixtures (`tests/fixtures/trust_boundary/tb-*.json`):

| ID | Rule(s) | One-line behaviour |
|---|---|---|
| tb-01 | R1, R2, R3 | LLM-supplied `origin`/`source` stripped; report is `agent_statement`; recall stays `unknown` |
| tb-02 | R1, R2 | LLM-supplied `source_class: trusted` is stripped |
| tb-03 | R1, R6 | `retract(actor="connector:registry")` runs as the bound agent principal; target is an `external_observation` ⇒ `logged_only`; evidence intact |
| tb-04 | R6 | Agent withdraws its own `agent_statement` from an earlier session ⇒ applied; the by-origin listing changes, justified belief does not (agent-origin reports never entered it) |
| tb-05 | R5 | `cite_event` takes provenance and proposition from the event, not from the LLM text |
| tb-06 | R5, R7 | Unknown `cite_event` writes nothing |
| tb-07 | R8 | Injection text in a tool-result event cannot withdraw a report or change a class |
| tb-08 | R3 | Repeated `hypothesis` writes never confirm a quarantined report |
| tb-09 | R1, §7 | Extractor-returned identity fields are discarded |
| tb-10 | R1 | LLM-supplied `recorded_at` and `id` ignored; LSN order wins |
| tb-11 | R4 | "Correcting r1…" in `remember` is an agent-origin assert, no effect |
| tb-12 | R9 | `recall(profile=..., policy=...)` stripped; session policy used |
| tb-13 | R9, R10 | Oversized `explanation_budget` clamped |
| tb-14 | R7 | Retract of an out-of-scope id and of a missing id are indistinguishable |
| tb-15 | R6 | Agent withdraws its own `agent_hypothesis` and `plan` from a week earlier ⇒ applied; a different agent principal ⇒ `logged_only` |
| tb-16 | R6 | Agent withdraws an `external_observation` ⇒ `logged_only`; recorded as `allege`; belief unchanged |
| tb-17 | R6 | Agent disputes an `external_observation` without a granted rule ⇒ `allege`; no effect |
| tb-18 | R6 | Agent disputes with a granted rule ⇒ recorded as `dispute`; belief becomes `unresolved` (assumes S-02's open point on dispute semantics); source not quarantined |
| tb-19 | R6 | Authority-rule validation rejects granting an agent principal `withdraw` on `external_observation` |
| tb-20 | R1, 9a | Caller-supplied `prev_hash`/`entry_hash` are ignored; the chain is computed by the log |

## 9b. Authority as implemented (T-A3 types, T-D2 evaluation)

`AuthorityRule` is the flat typed grant of `palimem.types.authority`: `who` (a typed principal id, an origin group, `any`, or one of the `target_*` kinds), `may` (correct, withdraw, dispute), `on` (a key scope of attribute and entity globs), `targets` (report, source, any) and `over_origins`. Principal ids are `<kind>:<name>` with kind `agent | user | connector | system`, fixed by the host. `Attr.authority` rules are matched first, then the global table of the admission config; the first match wins; the grant table is a versioned admission input, so `set_authority` returns a new admission version.

R6 is enforced in two places because wildcard `who` kinds cannot be checked against principal kinds at load time: explicit grants to an agent over external evidence are refused when the rule is built, and `palimem.admission.Authorizer` refuses at evaluation time any agent withdraw or correct whose target origin is not agent-class, any agent source- or table-wide extent, and any agent `dispute` not granted by a rule naming that principal. A failed withdraw or dispute is recorded as an `allege` (`excluded / authority_failed`). A failed `correct` is *not* an allege (S-02): its proposition remains an ordinary admissible assert and simply does not withdraw its target. The `retract`, `correct` and `dispute` tools above map to these cues unchanged.
