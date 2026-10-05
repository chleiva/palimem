# Architecture

A map of what exists in this repository today, derived from the code (module docstrings and public entry points) and the
documents that hold the detail. Where a part is missing or partial it says so; the full list is in
[`LIMITATIONS.md`](LIMITATIONS.md).

palimem keeps **propositional beliefs justified by evidence**. Four stages, each versioned separately and each with one job:

```
evidence log  ──►  admission  ──►  kernel  ──►  policy
 (what was said)   (what may be     (what the     (commit, abstain
                    heard)           admitted      or ask)
                                     evidence
                                     justifies)
```

Reliability enters at admission (whether a source may be heard) and at the policy (how much to trust what was heard),
never inside the kernel. The kernel's answer is invariant under the policy, not under admission.

## 1. Components and data flow

```
 WRITE PATH                                                                              READ PATH
 ──────────                                                                              ─────────
 connector / typed claim / text                                           Memory.ask / Memory.query / agent `recall`
        │                                                                              │
        ▼                                                                              ▼
 extract/        Extractor proposes CONTENT only (text -> claims); build_reports   store/  Backend.read_belief
 (optional)      binds source, origin, actor, origin_group, targets from the host        (generation barrier: a stale key or
        │        context, never from the text or the model output                         dependent returns ResourceLimited,
        ▼                                                                                 never the old version as current)
 Report ─────────► store/   Backend.append  ── ONE transaction ──────────────┐            │
                    1  log row: lsn, recorded_at, salted hash chain           │            ▼
                    2  admission/   admissible | quarantined | excluded       │     policy/   decide(): commit | abstain | ask
                       (origin, authority, derived confirmation,              │     (presets: justified, recency, lww;
                        origin groups count once; incremental, with the       │      kernel_status and decision stay separate)
                        whole-log evaluation kept as the audit oracle)        │            │
                    3  kernel/  justify_key for touched base keys;            │            ▼
                       derived keys rebuilt from STORED base beliefs,         │     Answer = Resolved | ResourceLimited
                       pinning the base versions they consumed                │     (kernel_status, decision, assertion,
                    4  belief versions with per-candidate Support, barrier    │      alternatives, provenance, inquiry)
                       marks, completion jobs, notification outbox ───────────┘
```

| Stage | Code | What it decides | Document |
|---|---|---|---|
| Extract (optional) | `src/palimem/extract/` | text to typed claims; never identity | [`EXTRACTION.md`](EXTRACTION.md) |
| Log and store | `src/palimem/store/` | append-only evidence log, salted hash chain, one transaction per append, versions, barrier, outbox, erasure, export | [`STORAGE.md`](STORAGE.md) |
| Admission | `src/palimem/admission/` | which logged reports enter the evidence set, and with what authority | [`API_TRUST_BOUNDARY.md`](API_TRUST_BOUNDARY.md), [`PIPELINE.md`](PIPELINE.md) |
| Kernel | `src/palimem/kernel/` | what the admitted evidence justifies: status, candidates, supports, per valid-time segment | [`KERNEL.md`](KERNEL.md), [`FAST_KERNEL.md`](FAST_KERNEL.md) |
| Policy | `src/palimem/policy/` | commit, abstain or ask, as a pure function of a versioned policy object | [`PIPELINE.md`](PIPELINE.md) |
| Pipeline | `src/palimem/engine/`, `src/palimem/memory.py` | wires the stages: `Pipeline`, `StoreAdmitter`, `KernelReviser`, and the host-level `Memory` core | [`PIPELINE.md`](PIPELINE.md) |
| Contract | `src/palimem/types/`, `schemas/` | frozen dataclasses, canonical JSON, generated JSON Schemas | [`TYPES.md`](TYPES.md) |
| Compat profile | `src/palimem/compat/` | the `revise-stream-v1` adapter that projects an `Answer` onto the study's contract | [`PIPELINE.md`](PIPELINE.md) |

Facts worth knowing about the middle of the diagram:

- **The kernel is the enumeration kernel.** It enumerates admissible interpretations per key under a budget of **12** reports
  per key (the default since the cross-check in [`BUDGET_CROSSCHECK.md`](BUDGET_CROSSCHECK.md); a key over the budget answers
  `ResourceLimited(environment_budget)`, never another answer). A faster candidate kernel (`kernel/fast.py`) exists and is off
  by default.
- **Admission is incremental.** A plain append does no work that grows with the log; `Admitter.evaluate` (the whole-log
  evaluation) is the audit oracle and is selectable per pipeline (`incremental`, `whole-log`, `crosscheck`).
- **Derived beliefs pin base versions.** Withdrawing or correcting a report re-justifies the base key and rebuilds every
  derived key that read it, so the repair reaches conclusions two or more steps downstream.
- **Two time axes.** `valid_at` selects a valid-time segment; `belief_as_of` accepts a log sequence number (canonical) or a
  timestamp that the log maps to the last sequence number at or before it (decision S-05).

## 2. The three API tiers

| Tier | Who may call it | Surface | Identity |
|---|---|---|---|
| **Host API** | trusted application code | `palimem.Memory` (the facade: `observe`, `ask`, `withdraw`, `explain`, `find`, `subscribe`, `delete`, `verify`, `declare`, `agent_session`) over `palimem.memory.Memory` (the core: `append`, `withdraw`, `delete`, `query`, `explain`, audit paths) | the caller names source, origin and actor, so it must never be handed to an LLM |
| **Agent tool API** | an LLM, through a session the host created | `palimem.agent`: `Host.bind_session` returns `AgentTools` with `remember`, `recall`, `retract`, `explain` (and `dispute` only for a principal the host granted it) | the host binds source, origin, actor and origin group; any such field the LLM supplies is stripped, noted in the result and audited; `remember` stores what the agent says as `agent_statement` or `agent_hypothesis`, which is never admitted as evidence and never confirms anyone; `retract` works only on the agent's own agent-class reports |
| **Transports** | processes that serve or inspect a store | `palimem mcp` (stdio JSON-RPC, serves the agent tool API only, `--read-only` removes the write tools, no network listener, no destructive tools); the `palimem` CLI (`inspect`, `explain`, `diff`, `export`, `import`, `verify`, `mcp`) | the MCP server's principal and session are fixed by whoever launched it, never by the client's arguments |

How an LLM should read an `Answer` is in [`AGENT_GUIDE.md`](AGENT_GUIDE.md); the rules of the boundary are in
[`API_TRUST_BOUNDARY.md`](API_TRUST_BOUNDARY.md).

## 3. Trust boundaries

Numbering follows [`THREAT_MODEL.md`](THREAT_MODEL.md) §3, which holds the threats and the status of each mitigation.

| Boundary | Where it is enforced in the code | Verified by |
|---|---|---|
| B1 LLM to agent tools | `palimem.agent`: identity is bound by `Host`/`AgentTools`, never by tool arguments | `tests/trust_boundary/` (20 fixtures; 18 pass, 2 recorded failures), `tests/test_agent_tools.py` |
| B2, B3 text to extractor to log | `palimem.extract`: no identity fields in the output grammar, unexpected keys rejected; `build_reports` binds identity from the host context; the store checks schema, proposition form, targets and authority at `append` | `tests/test_extract.py`, the extractor-quality set (injection items), [`eval/EXTRACTION_RESULTS.md`](eval/EXTRACTION_RESULTS.md) |
| B4 other processes to the MCP server | stdio only, one client, agent tool API only, read and write tools registered separately | `tests/test_mcp.py` (subprocess and in-process fake; not real MCP clients) |
| B5 process to storage | salted hash chain over the evidence and admission logs, `verify_log`, `verify_beliefs` | `tests/store/test_verify.py`, fixtures SEC-25, SEC-26, SEC-30 |
| B6 store to subscribers | outbox written inside the append transaction, at-least-once delivery with stable event ids | `tests/store/test_outbox.py` |
| B7 process to hosted LLM provider | the extractor is optional; every model call goes through `palimem.costs` (pre-run estimate, hard cap, ledger) and needs `PALIMEM_ALLOW_PAID_CALLS=1` | `tests/test_costs.py`, `tests/test_extract.py` |

## 4. Where each gate lives

Nothing here ships unless the differential gates hold; they are the merge gate for every change.

| Gate or check | What it asserts | Where | When it runs |
|---|---|---|---|
| Differential harness | the incremental store equals symbolic replay and the frozen gold on every Setting 1 query; frozen files match the pinned manifest | `harness/differential.py`, `harness/frozen.py`; [`HARNESS.md`](HARNESS.md) | CI `harness` job (every 5th stream on push and PR, all streams nightly) |
| Kernel differential | the kernel equals the frozen gold, with strict provenance through the compat projection | `harness/kernel_diff.py` | CI `harness` job |
| Pipeline differential | the whole pipeline, through `Memory` and the v1 adapter, equals the gold on both backends; strict provenance | `harness/pipeline_diff.py` | CI `harness` job (every 10th stream on push, all streams nightly; the strict provenance step runs every 25th stream on push and every 5th nightly) |
| Incremental-admission crosscheck | incremental admission equals the whole-log evaluation after every append | `PALIMEM_ADMISSION=crosscheck`, `tests/test_admission_incremental.py` | tests; opt-in mode |
| Conformance suite | 90 implementation-neutral fixtures (22 independent acceptance rows, decision and security fixtures); a ratchet stops a passing fixture from regressing | `tests/conformance/`, `tests/conformance/memory_status.json`; [`CONFORMANCE.md`](CONFORMANCE.md) | CI (`test` and `harness` jobs) |
| Trust-boundary fixtures | the agent tool API cannot set identity, retract external evidence or dispute without a grant | `tests/trust_boundary/`, `tests/trust_boundary/status.json` | CI `test` job |
| Static contract checks | `ruff`, `mypy --strict`, committed JSON Schemas equal the types | `python -m palimem.schemas --check` | CI `test` job (Python 3.11 to 3.13) |
| Secret scan | no credential patterns in tracked files | `scripts/check_secrets.sh` | CI (`test` job) |
| Budget cross-check | the enumeration kernel equals the brute-force oracle at 8 to 12 reports per key | `harness/budget_crosscheck.py`; [`BUDGET_CROSSCHECK.md`](BUDGET_CROSSCHECK.md) | manual and on change |
| Fast-kernel candidate | the fast kernel equals the enumeration kernel and the gold | `harness/fast_kernel_diff.py`, `.github/workflows/fast-kernel.yml` | nightly, on demand, and on pull requests that touch it |
| Extractor-quality gate (G-X) | per-model thresholds declared before any run, checked on the frozen test split once | `bench/extract/gate.json`, `bench/extract/extract_gate_check.py`; [`eval/EXTRACTION_GATE.md`](eval/EXTRACTION_GATE.md) | manual (it spends money, capped by the ledger) |
| Agent-level benchmark | scenario gold versus system actions, harmful-action and unnecessary-ask rates | `bench/agent/`; [`eval/AGENT_BENCHMARK.md`](eval/AGENT_BENCHMARK.md) | manual |
| Performance targets | declared absolute targets, measured on a laptop | `bench/perf/`, `.github/workflows/perf.yml`; [`PERFORMANCE.md`](PERFORMANCE.md) | nightly and on demand; never blocks a pull request, asserts no timing thresholds |
| Security workflow | `pip-audit` and CodeQL | `.github/workflows/security.yml` | on push and PR, weekly |
| Release | tests, tag equals the package version, build, publish through trusted publishing | `.github/workflows/release.yml`; [`RELEASING.md`](RELEASING.md) | a published GitHub release only; manual runs publish to TestPyPI only |

## 5. Repository layout

| Path | Contents |
|---|---|
| `src/palimem/` | the package: `types`, `store`, `admission`, `kernel`, `policy`, `engine`, `compat`, `extract`, `agent`, `mcp`, `facade`, `cli`, `costs` |
| `schemas/` | generated JSON Schemas and examples (not shipped in the wheel; the types are the source of truth) |
| `harness/` | differential and conformance harnesses against the study's frozen data |
| `tests/` | unit tests, the conformance suite (`tests/conformance/`), trust-boundary fixtures, performance smoke tests |
| `bench/` | `agent` (RETRACT-ACT), `extract` (extractor-quality set and gate), `perf`, `budget`, `kernel` results |
| `research/` | the R4.1 exponential-core spike |
| `docs/` | this documentation; start at [`README.md`](README.md) |
