# Performance: declared targets and measurements (T-E5)

Status: **targets declared 2026-10-05, before the first benchmark run** (the commit that adds this section carries no
benchmark code and no measurements; the measured section is appended by a later commit). Gate: G2 measures against
these numbers. A relative speed-up over replay is not an interactive-performance claim (design, "Resource contract").

## 1. Why absolute targets, and where the numbers come from

The study measured a *relative* result: the incremental store answers 6 to 12 times faster than replaying the log, the
total-CPU crossover sits at about r = 1.1 queries per observation, and write cost grows as 2^n in reports per key (hence
the per-key budget, default **7**, S-06). None of that says whether palimem is fast enough for an agent. The targets below
come from the use case, not from the implementation:

* An agent tool call that reads memory sits inside a loop whose model call costs hundreds of milliseconds to seconds. A read
  that costs a few milliseconds is invisible; one that approaches 100 ms is a perceptible stall and gets multiplied by every
  retrieval in a turn.
* A "remember this" call should be visible to the next read of the same turn. The human-perception threshold for an
  interactive response is about 100 ms.
* The first deployment is one agent, one store, one process on a laptop or a small VM (design, "Operating limits of v1").
  The store must leave most of a 8 to 16 GB machine free, and must come back quickly after a crash.

The numbers are judgement calls justified by that use case. They are **not** fitted to measurements. The author of this
section had read the pipeline code before declaring them; the declaration is nevertheless fixed here before any run, and a
miss is reported as a miss.

## 2. Reference configuration

All targets apply to this configuration unless a row says otherwise.

| Item | Value |
|---|---|
| API | `palimem.memory.Memory` public API only (`append(..., complete=True)`, `query`), as in `docs/PIPELINE.md` |
| Backend | `SQLiteBackend` on a file on local SSD (WAL), one process, one writer |
| Profile | product default `open-world`, policy preset `justified` |
| Schema | one derived attribute through one rule (derivation depth 1, `work_city <- employer, hq_city`), plus single-valued, multi-valued and stable attributes |
| Per-key history | at most the environment budget (7 reports per key) unless the workload says otherwise |
| Store size | **10^5 reports** in the log (the "reference size"); three scales are run (see §5) |
| Machine | a current laptop (8 cores, 16 GB); the report records the exact machine |

## 3. Declared targets

| ID | Quantity | Target at the reference size | Why this number |
|---|---|---|---|
| **T1** | Query latency, `Memory.query` serving read at the head (stored-belief path, warm): p50 / p99 | **p50 ≤ 5 ms, p99 ≤ 25 ms** | A read must be small next to a model call; 25 ms keeps ten reads in a turn under a quarter second. Historical (`belief_as_of`) reads are held to the same numbers |
| **T2** | Append-to-visible latency: time for `Memory.append(report, complete=True)` to return with the revised belief readable (barrier clear): p50 / p99 | **p50 ≤ 25 ms, p99 ≤ 100 ms** (a sustained single-writer rate of at least 40 appends/s) | The interactive threshold; the report must be readable by the next step of the same turn |
| **T3** | Resident memory: marginal RSS per report, measured as the slope between the two largest scales, and absolute RSS at the reference size | **≤ 1 KiB per report (≤ 1 GiB per million reports, a labelled linear extrapolation), and RSS ≤ 300 MB at 10^5 reports** | A million-report store must fit comfortably beside an agent runtime on a 16 GB machine |
| **T4** | Recovery after interruption: wall time from process start (SIGKILL during an append load, then reopen with `SQLiteBackend` + `Memory` + `recover()`) to the first correct query answer; plus integrity | **≤ 2 s at 10^5 reports; zero committed appends lost, zero partial revisions, `verify_log` ok** | A crashed agent restarts and answers at once. `verify_log` duration is *reported*, not gated (expected to be linear in the log) |
| **T5** | Store-versus-replay crossover r\*: the number of queries per observation at which the total work (appends plus queries) of the store equals that of recomputing each answered key's belief from the log (replay), on workload W1, at 10^4 reports | **r\* ≤ 2.0** (the study measured about 1.1 for its implementation; this is a reported comparison, the gated claim is that the store wins at r ≥ 2 on a read-heavy workload) | The design argues the store pays for itself only on read-heavy or long-history workloads; the target says how read-heavy |
| **T6** | Flatness: p99 append-to-visible latency at the largest measured scale divided by the same at the smallest scale | **≤ 4** | The resource contract bounds revision time per append; a latency that grows with the log is a violation however small the absolute number |
| **T7** | On-disk size per report (database file including log, admission rows, belief versions, chain, indexes) | **≤ 5 KiB per report (≤ 5 GB per million)** | A million reports must fit on a laptop disk with room to spare |

T1 and T2 must hold at the reference size for **both** the read-heavy workload W1 and the long-history workload W2. T3 to T7
are measured on the workload named in §4.

## 4. Workload models

All generators are seeded and deterministic (`bench/perf/workloads.py`). A run records the seed and the realised mix.

| ID | Name | Model | What it stresses |
|---|---|---|---|
| **W1** | read-heavy agent | Many entities (people and organisations), a heavy-tailed number of reports per key (most keys 1 to 3, a few up to the budget), mostly re-assertions of the same value by the same origin group, occasional changes and corrections, 1 to 3% withdrawals, derived `work_city` reads. Queries per appended report **r ≥ 1.1** (default r = 3), 85% current-time reads, 15% `belief_as_of` reads, queries biased towards recently written keys | the steady-state serving cost; r ≥ 1.1 is the region where the study says the store pays for itself |
| **W2** | long-history keys | A small set of hot keys receives reports up to the environment budget (7) with changes, corrections and disputes, over and over (withdrawals restore the key to a fresh history); a background of W1 traffic | the 2^n per-key cost at the validated limit |
| **W3** | cascade-heavy | Reports that touch keys with many dependents (organisation `hq_city` read by many `employer` keys) and withdrawals two steps upstream | dependency closure marking, derived revision, the generation barrier |
| **S1** | study replay | The frozen Setting 1 streams through the full pipeline (via `harness/convert.py`), when the study data is available | realism check against the benchmark the system is validated on; not a scale workload |

## 5. Scales and method

* **Scales:** a small, a medium and a large run (reports in the log: 10^3, 10^4, and the largest size reachable inside a
  declared wall-clock budget up to the 10^5 reference size). If the large run cannot reach 10^5 inside its budget, the report
  says so and states the size reached; **the target is then not met, not extrapolated.** Only T3's "per million" figure is an
  extrapolation, and it is labelled as one.
* **Latency:** per-operation `time.perf_counter_ns` around the public call; p50, p95 and p99 per window and overall; the
  first 5% of operations of each run is warm-up and excluded from the percentiles but reported.
* **Memory:** peak and current RSS from `resource.getrusage` and `/proc` or `ps` where available, plus `tracemalloc` for the
  Python heap on the small scales only (tracemalloc slows the run).
* **Recovery:** a child process appends under load and is killed with SIGKILL at a random point; the parent reopens the file
  and measures time to first correct query, counts committed versus visible reports, runs `recover()` and `verify_log`.
* **Crossover:** on W1, per appended report the store pays its append and then answers r queries by `Memory.query`; replay
  pays the append (the log write only, no revision) and answers each query by recomputing the key's belief from the log with
  `KernelReviser.recompute` semantics. r\* is the intersection of the two cost lines, computed from the measured per-operation
  costs rather than by running both to the same size.
* **Environment** (Python version, CPU, OS, SQLite version, git commit) is written into every JSON report.

## 6. What is not measured

* **Concurrency and multi-process access** (readers and a writer, an MCP server with several clients): one process, one
  writer only.
* **Histories longer than the generator produces**, and keys above the environment budget (those answer
  `ResourceLimited(environment_budget)` by design; the cost of the refusal is not benchmarked).
* **Network, model and extractor latency.** The extractor is out of the loop; reports arrive typed.
* **A real agent workload.** The design requires a one-week load characterisation on a real workload (T-J4); the numbers here
  are synthetic and laptop-bound, and nothing in this document should be read as a claim that they extrapolate beyond the
  sizes actually measured.
* **Cold-cache reads and disk-bound behaviour** beyond what a laptop SSD gives.

## 7. How misses are handled

A target that is missed stays declared and is reported as missed. The measured section records the bottleneck with profile
evidence so that a later task can address it. A target is only ever changed by a dated amendment in this file that states what
was observed and why the original number no longer fits the use case; it is never edited to match a result.
