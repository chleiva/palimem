# Settings 2 and 3 of the study, replayed through the pipeline (T-J5)

Until this document the differential evidence covered Setting 1 only (the 500 generated streams, [`HARNESS.md`](HARNESS.md),
[`PIPELINE.md`](PIPELINE.md)). This is the replay of the study's other two settings, the part of the G2 criterion that asks that
the study's Setting 2 inputs re-run through the service give an empty diff against the deposit. Everything below was run on one
laptop on 2026-10-05, with no model call: the inputs are cached data from the published deposit.

## 1. What the settings are, and why they can be replayed without a model

In Settings 2 and 3 the study's systems did not see the stream. Each report was rendered as natural language, an LLM extracted a
typed claim from every line, and the study cached the result (`extracted.json`). Every system, the study's store included, then
ran on **those claims**. Setting 2 is 100 generated streams (a stronger backbone re-extracted 15 of them, `s2_strong`); Setting 3
is 30 streams assembled from Wikidata statement histories ([`docs/SETTING3.md` of the study](https://doi.org/10.5281/zenodo.23127764)).
The replayable input is therefore the cached claims plus the stream file that carries the queries, the domain and the sources.

The study also stored, per stream, its own answers on the same claims (`answers.json`: its incremental store, `palimpsest`, and
its symbolic replay, `log_rule_adjudicator`) and the frozen gold (`frozen.json`).

## 2. Data and its pin

`harness/studydata.py` pins the files the replay uses: 565 files in the published Zenodo deposit (SHA-256 of the zip
`686cf0bb…`), each checked against the deposit's own `MANIFEST.sha256` when `harness/s23_manifest.json` was built, and refused
if it differs at run time. The zip inside a study checkout is a later rebuild with a different hash and is not accepted.

| Dataset | Streams | Files used per stream | Stream file |
|---|---|---|---|
| `s2` (Setting 2, registered evaluation set) | 100 | `extracted`, `frozen`, `answers` | `data/setting2_100/<sid>.json` |
| `s2_strong` (Setting 2, stronger backbone) | 15 | the same | the same 100-stream directory |
| `s3` (Setting 3, Wikidata-derived) | 30 | the same | `data/setting3/<sid>.json` |

## 3. Method

`python -m harness.replay_s23` takes the stream, replaces its observations with the extracted claims in the study's order, converts
it with `harness.convert` (the paper's source-level retraction as the compat side table), appends it report by report through
`palimem.memory.Memory` on a fresh in-memory and a fresh SQLite backend with the real admission stage, kernel and store, answers
every query from the stored belief versions at the query's log position, and compares status, assertion and alternatives:

1. with the study's incremental store on the same claims (**primary**; the same differential as for Setting 1);
2. with the study's symbolic replay on the same claims (secondary, to attribute a difference to the study's own store/replay gap);
3. with the frozen gold (**informational**: extraction errors make the claims disagree with the gold in the study's store too, which
   is the paper's subject, not a conformance question).

Provenance is not compared (the cached answers carry the study store's interim provenance).

**One normalisation, counted.** The extractor sometimes emits a retraction whose target is not an assertion (8 claims in Setting 2)
or a correction whose `op_of` is not an assertion (3 claims). The study's own semantics make both no-ops (`Stream.admitted` only
returns assertions), and the contract cannot express a withdraw or a correction of something that is not a report. The replay drops
the first and turns the second into a plain assertion, and reports the counts; the differential tests that this preserves behaviour
(it does: 0 disagreements below).

## 4. Results

| Dataset | Backend | Streams replayed | Queries compared | Disagreements with the study's store | With the study's symbolic replay | Gold agreement (palimem / study store) |
|---|---|---|---|---|---|---|
| `s2` | memory | 100 of 100 | **6,737** | **0** | 6,737 of 6,737 equal | 6,285 / 6,285 |
| `s2` | SQLite | 100 of 100 | 6,737 | 0 | 6,737 of 6,737 equal | 6,285 / 6,285 |
| `s2_strong` | memory | 15 of 15 | 994 | 0 | 994 of 994 equal | 956 / 956 |
| `s2_strong` | SQLite | 15 of 15 | 994 | 0 | 994 of 994 equal | 956 / 956 |
| `s3` | memory | **29 of 30** | 690 of 714 | 0 | 690 of 690 equal | 675 / 675 |
| `s3` | SQLite | 29 of 30 | 690 of 714 | 0 | 690 of 690 equal | 675 / 675 |

* The 6,737 Setting 2 queries are the same count the study reports for its Setting 2 evaluation set.
* Every slot type is covered with 0 disagreements. Setting 2: `current` 2,367, `downstream` 2,909, `asof` 287, `belief_asof` 307,
  `reported` 338, `yesno:holds` 313, `yesno:erroneous` 128, `yesno:changed` 88. Setting 3 has no `changed` queries.
* In the deposit's final cached answers the study's store and its symbolic replay agree on every one of these queries (0 gaps;
  the study reports 18 of 6,737 Setting 2 answers differing before its fix).
* palimem's agreement with the gold equals the study store's to the query (93.3% in Setting 2, 97.8% in Setting 3): the claims, not
  the pipeline, carry the extraction error. That is what the paper's regime analysis measures and is not re-measured here.
* The extracted claims in the deposit contain no `until` or `interval` cue, no negative polarity and no non-empty context (counted
  per dataset in the report), so none of the kernel's known unsupported features is exercised by them.

Raw numbers: [`../bench/s23/results/replay-s23.json`](../bench/s23/results/replay-s23.json).

### 4.1 Coverage gap: one Setting 3 stream, 24 queries, not replayed

`s3_Q106543540` is refused by the kernel: `KernelUnsupported: single-valued attr 'ceo_city': a world holds several values`. Its domain
declares `ceo_city` as a **single-valued derived** attribute computed from the CEO's `work_city`, which is a **multi-valued** derived
set. The study's oracle answers the two `ceo_city` queries (`q7`, `q8`) as `unresolved` with alternatives `[Q36262, None]`; the kernel
cannot represent a derived single-valued key whose world holds several values (the contract has no cardinality for derived attributes,
a gap recorded earlier). The refusal aborts the whole stream, so 24 of 714 Setting 3 queries (3.4%) are not replayed, including 22 that
do not depend on `ceo_city`. Nothing was loosened: the stream is reported as not replayed. A per-key refusal instead of a per-stream
one would recover the 22 and is a design question, not done here.

## 5. The authority-coincidence check (decision S-02)

For every correction and retraction, the relation of its source to its target's, on the extracted claims (what the systems saw) and
on the stream's original reports:

| | Setting 2 (100 streams) claims / original | `s2_strong` (15) | Setting 3 (30) |
|---|---|---|---|
| correction, same source | 628 / 629 | 101 | 24 |
| **correction, different source of the same origin** | **0 / 0** | **0** | **0** |
| correction, different origin | 454 / 455 | 74 | 6 |
| correction with a dangling target | 2 / 0 | 0 | 0 |
| retraction, same source | 1,286 / 1,281 | 188 | 0 |
| retraction, different source (any origin) | 0 / 0 | 0 | **80** |
| retraction of a whole source | 33 / 30 | 4 | 0 |
| retraction with a dangling target | 1 / 0 | 0 | 0 |

* **S-02 holds on Settings 2 and 3.** There is no correction by a different source of the same origin as its target in any dataset,
  so origin-based authority (the compat profile) and source-based authority (the product default) coincide on the paper's A-SELF rule
  there too, as on Setting 1 (0 of 5,505). The premise "a source is its own origin" is false in these data as well; the conclusion
  does not depend on it.
* **Setting 3's 80 retractions are all `wd_editors` retracting other sources' claims** (60 of them unreferenced Wikidata claims):
  by construction cross-source. The compat profile accepts them, as the paper does. Under the **product default** (authority of the
  target's own source) every one would land as `allege` with no effect unless the host grants `wd_editors` an authority rule for
  those keys. That is not a bug (the design wants exactly that), but it is a real consequence for ingesting editorial retractions
  from a third party: the grant must be configured, which the zero-config path does not do.
* Setting 2 has 33 whole-source retractions in the claims. The contract has no source-scope withdraw; the compat profile carries
  them as a side table (decision pending on whether a source-scope withdraw belongs in the product contract).

## 6. What this shows, and what it does not

**Shows:** admission, kernel, store, revision, the policy-free query path and the v1 adapter give the study store's answers on
natural-language-extracted claims, on both backends, on 100 Setting 2 streams, 15 stronger-backbone streams and 29 of 30 Wikidata
streams (8,421 queries per backend in all, 0 disagreements), including whole-source retractions, cross-origin corrections, derived keys and
bitemporal queries; and the S-02 premise holds on the data where real corrections and shared origins occur.

**Does not show:**

* Nothing about extraction: the claims were extracted once, by the study's extractor, and are replayed as given. The cheap-model
  extractor measurements are in [`eval/EXTRACTION_RESULTS.md`](eval/EXTRACTION_RESULTS.md).
* Not the paper's regime results or its H1; those need the study's adjudicator baselines.
* Not provenance parity (not compared here; Setting 1 has it, [`PERFORMANCE.md`](PERFORMANCE.md) and [`PIPELINE.md`](PIPELINE.md)).
* Not a load or scale result.
* One stream of 30 in Setting 3 is not replayed (section 4.1).
* The queries are the study's, one author's, on streams generated by the study's own generator (Setting 2) or assembled by its
  own mapping from Wikidata (Setting 3); agreement with the study's store is a conformance statement, not an independent
  validation of the semantics.

## 7. How to run it

```bash
python -m harness.studydata fetch --zip ~/Downloads/palimpsest-v1.0.zip   # or without --zip to download (~37 MB), hash-checked
python -m harness.studydata verify                                        # all 565 files against the pinned manifest
python -m harness.replay_s23 --dataset all --out report.json             # both backends, about 3 minutes
python -m harness.replay_s23 --stride 10 --backends memory               # the bounded CI check
python -m harness.replay_s23 --dataset s2 --limit 3 --backends memory --inject-bug mutate-answer   # must exit 1
```

Exit codes: 0 all agree, 1 disagreement, 2 setup error. CI runs the bounded form on every push (every 10th stream, in-memory
backend), the self-test, and `tests/test_replay_s23.py`; the nightly schedule runs everything on both backends.
