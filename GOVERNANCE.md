# Governance

palimem is in an early, single-maintainer phase. This document says how decisions are made now, and how that is expected to change.

## Roles

- **Maintainer** (currently @chleiva): final say on merges, releases, RFC outcomes and decision records. Holds the repository, PyPI and npm accounts.
- **Contributor**: anyone who opens an issue, RFC discussion or pull request.
- **Reviewer**: a contributor the maintainer asks for a specific review (for example an external reviewer of the G0 contracts, which the plan calls for).

More maintainers will be added by the existing maintainer(s) after sustained, high-quality contribution. This document will be revised when there is more than one.

## How decisions are made

| Kind | Where recorded | Who decides | Process |
|---|---|---|---|
| Bug fix, docs, refactor, new fixture that current behaviour passes | PR | maintainer | normal review; the CI gates must pass |
| Ambiguity in the design or semantics | `docs/decisions/S-NN.md` | maintainer | a record states context, options, recommendation, effect on the compat profile; status moves `proposed` to `accepted`, `amended` or `rejected` with a date |
| Contract change (types, schemas, status/decision values, defaults, profiles, backend interface, fixture expectations) | `docs/rfcs/NNNN-*.md`, plus `docs/CONTRACT_PENDING.md` until G0 freezes | maintainer, after discussion | RFC process in `docs/VERSIONING.md` section 10 |
| Release | `CHANGELOG.md`, GitHub release | maintainer | checklist in `docs/RELEASING.md` |
| Security report | private advisory | maintainer | `SECURITY.md` |

## Gates are not negotiable by default

The differential gate (store versus symbolic replay versus frozen gold) and the conformance suite decide what may merge. A maintainer cannot waive a failing gate to ship a feature. A gate may be changed only through an RFC that explains why the gate, not the code, is wrong, and a benchmark number never changes without a dated note in the study's pre-registration addendum.

## Scope

The project non-goals hold: no weight updates, no episodic or procedural memory, no multi-tenant or distributed deployment in v1, no claim of truth. Proposals outside them are welcome as RFCs but are not accepted by default.

## Disagreement

Discuss on the issue or RFC. If there is no consensus, the maintainer decides and writes the reasoning into the record, including the strongest objection.

## Changes to this document

By pull request, approved by the maintainer; an RFC is not required.
