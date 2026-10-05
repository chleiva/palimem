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

---

## 9. After optimisation (Lane O), measured on 2026-10-05, same laptop, same commands, same seeds

**Same caveats as §8: one laptop, one seed, one pass, synthetic workloads, not a load characterisation (T-J4). The declared
targets in §3 are unchanged.** The pass is `bench/perf/run_all.sh` with the output redirected to `bench/perf/results/after/`
(the "before" files in `bench/perf/results/` are untouched). It took about 7 minutes; the "before" pass took about 65.

### 9.1 What changed in the code (no answer changed, see 9.5)

1. **Targeted revision.** `justify_derived` took the breakpoints of *every* entity's keys of every base attribute; it now
   takes them from the keys its rules can read (`relevant_base_keys`, `src/palimem/kernel/derive.py`), and the revision
   re-justifies only the derived keys whose rules read a changed key (`src/palimem/engine/dependents.py`: reverse plans
   derived statically from the rules, plus a value index over the attributes that bind entity variables, rebuilt lazily from
   the store and dropped whenever a belief may change outside a revision). `Pipeline(exhaustive=True)` keeps the old
   behaviour for audits and tests.
2. **Bounded caches.** Admission evaluations kept: 4 instead of 512. The decision on a report's own merits is memoised for
   the life of the log (it depends only on the report, its target and the config). Per-evaluation facts (direct entries,
   entity universe) are computed once per evaluation instead of once per recomputed key.
3. **Derived writes.** A derived version is written when its content, its pins or its pinned base versions change, or when the
   store's own closure marks it. Measured on W1 at 1,000 reports: of 1,821 derived writes, **42 (2.3%) were forced by the mark
   alone**; the rest are real changes (see R3 below: this contradicts the hypothesis of §8.4, F3).
4. **`verify_beliefs`.** On demand per key (`keys=`), a `since_lsn` filter, and a checkpointed incremental mode
   (`verify_beliefs_incremental`); the full mode stays and is the only one that sees an old row edited in place.
5. **Completion jobs.** A job that ends a run with keys still unfinished is counted; after 3 such runs it is *blocked* with the
   reason recorded and is no longer retried on every append. `retry_blocked()` puts blocked jobs back to pending.

### 9.2 Before and after, same runs

| run | reports reached | append p50 / p99 ms, before | append p50 / p99 ms, after | RSS slope KiB/report, before → after | disk KiB/report, before → after |
|---|---|---|---|---|---|
| W1, default, 300 | 300 | 23.1 / 43.3 | 1.2 / 11.6 | 99.2 → 18.5 | 21.0 → 22.0 |
| W1, default, 1,000 | 1,000 | 178.5 / 245.8 | 1.9 / 38.5 | 175.8 → 7.6 | 18.1 → 19.4 |
| W1, 50 people, 1,000 | 1,000 | 15.1 / 43.8 | 2.0 / 19.9 | 199.5 → 7.6 | 15.1 → 17.1 |
| W1, 250 people, 300 | 300 | 3.7 / 206.7 | 1.1 / 3.2 | 91.6 → 13.3 | 19.2 → 18.3 |
| W2, default, 300 | 300 | 3.8 / 32.5 | 1.2 / 5.7 | 98.7 → 17.1 | 19.7 → 20.1 |
| W2, default, 1,000 | 1,000 | 9.5 / 232.4 | 2.0 / 4.6 | 209.7 → 5.5 | 11.2 → 12.2 |
| W3, default, 300 | 300 | 35.6 / 61.4 | 2.5 / 50.3 | 100.9 → 26.9 | 60.1 → 72.6 |
| W3, default, 1,000 | 1,000 | 240.0 / 359.4 | 3.7 / 156.1 | 195.5 → 15.9 | 91.6 → 118.3 |
| **W1, default, 10,000 (20 min budget)** | **72** → **10,000** | 16,486 / 17,544 (at 72 reports) | 11.3 / 252.3 | n/a → 5.2 | 87.4 (at 72) → 50.0 |
| W1, 250 people, 10,000 (25 min budget, supplementary) | 7,852 → **10,000** | 239.0 / 588.7 | 11.2 / 249.4 | 141.6 → 4.2 | 43.0 → 59.8 |

The reference size (10⁵ reports) was still not reached, because no run asked for it; the largest run now completes 10,000
reports in 188 s where the same run reached 72 reports in 1,205 s. Elapsed times: W1 1,000 reports 110 s → 4 s; W3 1,000
reports 240 s → 16 s. RSS at the end of the 10,000-report run is 90 MB (before: 1,306 MB at 7,852 reports, supplementary run).

Other measurements, before → after:

| quantity | before | after |
|---|---|---|
| `verify_beliefs` after the kill, 300 preload | 1.21 s (305 reports) | 0.17 s (370 reports) |
| `verify_beliefs` after the kill, 1,000 preload | 6.73 s (1,004 reports) | 0.46 s (1,111 reports) |
| time to first correct query after a kill (T4) | 0.09 s / 0.13 s | 0.10 s / 0.13 s, 0 acknowledged appends lost |
| crossover r\* against a cold replay, 1,000 reports | 3.60 | 0.10 |
| crossover r\* against a warm replay, 1,000 reports | 272.7 | 32.3 |
| store append mean, 1,000 reports | 110.7 ms | 3.04 ms |
| heap traced by tracemalloc, 400 reports, 50 people | 29.1 MB (400 cached evaluations) | 1.0 MB (4 cached evaluations) |

(The recovery runs differ in size between the passes because the kill lands after a fixed delay and the store is now faster:
the counts of reports at the kill are 305 → 370 and 1,004 → 1,111. `verify_beliefs` is therefore faster for a *larger* log.)

### 9.3 Verdicts against the declared targets

"Holds at the size reached" is not "holds at the reference size": every extrapolation is labelled.

| target | declared | measured (largest size) | holds at the size reached? | verdict |
|---|---|---|---|---|
| T1 query latency | p50 ≤ 5 ms, p99 ≤ 25 ms | 10,000 reports: p50 0.13, p99 4.64 ms; 250 people: p50 0.68, p99 15.5 ms | yes | **not shown at 10⁵**; the 250-people run's p99 is 15.5 ms at 10,000 reports against 0.35 ms at 300, so the trend is upward |
| T2 append-to-visible | p50 ≤ 25 ms, p99 ≤ 100 ms | 10,000 reports: p50 11.3 ms, **p99 252 ms** | p50 yes, **p99 no** | **missed** at the size reached (p99) |
| T3 memory | ≤ 1 KiB/report, RSS ≤ 300 MB at 10⁵ | slope 5.2 KiB/report; RSS 90 MB at 10,000 | **no** (about 5 times over; was about 175) | **missed**; linear extrapolation to 10⁵ reports gives about 0.5 GB (estimate) |
| T4 recovery | ≤ 2 s, nothing lost | 0.13 s at 1,111 reports, 0 lost | yes | **not shown at 10⁵** |
| T5 crossover | r\* ≤ 2 at 10⁴ reports | cold 0.10 at 1,000 reports; warm 32.3 | cold yes, warm no | **not shown at 10⁴**; the store barely beats a *warm* replay |
| T6 append scaling | p99 ratio largest / smallest scale ≤ 4 | W1 300 → 10,000: 11.6 → 252.3 ms, ratio **21.8**; W1 300 → 1,000: 3.3; W2 300 → 1,000: 0.81 | W1 over the long range **no** | **missed** (W1, 300 to 10,000); the 300 to 1,000 ratios meet it, but that is a shorter range than the one now measured, so they are not comparable with the §8 ratios of 5.7 and 7.2 |
| T7 disk | ≤ 5 KiB/report | 50.0 KiB/report at 10,000 (47.0 KiB after a WAL checkpoint); 19.4 KiB at 1,000 | **no** (about 10 times over at 10,000) | **missed**; per-report disk grows with scale |

### 9.4 What remains, with profile evidence

**R1. Append cost is still linear in log length, because every revision makes whole-log passes.** Plain appends (no derived
fan-out) cost about 2 ms at 1,000 reports and about 10 ms at 10,000 (mean `affiliations:assert` 10.1 ms, `employer:assert`
11.0 ms, `residence:assert` 10.0 ms). A 100-append profile window at 10,000 reports
(`bench/perf/results/after/profile-w1-10000.json`) attributes it to `Admitter._evaluate` (1.7 s cumulative over 100 appends,
17 ms each), `direct_entries` (1.1 s, 11 ms each) and the per-key diff in `KernelReviser.revise` (`_ids`, 0.5 s), with about
70,000 `_rid` calls per append. The cost at 10⁵ reports on this trend is about 100 ms per plain append (linear extrapolation,
an estimate), which would miss T2's p50. The fix is an *incremental* admission evaluation (extend the previous evaluation by one
entry and recompute only what the new entry can change: its own decision, its target, confirmations of its key, source-wide
withdrawals) and an incremental per-key admitted-entries index. It was not attempted here because confirmation and withdrawal
effects reach back into earlier decisions, so it needs its own equivalence tests against the full evaluation.

**R2. The p99 append tail is fan-out, by design of pinning.** At 10,000 reports `hq_city:change` appends average 107 ms (p95
448 ms, max 780 ms) and `hq_city:withdraw` 256 ms, against 10 to 16 ms for every other kind except `hq_city:assert` (1 ms, the
seeding of organisations). An organisation's city is read by every employee's `work_city`, and each employee's derived belief
pins the exact version of the organisation's belief it consumed, so a new organisation version means a new derived version per
employee (about 20 employees per organisation at this population: 2,500 people, 125 organisations). That is the declared
behaviour (derived beliefs pin the base versions they consumed), not a bug, and it sets a floor on T2's p99 and on T6 for
fan-out workloads unless the pinning granularity changes (a contract decision, out of scope).

**R3. Disk per report grows with scale and comes from the same fan-out, not from rewriting unchanged versions.** The F3
hypothesis of §8.4 was wrong: only 42 of 1,821 derived writes (2.3%) at 1,000 reports were forced by the store's mark alone.
At 10,000 reports the database holds 40,096 `work_city` versions for 10,000 appends (4 per append, mean 8.6 KB of JSON each,
346 MB of the 366 MB of belief JSON; the `beliefs` table occupies 408 MB); `belief_pins` and its two indexes add 147 MB. Each
version is large because it carries the whole timeline with per-interval supports and the union of pinned report ids. Options,
none implemented: compress the belief JSON column (a storage-layer change, but the tamper tests and `verify` read the JSON
column directly, so it needs care); store pins as a separate row per report only for the current version; or amend T7 (the
author's decision, with a dated note in this file). The WAL accounts for about 6% of the file at 10,000 reports (511.9 MB
against 481.1 MB after a checkpoint), so the metric is not an artefact of an uncheckpointed WAL.

**R4. Memory: 5.2 KiB per report remains.** At 400 reports tracemalloc shows 1.0 MB retained (2.5 KiB per report):
0.19 MB in `admission/admitter.py` (the memoised decisions), 0.18 MB in JSON decoding, 0.14 MB in the SQLite layer, 0.08 MB in
dataclasses and 0.07 MB in the pipeline, i.e. the decoded in-memory log plus one decision per report. The slope at 10,000
reports (5.2 KiB per report, measured by RSS) is about twice that, and the difference was **not isolated**: it may be
interpreter and allocator overhead, SQLite's page cache, or structures that only grow at scale. Reaching 1 KiB would need a
windowed log or an incremental evaluation that does not hold every decision (R1).

**R5. T5 against a warm replay.** The store answers a query in 0.20 ms against 0.29 ms for a replay that reuses the cached
admission evaluation at the head, so its read advantage over a cached recomputation is small; the store's value is against a
cold replay (29 ms) and in the barrier and provenance guarantees, not raw read speed.

### 9.5 What was verified so that no answer changed

All with the final code, from the worktree, `PALIMPSEST_STUDY_DIR=$HOME/palimpsest`:

| gate | command | result |
|---|---|---|
| kernel vs frozen gold, strict provenance, all streams | `python -m harness.kernel_diff --source-retract sidetable --strict --provenance strict` | 500 streams, 30,272 queries, **0 disagreements**; provenance vs oracle 0 disagreements |
| full pipeline vs frozen gold, in memory, strict provenance, all streams | `python -m harness.pipeline_diff --backend memory --provenance strict` | 500 streams, 30,272 queries, 65,632 appends, **0 disagreements**, 0 resource-limited; stored supports vs audit recomputation 0 mismatches of 26,182 checked |
| full pipeline on SQLite, strict provenance, every 2nd stream | `python -m harness.pipeline_diff --stride 2 --backend sqlite --provenance strict` | 250 streams, 15,149 queries, 32,714 appends, **0 disagreements**, 0 mismatches of 13,083 supports checked |
| gates can fail | `pipeline_diff --limit 25 --inject-bug {mutate-answer,no-source-retraction,self-update}`; `kernel_diff --limit 100 --inject-bug {self-update,ignore-corrections,mutate-answer}`; `kernel_diff --limit 100 --source-retract sidetable --provenance strict --strict --inject-bug drop-provenance` | all seven exit 1 |
| targeted vs exhaustive revision | `tests/test_revise_targeted.py` | the same answers (head, `belief_as_of`, `valid_at`) on random operations, on both backends; with the reverse index deliberately broken the test fails on 2 of 6 seeds |
| memoised admission vs a fresh evaluation | `tests/test_revise_targeted.py::test_memoised_admission_equals_a_fresh_evaluation` | identical decisions and withdrawals |

The quadratic regression is counted in operations, never in seconds (`Pipeline.stats`): an append that changes one person's
employer justifies the same number of derived keys and reads the same number of keys with 20, 80 or 320 entities
(`test_a_person_append_does_a_constant_amount_of_derived_work`, `test_work_per_append_does_not_grow_with_the_entity_count`),
and an organisation change justifies its employees and only them
(`test_an_organisation_change_justifies_its_employees_and_only_them`).


## 10. After incremental admission (Lane O2), measured on 2026-10-05, same laptop

**Same caveats as §8 and §9: one laptop, one seed, one pass, synthetic workloads; the declared targets in §3 are unchanged.**
"Before" below is the code at the end of §9 (commit `ec4f6b6`), re-measured in the same session with the same commands and
seeds, so the two columns share the machine state. Raw results: `bench/perf/results/o2/` (`before-*`, `after-*`).

### 10.1 What changed (no answer changed, see 10.5)

1. **Admission is maintained, not re-evaluated.** `IncrementalAdmission` (`src/palimem/admission/incremental.py`) holds the
   evaluation's state for the committed head and updates it by what an append can change: the new report's own-merit
   decision, its withdrawal effects, the confirmations of the keys that hold quarantined evidence, and the direct evidence of
   the keys whose status changed. The whole-log `Admitter.evaluate` is the audit oracle and is no longer on the append path
   (`tests/test_pipeline_incremental.py` fails if an append runs it).
2. **Withdrawal effects are maintained too.** The first draft recomputed them over *all actors* for every actor append
   (O(actors), and about half the appends of W1 are withdrawals or corrections, so it still grew with the log). They are now
   kept as per-target claims plus a set of actors that no longer act, and an actor append recomputes only the cascade it
   causes, in descending LSN order (the status of an actor depends only on higher-LSN actors). An actor append costs the same
   with 10 or 1,500 actors in the log (`tests/test_admission_incremental_work.py`, operation counts).
3. **Completion jobs recompute from the same state.** The first draft routed `KernelReviser.recompute` through a whole-log
   evaluation that the old evaluation cache had been hiding, which would have made every append O(log). Found by the work-count
   test, not by a benchmark.
4. **The incremental path reads the log uncached** (`ViewLog.peek` / `scan`): it keeps the entries it needs, and reading
   through the caching view held a second decoded copy of every report.
5. **Audit modes.** `Pipeline(admission=...)` / `PALIMEM_ADMISSION`: `incremental` (default), `whole-log` (the previous path),
   `crosscheck` (incremental, compared with the whole-log evaluation decision for decision after every append).

### 10.2 Before and after (W1, 250 people, so reports per person grow with the log)

| reports | append p50 ms | append p95 ms | append p99 ms | sustained appends/s | plain append mean ms (`residence:assert`) | RSS slope KiB/report |
|---|---|---|---|---|---|---|
| 1,000 before → after | 1.89 → 0.80 | 4.67 → 3.35 | 59.7 → 58.4 | 319 → 453 | 1.5 → 0.6 | 13.7 → 13.4 |
| 3,000 before → after | 4.20 → 1.13 | 8.90 → 5.54 | 91.6 → 83.5 | 165 → 299 | 3.2 → 0.8 | 7.8 → 6.3 |
| 10,000 before → after | **11.61 → 1.88** | 26.05 → 10.20 | 257.5 → 240.1 | 58 → 124 | **8.9 → 1.0** | 3.7 → 5.0 |

W1 with the default population (people = reports / 4, so reports per person stay constant), 10,000 reports: p50 1.08 ms,
p95 8.4 ms, p99 237.6 ms, 141 appends/s, 93 s (1,000 reports: p50 0.80 ms). Against Lane O's final run of the same size
(p50 11.3 ms, 188 s) that is about 10 times lower p50 and 2 times faster in total.

* **Plain appends no longer scan the log.** `residence:assert` was 1.5 → 3.2 → 8.9 ms at 1,000 → 3,000 → 10,000 reports and is
  now 0.6 → 0.8 → 1.0 ms. The remaining growth (1.7 times over a 10 times longer log) is per-key history, not log length: with
  the default population (constant reports per person) p50 goes from 0.80 to 1.08 ms over the same range (1.35 times).
* **The tail did not move, as predicted in §9.4 R2.** `hq_city:change` appends average 140 ms at 10,000 reports (154.6 before):
  an organisation's city is read by every employee's `work_city`, and each derived belief pins the organisation's version, so
  one change writes one new derived version per employee. p99 is therefore 240 ms against the declared 100 ms: **T2 p99 is
  still missed**, and T6 (p99 ratio, largest scale to smallest) is unchanged in kind: 59.7 → 257.5 ms over 1,000 → 10,000
  reports before (4.3 times), 58.4 → 240.1 ms after (4.1 times). T6 is declared over 300 → 10,000 and this pass has no 300
  point, so the verdict stays "missed, not re-measured at 300".
* **Disk is unchanged** (this change does not touch what is stored): 59.1 KiB per report at 10,000 reports with 250 people
  (checkpointed), 48.1 KiB with the default population. **T7 is still missed** (about 10 times over).

### 10.3 Memory

| measure | whole-log admission | incremental admission |
|---|---|---|
| tracemalloc retained, 400 reports, 50 people | 2.41 KiB per report | 1.85 KiB per report (−23%) |
| tracemalloc retained, 3,000 reports, 250 people | 2.19 KiB per report | 1.67 KiB per report (−24%) |
| tracemalloc peak, 3,000 reports, 250 people | 4.65 KiB per report | 3.69 KiB per report (−21%) |

Both rows come from the same code in the same session with only `PALIMEM_ADMISSION` changed, so they isolate this change
(`python -m bench.perf.heap_compare`). The incremental state's first draft *raised* retention by 0.7 KiB per report, because
it kept the engine's entries while the log view cached decoded copies; point 4 above removed that duplicate.
**The RSS slope at 10,000 reports is higher (3.7 → 5.0 KiB per report) and was not isolated**: RSS includes memory the
allocator has not returned and measures whole-process behaviour, while tracemalloc measures retained Python objects, and the
two disagree here. Candidates (not tested): allocator behaviour with the larger working set, the per-key tuples of direct
evidence rebuilt on a key's change, and the claim dictionaries. **T3 is still missed** (about 5 times over).

### 10.4 What remains (profile, 100 appends after 9,900, 250 people: `bench/perf/results/o2/after-profile-w1-10000-p250.json`)

`Admitter._evaluate`, `direct_entries` and `_compute_withdrawn` are gone from the top of the profile. What is left: SQLite
statements (27,200 per 100 appends, 272 per append, 0.145 s), `kernel/provenance.py:minimize` (92,800 calls, 0.082 s, the
environment minimisation of the derived beliefs' supports), JSON encoding of belief versions (0.068 s) and belief construction.
That is the cost of writing fan-out derived versions (R2, R3), which is the design's pinning granularity, not admission.

### 10.5 What was verified so that no answer changed

All with the final code. Commands from the repository root, `PALIMPSEST_STUDY_DIR=$HOME/palimpsest`.

| check | command or test | result |
|---|---|---|
| full pipeline vs frozen gold, in memory, strict provenance, all streams | `python -m harness.pipeline_diff --backend memory --provenance strict` | 500 streams, 30,272 queries, 65,632 appends, **0 disagreements**, 0 provenance disagreements, 0 of 26,182 stored supports differ from the audit recomputation; 401 s (752 s with the first draft) |
| full pipeline on SQLite, every 2nd stream | `python -m harness.pipeline_diff --backend sqlite --stride 2` | 250 streams, 15,149 queries, 32,714 appends, **0 disagreements** |
| incremental vs whole-log oracle after every append, frozen streams | `PALIMEM_ADMISSION=crosscheck python -m harness.pipeline_diff --backend memory --stride 10` | 50 streams, 3,022 queries, 6,490 appends, **0 disagreements** (every append compared) |
| kernel vs frozen gold | `python -m harness.kernel_diff --source-retract sidetable --strict --provenance strict` | 30,272 queries, **0 disagreements**, 0 unexplained |
| gates can fail | `pipeline_diff --limit 25 --inject-bug {mutate-answer,no-source-retraction,self-update}` and `--provenance strict --inject-bug drop-provenance` | all four exit 1 |
| equivalence on random streams | `tests/test_admission_incremental.py` | **2,040 random streams** (340 seeds × 6 admission configurations: product, grants, not-live, compat, compat-live, source-status overrides), 40 reports each, with confirmations, quarantine, origin groups, corrections, withdrawals of actors, source-level withdrawals, allege downgrades, agent origins, attributions and effects reaching far back; after **every** append every decision (outcome, reason, version, confirmers, effective cue, withdrawals, authority), the withdrawal map (`by` and `kind`), the direct evidence per key, the attributions, the entity universe and the emitted admission records equal a fresh whole-log evaluation, and `audit()` finds the maintained withdrawal state equal to a from-scratch recomputation |
| rollbacks | `test_rolled_back_appends_leave_no_trace` | random appends applied and not committed (as a failed transaction): the state returns to the committed prefix from the undo journal, with zero rebuilds |
| the net bites | `tests/test_admission_incremental.py -k mutation` | deliberately broken versions are all caught: no confirmation updates, rollbacks never undone, withdrawn actors keep acting, oldest actor owns a withdrawal, no status cascade, withdrawn ignored in direct evidence, attributions dropped, later reports of a covered source escape; and, end to end, a broken state under `crosscheck` raises |
| work does not grow | `tests/test_admission_incremental_work.py`, `tests/test_pipeline_incremental.py` | plain appends scan nothing however many actors exist; an actor append costs the same with 10 or 1,500 actors; a withdraw-of-a-withdraw chain costs constant work and ends in the whole-log state; no append runs `Admitter.evaluate` |

**Limits.** The random streams are short (40 reports): the long-log behaviour is covered by the frozen streams (a few
hundred reports at most) and by a 3,000-report benchmark workload run under `crosscheck` (§10.6), not by exhaustive search.
Whether a subclass that overrides the whole-log internals stays equivalent is the subclass's burden: `supports_incremental`
sends any subclass without the two overlay hooks to the whole-log path, and the compat profile's source retraction
(`CompatAdmitter`) provides them and is exercised by the frozen-stream gates above.

### 10.6 A long realistic log under `crosscheck`

`PALIMEM_ADMISSION=crosscheck python -m bench.perf run w1 --reports 3000 --persons 250` (the benchmark workload itself, with
withdrawals, corrections, changes and organisation fan-out): all 3,000 appends completed with the incremental state compared
against a whole-log evaluation of the same prefix after **every** append (decisions, withdrawals, direct evidence, attributions
and the emitted admission records; any difference raises and aborts the run), 0 resource-limited answers, 120 visibility checks
all visible, no pending completion jobs at the end (`bench/perf/results/o2/crosscheck-w1-3000-p250.json`, 32 s against 14 s
without the comparison). This is the longest log the equivalence has been checked on; the 10,000-report runs of §10.2 were
not compared append by append (that comparison is quadratic).
