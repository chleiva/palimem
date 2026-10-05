# Second annotation of RETRACT-ACT gold (blind pack and agreement tooling)

Status: tooling ready, **no annotations collected or committed**. Authority: ruling 22 of [`RULINGS-2026-10-05.md`](../decisions/RULINGS-2026-10-05.md).

The registered RETRACT-ACT gold was written by one author, with LLM help ([`AGENT_BENCHMARK.md`](AGENT_BENCHMARK.md), threats to validity). Every agent-level number rests on it. The second annotation is the author, **blind to every system output and to the existing gold**, labelling the same decision points. Agreement is reported as Cohen's kappa and every disagreement is adjudicated in writing.

## What is annotated

29 decision points, shown one at a time with only what was known at that moment:

- all **25 test** decision points (20 test scenarios), and
- every decision point of **RA-006** (test, already in the 25), **RA-007** (dev, 1 point) and **RA-026** (dev, 3 points).

RA-007 is annotated *without* a profile: the author answers what the product should do, and the report then compares against both registered gold profiles (`default` and `authority_source`). RA-006 and RA-007 are the two items whose golds contradict each other on one situation (see ruling 1); RA-026.d1 is the gold-erratum candidate (ruling 18).

Several points belong to one story (RA-026 has three, RA-027 two). Each is shown with its own timeline only, in a seeded random order, and is to be answered independently.

## How to run it

```
python bench/agent/annotation/make_pack.py            # writes bench/agent/annotation/out/ (gitignored)
python bench/agent/annotation/make_pack.py --check    # verifies the pack is blind and offline
open bench/agent/annotation/out/annotation-pack.html  # macOS; any browser, no network needed
```

In the form choose one action per item (`act`, `ask`, `abstain`, `revalidate`), type the value for `act` and `revalidate`, and add a one-line reason whenever you hesitate. **Save** downloads `annotations-<hash>.json`: that file is the record (the browser also keeps a convenience copy that can be lost). **Load** resumes a partial session. Do not open `out/private/mapping.json`, the benchmark documents or any result file until every item is saved.

Then, and only then:

```
python bench/agent/annotation/kappa.py --annotations ~/Downloads/annotations-XXXXXXXX.json \
    --mapping bench/agent/annotation/out/private/mapping.json
python bench/agent/annotation/kappa.py --annotations ... --profile authority_source   # RA-007 under the other gold
python bench/agent/annotation/kappa.py --selftest                                    # validates the statistics
```

This writes `bench/agent/annotation/out/kappa-report.json` and `.md` (gitignored: they contain your annotations and the gold, so commit them deliberately, after adjudication).

## What the report contains

- Cohen's kappa over the four actions, raw agreement with a Wilson 95% interval, and a seeded bootstrap 95% interval for kappa (resamples with an undefined kappa are skipped and counted), for **all 29 points**, the **25 test points** and the **RA-006 / RA-007 / RA-026 items** separately.
- The gold-by-annotator confusion matrix and agreement on the value where both chose `act` or `revalidate`.
- Every disagreement (a different action, or the same `act` with a different value), with the scenario narrative, the registered gold and rationale, the gold under any other profile, your answer and reason, and an empty **adjudication** line.
- A gold-errata draft table.

Read it with these cautions: 29 items give wide intervals (report the interval, not only the point estimate); kappa is undefined when both raters use a single label; the three ruling items are five points in total and say nothing statistical on their own.

## Adjudicating disagreements

1. For each disagreement write a **reason** in the report (what evidence decides it and why). A disagreement without a written reason stays open.
2. **Never edit the gold silently.** If the adjudication changes a gold, record it in `bench/agent/gold_errata.md` (create the file at the first erratum) with date, item, old value, new value, reason and adjudicator:

   | date | item | old | new | reason | adjudicator |
   |---|---|---|---|---|---|

3. An erratum creates a **new gold version**; registered runs are not overwritten. Re-score the registered runs against the corrected gold as a labelled, separate analysis next to the registered numbers (see the "Product changes since registration" notes in [`AGENT_BENCHMARK_RESULTS.md`](AGENT_BENCHMARK_RESULTS.md) and [`AGENT_BENCHMARK_LLM_RESULTS.md`](AGENT_BENCHMARK_LLM_RESULTS.md)).
4. **A second model family may only be a third opinion**, never the second annotator: it may be consulted on a disagreement, its answer is recorded as such, and it never counts toward the kappa.

## How blindness is enforced and tested

The generator builds each item from a **whitelist** of scenario fields (sources and classes, attributes and derivations, reports, executed actions, and the decision point's task, mode, kind, day and plan fields). It never reads the gold, the gold-by-profile, the resolver hints, the title, slug, description, category, tags, stakes tier or any cost weight, and it never reads a system output or result file. Scenario and decision-point ids are replaced by opaque ids (`I-xxxxxx`); the mapping is a separate file outside the HTML.

`make_pack.py --check` and `tests/test_annotation_pack.py` verify, on the generated HTML and on every rendered item: no forbidden field name appears; no scenario or decision-point id, title, slug, description, gold rationale, resolver hint or tag/category value appears; the page has no external reference. The tests also poison every gold, title, tag and resolver field in a copy of the scenarios and assert the pack is byte-identical, which proves the generator reads none of them, and they plant leaks to show the check can fail. The form's JavaScript is syntax-checked when `node` is available.

## Limits that remain

- The annotator is the scenario author: blind to the gold and to every output, but not to their own design. Independence is partial and the report says so.
- The wording of the timeline (the narrative) is generated from the typed reports in plain language; it adds nothing the typed data lacks, but it is a rendering, not the agents' memory text.
- Shown to the annotator: the reliability class of each source and its origin group, because the agents saw them too. The protocol text in the form mirrors the one given to the LLM agents.
- The kappa and Wilson implementations are checked against hand-computed values (`--selftest`, and the tests): a 2x2 case with kappa 0.4, perfect agreement, cyclic-shift adversarial cases (kappa -1/3 and -1/2), an undefined case, and Wilson intervals for 0/10, 7/10 and 10/10.
