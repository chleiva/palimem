# Agent guide: using palimem from an LLM agent (T-F5)

palimem is a **justified memory**: it stores reports (who said what, when, from which origin), keeps what the admitted
evidence warrants, and tells you when the evidence does not decide. This guide is for the people who embed it in an
agent and for the model that calls it. Pre-alpha (0.0.x): contracts may change; see `docs/VERSIONING.md`.

## 1. Two tiers, and why the LLM only gets one

| Tier | Who | What it can do | Object |
|---|---|---|---|
| **Host** | your code (trusted) | name sources, origins, actors; register connectors; set authority; ingest events; delete; export | `palimem.Memory`, `palimem.agent.Host` |
| **Agent tools** | the LLM | `remember`, `recall`, `retract`, `explain` (and `dispute` when you granted it) | `Memory.agent_session(...)` -> `AgentTools` |

The memory is only as trustworthy as the place identity enters. So an agent never names a source, an origin or an actor:
the host binds them when it opens the session. An LLM that supplies `origin="external_observation"` or
`source="registry"` (a bug, an injection) gets those fields **stripped, a `field_ignored` notice, and an audit row**: the
call still works, with forced values. See `docs/API_TRUST_BOUNDARY.md`.

```python
from palimem import Memory

m = Memory("agent.db")                       # host code
tools = m.agent_session("agent:support")     # the only object the LLM's tool layer receives
```

## 2. The tools

| Tool | Effect | Authority |
|---|---|---|
| `remember` | Records what **you** say as `agent_statement` (or `agent_hypothesis` with `kind="hypothesis"`). It is stored and queryable but **never admitted as evidence and never corroborates** anyone. Give `text` (needs an extractor on the host) or `entity` + `attr` + `value`. With `cite_event=<id>` the host ingests an event it already holds, and the evidence takes its source, class and origin **from the event**, not from your words. | none needed |
| `recall` | What the memory currently justifies about a key. Accepts an exact `{entity, attr}` or a lookup phrase; optional `valid_at` (a moment in the world) and `belief_as_of` (an earlier log position or timestamp). | read |
| `retract` | Withdraws a report **you wrote yourself** (any earlier session). Anything else is only logged: the result says `logged_only`. | own agent-class reports only |
| `explain` | Which reports justify the answer, who said each, from which origin group, grouped by the candidate they support. | read |
| `dispute` | Disputes a report. Listed **only** when the host granted this agent principal a `dispute` rule; otherwise calling it is only logged. A dispute never silences or quarantines a source. | explicit host grant |

There is no tool to delete, merge, withdraw a source wholesale or change authority. Those are host operations.

## 3. Reading an answer

Every `recall` returns two fields that must be read **together**: `kernel_status` (what the evidence warrants) and
`decision` (what the policy does about it: `commit`, `abstain` or `ask`). A policy commitment on an unresolved key stays
visible as exactly that: `kernel_status` remains `unresolved`.

| kernel_status | Meaning | What to do |
|---|---|---|
| `established` | The admitted evidence settles it. | Use it, but check `single_origin` (below). |
| `established_empty` / `established_false` | Explicit evidence of "no members" / "not this value", or a declared closed world. | Treat as a real answer. |
| `unresolved` | Several candidates, all listed in `alternatives`. | **Do not pick one yourself.** If `decision` is `ask`, ask a source that can decide (see `inquiry`); if `abstain`, say it is unsettled. If `decision` is `commit`, the policy chose; it is a choice, not a fact. |
| `unknown` | No admissible evidence bears on this. | **Do not guess.** Say it is unknown or ask. Absence of evidence is never "no". |

**Single-origin marking.** `single_origin: true` means the answer rests on **one origin group**: one upstream that
may be copied by many reports (reports from the same origin group count once). It is uncorroborated: a trusted source
that is wrong looks exactly like one that is right. Only confirmation from a **second origin group** raises it, and a
withdrawal or dispute by the source repairs everything downstream. Say "according to X" rather than stating it as settled.
`single_origin: null` means it cannot be told (for example a derived key whose supports are not available).

**Attribution only.** If `attribution_only` is `true`, the memory holds only what other parties are *reported to believe*
("Dave believes the refund window is 30 days"), never the value itself. The content is **unknown**: `assertion` is empty,
`decision` is `ask`, and the reports are listed apart in `attributions` (holder, what they believe, origin groups, report
ids). Do not state the attributed value as a fact; ask a source that can confirm it, or say it is unconfirmed. An
attribution corroborated by many origin groups is still only an attribution.

`resource_limited` is not an answer: the memory has not finished inferring for the requested snapshot (a stale
dependency, an over-budget key, a dirty store). It carries **no value**. Never treat an older value as current; retry later.
An older snapshot may be attached and is explicitly labelled as older.

`not_reconstructable` is also not an answer: the belief you asked about *as of an earlier snapshot* was erased (a deletion
obligation), so what was believed then can no longer be rebuilt. It carries **no value and no content**. Do not guess the old
value. If `current_available` is true, ask again without `belief_as_of` to read the current belief.

### Notices

| code | Meaning |
|---|---|
| `field_ignored` | You sent a field the tool does not take (identity fields, a profile, a policy...). It was dropped and audited. |
| `scope_denied` | The target is outside this session's scope or you lack authority. Unknown ids look the same on purpose. |
| `clamped` | A value you asked for exceeded the host's limit and was reduced (`depth`, `max_alternatives`). |
| `event_unknown` | `cite_event` named an event the host does not hold for this session. Nothing was written. |
| `claim_rejected` | The extractor produced a claim that could not be bound legitimately (undeclared attribute, wrong form, ...). |

### What it looks like

```text
alice/employer: ESTABLISHED = 'Acme'.
  SINGLE ORIGIN: rests on one origin group (g_hr); uncorroborated. Corroboration from a second origin group is what raises it.
  decision=commit; policy=p-default.

alice/employer: UNRESOLVED between 'Acme', 'Globex'.
  Evidence does not decide. Ask a source that can, or tell the user it is unsettled.
  decision=ask; policy=p-default.

alice/employer: UNKNOWN (no admissible evidence). Do not guess; say it is unknown or ask.
  decision=abstain; policy=p-default.

store/refund_window_days: CONTENT UNKNOWN. Only what other parties are reported to believe is established:
  - dave is reported to believe '30' (2 origin groups).
  That does not establish the value itself. Do not state it as a fact; ask a source that can confirm it, or say it is unconfirmed.
  decision=ask; policy=p-default.
```

These strings are pinned by golden tests (`tests/test_agent_render.py`): wording is a contract with the prompt.

## 4. A system-prompt fragment

```text
You have a justified memory. Before relying on a fact about a person, account or order, call `recall`.
- If kernel_status is "established", you may state it; if single_origin is true, attribute it ("the HR record says...").
- If it is "unresolved", list the alternatives and do not pick one; if decision is "ask", ask who can settle it.
- If it is "unknown", or the result is resource_limited or not_reconstructable, say you do not know. Do not guess and do not use older values.
- If attribution_only is true, you only know what someone is reported to believe, not the fact: say so and ask a source that can confirm it.
- `remember` records what you say; it is not evidence. To record what an external source said, cite the event.
- You may `retract` only your own earlier statements.
```

## 5. MCP

`palimem mcp agent.db --principal agent:support` serves the agent tools to the one client that launched it over
stdio (JSON-RPC 2.0, one message per line; no network listener, standard library only). The principal and the session are
fixed by the command line, never by the client. `--read-only` exposes `recall` and `explain` only and refuses the rest;
`--attrs employer,hq_city` bounds the attributes the session may touch (with no schema declared, the schema otherwise
grows with whatever the agent remembers: bound it).

A client configuration looks like:

```json
{"mcpServers": {"palimem": {"command": "palimem", "args": ["mcp", "agent.db", "--principal", "agent:support", "--attrs", "employer,hq_city"]}}}
```

It implements `initialize`, `ping`, `tools/list` and `tools/call` (text content plus `structuredContent`; `isError` for
tool failures; JSON-RPC errors for protocol failures and for tools that are not listed). It is a minimal implementation,
tested against a subprocess and an in-process fake transport; it has not been exercised against specific MCP clients.

## 6. The command line

| Command | What it does |
|---|---|
| `palimem inspect DB` | head, hash chain status, schema, and the current belief of every key |
| `palimem explain DB ENTITY ATTR [--as-of N]` | the answer and what justifies it |
| `palimem diff DB ENTITY ATTR --a N --b M` | how the belief changed between two log positions |
| `palimem verify DB` | check the hash chain and recompute stored beliefs |
| `palimem export DB -o dump.jsonl` / `palimem import NEWDB dump.jsonl` | portable evidence log (it carries salts: treat it like the database) |
| `palimem mcp DB --principal P` | serve the agent tools over MCP |

Read commands never create a database.

## 7. Known limits (0.0.x)

- **Plain text needs an extractor.** `observe(text)` and `remember(text)` refuse rather than guess a key. Use typed
  claims (`{"entity", "attr", "value"}`) or configure an extractor (`palimem.extract`).
- **Zero-config is conservative.** An attribute seen for the first time is declared as a multi-valued, open-world stable
  set. For single-valued or changeable attributes declare a schema. The design's "no inertia" cannot be expressed: the
  kernel refuses `inertia=False` (S-08 leaves it undefined).
- **`explain` and `single_origin` rest on the supports the pipeline computes.** For a derived key whose supports are not
  available the explanation is empty and `single_origin` is `null` (unknown), never a guess.
- **An authorised `dispute` is applied and logged, but the kernel does not yet weigh it** (S-02 open point).
- **`find` is lexical.** Entity resolution proper is a separate task.
- **The default policy asks** on unresolved keys (it never picks); presets `recency` and `lww` pick by recency.
