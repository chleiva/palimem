# RFC NNNN: <short title>

- **Status:** draft | discussion | final-comment | accepted | rejected | withdrawn | implemented
- **Author(s):**
- **Created:** YYYY-MM-DD
- **Final comment period ends:** (set when FCP starts)
- **Target release:** (e.g. 0.3.0, or "next major")
- **Supersedes / superseded by:**
- **Related decision records / issues:** (e.g. docs/decisions/S-02.md, #12)

## Summary

One paragraph: what changes and why it matters.

## Motivation

What problem does this solve? Which gate, concern or measured result does it relate to? Link evidence (benchmark numbers, fixtures, an incident), not opinions.

## Design

Normative description. Types and signatures where relevant, using the field names of the contract. State defaults explicitly.

## Compatibility

- **Contract impact:** none | minor | breaking (see `docs/VERSIONING.md` section 3).
- **Semantics impact:** does the kernel conclude anything differently from the same admitted evidence?
- **Store-format impact and migration:**
- **Backend-interface impact:** (required operation / optional capability / none)
- **Compat profile `revise-stream-v1` and gate G1:** effect, and how 0 disagreements is preserved.
- **Benchmark numbers that move:** and the pre-registration addendum entry to write.

## Conformance fixtures

Fixtures added or changed (id, one-line expected behaviour). Say which are negative tests. For a capability-dependent fixture, list `requires`.

## Security and privacy

Does this widen the attack surface or change what is stored about people? Reference `docs/THREAT_MODEL.md`. Trust-boundary effects (who can set which field).

## Alternatives considered

At least two, with why they were not chosen. Include "do nothing".

## Drawbacks and risks

## Unresolved questions

What must be decided before FCP, and what may be deferred.

## Decision

(Filled in at acceptance: who decided, date, reasoning, any amendments.)
