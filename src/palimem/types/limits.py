"""Contract constants (decided limits)."""

from __future__ import annotations

DEFAULT_ENVIRONMENT_BUDGET = 12
"""Default per-key environment budget (S-06, decided; raised from 7 to 12 on the evidence below).

The budget started at 7, the envelope the study generator produces and the differential CI validated. S-06
allowed raising it to 12 only once the enumeration kernel's answers at 8 to 12 admitted reports on a key had
been cross-checked against the brute-force global oracle (``revise_stream.oracle_v2``) on FRESHLY GENERATED
streams. ``harness.budget_crosscheck`` did that: 1,050 fresh streams (seeds 7,700,000 and up), 80,856 query
comparisons over P0c and P0cSU and every slot type, **0 disagreements**, with 928 / 850 / 714 / 537 / 317 keys
at n = 8 / 9 / 10 / 11 / 12 (docs/BUDGET_CROSSCHECK.md, bench/budget/results/). Above the budget a key answers
``ResourceLimited(environment_budget)``, never a silent degradation. Explicit budgets (``Memory(budget=...)``,
``justify_key(budget=...)``) are unchanged."""

MAX_BELIEF_NESTING = 1
"""Maximum nesting of ``belief_of`` (S-11, decided). ``belief_of(h, P)`` has depth 1 when P is
plain; deeper nesting is *reserved* in the JSON schema so lifting the limit later is non-breaking."""
