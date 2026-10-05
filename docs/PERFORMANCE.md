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

---

## 8. Measured on 2026-10-05, Apple M3 laptop (8 cores, 16 GB)

**These are laptop numbers from one synthetic workload family, one seed, and one pass. They are not a load characterisation
(T-J4) and nothing here extrapolates beyond the sizes actually measured.** Environment (also stored in every result file):
Apple M3, Darwin 25.3.0, Python 3.13.7, SQLite 3.50.4, `SQLiteBackend` on a local SSD file, one process, one writer,
open-world profile, `justified` policy. Raw results: `bench/perf/results/*.json`; the whole pass is `bench/perf/run_all.sh`
(about 65 minutes); the tables below are `python -m bench.perf report` over those files.

**The reference size (10^5 reports) was not reached by any run, and cannot be reached by the current implementation in a
usable time (finding F1). By the rule of §5 every target judged against the reference size is therefore reported as missed,
not extrapolated.** Each row below also says whether the target would hold at the size that *was* reached.

### 8.1 What was run

| Run | Purpose | Reached |
|---|---|---|
| W1, W2, W3 at 300 and 1,000 reports, people = reports / 4 (default scaling) | the three workloads at two scales | completed |
| W1 at 10,000 reports, default scaling, 20 minute budget | the "largest reachable" scale (§5) | **72 reports** (stopped by the budget) |
| W1 at 1,000 reports with 50 people; W1 at 300 reports with 250 people | separate entity count from log length | completed |
| W1 at 10,000 reports with 250 people, 25 minute budget (*supplementary*) | log length at a fixed population | 7,852 reports (stopped by the budget) |
| kill and recover at 300 and 1,000 preloaded reports | T4 | completed |
| store versus replay at 300 and 1,000 reports | T5 | completed |
| cProfile (100-append windows at 1,000 reports) and tracemalloc (400 reports) | bottleneck evidence | completed |

Supplementary runs (explicit people count) never judge a target; they only explain the primary ones.

### 8.2 Runs

| workload | people | reports reached | stopped early | elapsed s | append p50 / p95 / p99 ms | appends/s | query p50 / p95 / p99 ms | RSS MB (end) | RSS slope KiB/report | disk KiB/report |
|---|---|---|---|---|---|---|---|---|---|---|
| W1 | n/4 | 72 | yes | 1205 | 16486.4 / 17319.9 / 17544.2 | 0.1 | 0.12 / 0.19 / 0.20 | 36 | n/a | 87.4 |
| W1 | 250 | 300 | no | 30 | 3.7 / 197.6 / 206.7 | 10.1 | 0.11 / 0.32 / 0.43 | 52 | 91.6 | 19.2 |
| W1 | n/4 | 300 | no | 5 | 23.1 / 35.2 / 43.3 | 59.8 | 0.11 / 0.31 / 0.48 | 53 | 99.2 | 21.0 |
| W1 | 50 | 1000 | no | 18 | 15.1 / 37.7 / 43.8 | 58.7 | 0.24 / 0.84 / 1.72 | 187 | 199.5 | 15.1 |
| W1 | n/4 | 1000 | no | 110 | 178.5 / 226.5 / 245.8 | 9.2 | 0.12 / 0.64 / 1.90 | 173 | 175.8 | 18.1 |
| W1 (supplementary) | 250 | 7852 | yes | 1500 | 239.0 / 359.8 / 588.7 | 5.4 | 0.70 / 5.60 / 13.23 | 1306 | 141.6 | 43.0 |
| W2 | n/4 | 300 | no | 5 | 3.8 / 31.8 / 32.5 | 68.5 | 0.12 / 0.38 / 0.58 | 53 | 98.7 | 19.7 |
| W2 | n/4 | 1000 | no | 102 | 9.5 / 210.5 / 232.4 | 9.9 | 0.13 / 0.87 / 1.71 | 194 | 209.7 | 11.2 |
| W3 | n/4 | 300 | no | 11 | 35.6 / 50.1 / 61.4 | 28.2 | 0.26 / 0.84 / 1.62 | 56 | 100.9 | 60.1 |
| W3 | n/4 | 1000 | no | 240 | 240.0 / 309.6 / 359.4 | 4.2 | 0.35 / 2.60 / 5.44 | 185 | 195.5 | 91.6 |

No run produced a `ResourceLimited` answer, a visibility failure after an append, or a completion job left pending
(`integrity` in each result file). Append latency is bimodal (see F1), so p50 alone is not a safe summary.

### 8.3 Verdicts against the declared targets

| target | workload | declared | measured at (reports) | measured | would it hold at the size reached? | verdict |
|---|---|---|---|---|---|---|
| T1 | W1 | p50 ≤ 5 ms, p99 ≤ 25 ms | 1,000 | p50 0.12; p99 1.90 | yes | **missed** (reference size not reached) |
| T1 | W2 | p50 ≤ 5 ms, p99 ≤ 25 ms | 1,000 | p50 0.13; p99 1.71 | yes | **missed** (reference size not reached) |
| T2 | W1 | p50 ≤ 25 ms, p99 ≤ 100 ms | 1,000 | p50 178.5; p99 245.8 | **no** | **missed** |
| T2 | W2 | p50 ≤ 25 ms, p99 ≤ 100 ms | 1,000 | p50 9.5; p99 232.4 | **no** (p99) | **missed** |
| T3 | W1 | ≤ 1 KiB/report marginal, RSS ≤ 300 MB at 10^5 | 1,000 | slope 176 KiB/report; RSS 173 MB | **no** (about 175 times over) | **missed** |
| T4 | — | ≤ 2 s to first correct query; nothing acknowledged lost | 1,004 | 0.13 s; 0 lost; recover, `verify_log`, `verify_beliefs` ok | yes | **missed** (reference size not reached) |
| T5 | W1 | r\* ≤ 2.0 at 10^4 reports | 1,000 | r\* 3.60 (cold replay) | **no** | **missed** |
| T6 | W1 | p99 append ratio, largest / smallest scale ≤ 4 | 300 → 1,000 | 43.3 → 245.8 ms, ratio 5.7 | **no** | **missed** |
| T6 | W2 | same | 300 → 1,000 | 32.5 → 232.4 ms, ratio 7.2 | **no** | **missed** |
| T7 | W1 | ≤ 5 KiB/report on disk | 1,000 | 18.1 KiB/report | **no** (3.6 times over) | **missed** |

In words: **T1 and T4 hold at the sizes measured, but were not shown at the reference size; T2, T3, T5, T6 and T7 are missed
outright, at 1,000 reports.** The extrapolated figure for T3 (about 168 GiB per million reports, a linear extrapolation of
the 176 KiB/report slope) is a labelled estimate, not a measurement; the supplementary run measured 1.3 GB at 7,852 reports.

### 8.4 Findings

**F1. Append cost grows faster than linearly with the number of entities; log length matters little.** Appends to attributes
that feed no derived key (`residence`, `affiliations`) cost 2 to 5 ms at every size. Appends to attributes that feed the
derived `work_city` (`employer`, `hq_city`) cost:

| run | entities | derived-affecting appends | mean ms | other appends, mean ms |
|---|---|---|---|---|
| 300 reports, default | 80 | 165 | 28.9 | 1.9 |
| 1,000 reports, 50 people | 55 | 505 | 28.5 | 5.3 |
| 300 reports, 250 people | 262 | 155 | 189.6 | 2.1 |
| 1,000 reports, default | 262 | 513 | 207.5 | 4.8 |
| 10,000 target, default (72 reached, all organisation `hq_city` seeds) | 2,625 | 72 | 16,486 | n/a |

At a fixed 262 entities a 3.3 times longer log adds 9% (189.6 to 207.5 ms). Going from 262 to 2,625 entities multiplies the
cost by about 80 (roughly E^1.9), although that run's log is 14 times shorter; going from 80 to 262 multiplies it by about 7
(roughly E^1.6). The profile at 1,000 reports shows why: over 100 appends `justify_derived` is called **13,362 times
(134 per append)** and accounts for 27.5 of 34.9 profiled seconds (79%), with 10.5 million calls to `base_attrs_closure`.
The revision step re-justifies the derived attribute for **every entity** (`for e in ks.entities` in
`src/palimem/engine/pipeline.py`, `KernelReviser.revise`) rather than for the entities whose inputs changed, and each
`justify_derived` call itself gathers the breakpoints of every (entity, base attribute) key in the store
(`src/palimem/kernel/derive.py`, lines 271 to 274). One call is therefore O(entities), one call per entity is O(entities)
calls, and an append costs O(entities²). With 55 entities the same 100 appends call `justify_derived` 2,970 times. At the
default population scaling the reference size means 25,000 people, and the measured trend puts a single derived-affecting
append in the region of tens of minutes: **the reference size is out of reach by design of the current revision step, not by
tuning.** This is an estimate from a trend over three points and is labelled as such.

**F2. Resident memory per report is roughly 175 times the target, and the heap evidence points at the admission
evaluation cache.** RSS slope is 92 to 210 KiB per report in every run (T3 asks for 1 KiB). The supplementary run reached
1.3 GB RSS (1.6 GB peak) at 7,852 reports. A tracemalloc run at 400 reports shows 29 MB traced, of which 22.5 MB sits in
`admission/admitter.py` and 5.4 MB in `admission/ids.py`, with 400 entries in the pipeline's evaluation cache
(`Pipeline._evals`, capped at 512). Each cache entry holds the admission evaluation of a whole log prefix, so memory grows
with (number of cached prefixes) times (log length) until the cap and linearly in the log afterwards. The numbers are
consistent with that mechanism; this task did not change the cache to prove it.

**F3. Disk use is 11 to 92 KiB per report (target 5), and workloads with dependency fan-out are worst.** W3 (cascades)
writes 60 to 92 KiB per report against 11 to 21 for W1 and W2, and the supplementary run reaches 43 KiB per report at
7,852 reports (293 MB). From the code, `revise` appends a new derived belief version for every entity whose key is in the
dependency closure of the touched key, even when the content is unchanged (`if not same or dk in marked`). That is the
likely cause; it was not isolated by experiment.

**F4. Query latency is small at these sizes but grows with log length.** p50 is 0.11 to 0.35 ms and p99 0.4 to 5.4 ms in
the 300 and 1,000 report runs. At a fixed population the supplementary run shows query p50 rising from 0.10 ms to 1.68 ms and
p99 from 0.4 ms to 17.0 ms between 250 and 7,250 reports. T1's p99 limit (25 ms) was not crossed within what was measured,
but the trend suggests it will be well before 10^5 reports; that is an observation of a trend, not a measurement at that size.
Over the 7,852 reports the append p50 also rose from about 173 ms to 343 ms at a constant 250 people.

**F5. Recovery is fast to the first answer; the integrity check is not.** Time from process start (imports included) to the
first correct query after a SIGKILL during an append load: **0.09 s at 305 reports and 0.13 s at 1,004**, with nothing
acknowledged lost, `recover()` ok, `verify_log` ok and `verify_beliefs` ok. `recover()` takes 11 ms and 32 ms and no completion
job needed running in these two runs. `verify_log` takes 9 and 27 ms, but `verify_beliefs` (recomputing beliefs with the
kernel) takes **1.2 s and 6.7 s**: 5.6 times more for 3.3 times more reports. It is not part of T4, but at that rate it
will not be usable as a routine check on large stores.

**F6. Store versus replay (T5).** Means over the measured operations:

| reports | store append | store query | replay: log-only append | replay: query, cold | replay: query, warm | r\* (cold) | r\* (warm) |
|---|---|---|---|---|---|---|---|
| 300 | 16.7 ms | 0.147 ms | 0.25 ms | 9.0 ms | 0.39 ms | 1.87 | 68.6 |
| 1,000 | 110.7 ms | 0.262 ms | 0.22 ms | 31.0 ms | 0.67 ms | 3.60 | 272.7 |

The store answers a query about 60 to 120 times faster than a *cold* replay (which re-evaluates admission over the whole log)
at these sizes. This is a different quantity from the study's 6 to 12 times, which compared full systems on its own
benchmark, so the two must not be set against each other. A *warm* replay that reuses the cached admission evaluation of the
current head is only 2.6 times slower per query than the store, which makes the store's read-side advantage over a cached
recomputation small, while its write cost grows with scale: r\* rose from 1.87 to 3.60 between 300 and 1,000 reports. At 10^4
reports (the declared size) it is not measured, and the trend is upward.

**F7. A key over the environment budget leaves a completion job pending for ever (found by accident, behaviour as designed).**
In an earlier run, a generator that let a key carry 8 live reports left 7 jobs pending after a clean 1,000-report run;
reads of those keys return `ResourceLimited(environment_budget)`, as S-06 requires. Two things follow for operators.
`complete_pending` re-attempts every pending job after each append, so a store that accumulates over-budget keys pays an
increasing cost per append; and the pending-job count is a cheap health signal. Every run now reports
`pending_completion_jobs_at_end`.

### 8.5 Method issues found and fixed during this task

Reported because the first results were discarded and re-run: (1) the generator undercounted live reports per key, because a
correction aimed at a correction *restores* its target (S-02) and the generator did not model that; some keys exceeded the
budget of 7, which also caused one intermittent failure of the recovery test in about 1 run in 50 (root cause found by
keeping the failing database; the generator now never targets a correction and caps corrections per key, and two end-to-end
tests fail if a workload pushes any key over the budget, checked by mutation); (2) the first recovery timing included
`verify_log` and `verify_beliefs`; T4 is now time to the first correct answer, with verification reported separately; (3)
warm-up was 5% of the target size, which swallowed every append of an early-stopped run; it is now 5% of the reached size;
(4) a 72-report run was being used as the smallest scale for T6; scale endpoints now need at least 100 reports.

### 8.6 Recommendations (nothing below was implemented in this task)

1. **Revise only the dependents of the changed keys.** For the rule `work_city <- employer, hq_city`, an `employer(p)` change
   affects only `work_city(p)`, and an `hq_city(o)` change affects only the `work_city` of entities whose `employer` is `o`.
   The store already keeps key and attribute dependency indexes; the revision step does not use them for this. This removes the
   superlinear term of F1 and most of F3. It is the single change that decides whether T2, T6 and the reference size are
   attainable, and should be a task of the engine owner (`src/palimem/engine/`).
2. **Bound the admission evaluation cache by size, or hold only the evaluations at the current and previous log position,
   which is all `revise` reads.** Expected effect: removes the dominant term in F2.
3. **Do not write an unchanged derived belief version**; write on a content change only, and record the closure marking
   separately. Expected effect: disk per report in F3.
4. **Make `verify_beliefs` incremental or sampled** (F5), and cap or batch the retries of jobs that cannot finish (F7).
5. **Targets: no amendment is proposed yet.** The numbers were justified by the use case and T1 and T4 are consistent with the
   measurements. T3 and T7 (1 KiB and 5 KiB per report) should be re-examined only after recommendations 1 to 3 have been
   applied and the benchmark re-run, because with one belief version written per entity per append no plausible target
   could be met, and relaxing them now would hide the defect. If after the fix the data still says the numbers do not fit the
   use case, the amendment belongs in this file with its date.
6. **Re-run on the same commands** (`bench/perf/run_all.sh`), then add the 10^4 and 10^5 scales, a second seed, and a real
   agent workload (T-J4) before any G2 claim.
