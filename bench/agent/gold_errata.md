# Gold errata and adjudications (RETRACT-ACT)

Rule: the registered gold is never edited silently. Every change is recorded here with date, item, old and new value, reason and adjudicator, and is applied as a **versioned overlay** (the registered scenario files and hashes stay as registered; results are reported under both the registered gold and the corrected gold). Adjudicator for every entry below: **the author**, 2026-10-05, after the second annotation (`docs/eval/ANNOTATION.md`).

## Errata (change the gold)

| date | item | old | new | reason | adjudicator |
|---|---|---|---|---|---|
| 2026-10-05 | RA-026.d1 | `act london` | `ask` | Under the study's gap semantics the later report does not establish that London remained valid through day 300; that date is unresolved, so the task calls for clarification (ruling 18 retained). | author |

## Adjudications that keep the gold

| date | item | gold | annotator | decision | reason | adjudicator |
|---|---|---|---|---|---|---|
| 2026-10-05 | RA-006.d1 | `ask` | `act fr` | keep gold | The kernel is reliability-neutral, so the blog's cross-origin correction leaves a dispute rather than replacing the registry's value or automatically losing to it. Ask the registry or the user to resolve it; ruling 1 retained. | author |
| 2026-10-05 | RA-023.d1 | `act porto` (default P0cSU) | `ask` | keep gold; keep P0cSU as the benchmark default | A source's later report supersedes its own earlier value under this profile even without a change cue. This assumption is documented explicitly; `self_update_off` (gold `ask`) is retained as the separate condition testing the study's literal reading. | author |

## Provenance of the second annotation

Self-report of the executing model, relayed by the author: it produced the annotations after reading the supplied pack and did not consult gold, mapping files or benchmark documents before answering. This is a **model self-report, not independent certification of blindness**. The submission is classified as a **model third opinion**, and the agreement result (27 of 29, kappa 0.866) is qualified accordingly. No independent human annotation exists.
