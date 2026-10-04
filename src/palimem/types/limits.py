"""Contract constants (decided limits)."""

from __future__ import annotations

DEFAULT_ENVIRONMENT_BUDGET = 7
"""Default per-key environment budget (S-06, decided): the validated envelope of the study
generator (keys cap at 7 reports). Above it a key answers ``ResourceLimited(environment_budget)``;
the design's earlier default of 12 was beyond anything validated."""

MAX_BELIEF_NESTING = 1
"""Maximum nesting of ``belief_of`` (S-11, decided). ``belief_of(h, P)`` has depth 1 when P is
plain; deeper nesting is *reserved* in the JSON schema so lifting the limit later is non-breaking."""
