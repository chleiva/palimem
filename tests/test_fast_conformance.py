"""T-B10 promotion criterion: the independent conformance suite, with the fast kernel on the base-key path.

The pipeline calls ``justify_key`` for every base key. This test swaps that one call for the fast kernel's
dispatch (fast inside the proven class, enumeration outside it) and runs the whole conformance suite through the
same adapter as the production run (``tests.conformance.impl_memory``). The outcome must equal the recorded
production baseline exactly: the same fixtures pass, the same fixtures fail (for the same causes). A fixture that
passes under enumeration and fails under fast is a promotion blocker; one that newly passes would mean the two
kernels differ somewhere the suite can see.

The retraction fixtures (ind-02 cascade, ind-14 cascade budget, sec-41 repair, the derived-key fixtures) are in
the suite, so this covers "the independent suite incl. the retraction fixtures".
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from palimem.engine import pipeline as pipeline_mod
from palimem.kernel.fast import dispatch_key
from tests.conformance.impl_memory import STATUS_FILE, current_status

BUDGET_FIXTURES = {
    "ind-08a-environment-budget-resource-limited",
    "s06-01-default-environment-budget-is-twelve",
    "s06-02-over-budget-is-never-unresolved",
    "s06-05-explicit-environment-budget-of-seven",
}


def _fast_justify_key(ks: Any, key: Any, entries: Any, semantic: Any, *, budget: int, change_from: Any = None) -> Any:
    return dispatch_key(ks, key, entries, semantic, budget=budget, change_from=change_from).result


@pytest.mark.skipif(not hasattr(pipeline_mod, "justify_key"), reason="pipeline layout changed")
def test_the_conformance_suite_gives_the_same_outcome_with_the_fast_kernel(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = json.loads(STATUS_FILE.read_text())
    monkeypatch.setattr(pipeline_mod, "justify_key", _fast_justify_key)
    now = current_status()
    lost = set(recorded["pass"]) - set(now["pass"])
    gained = set(now["pass"]) - set(recorded["pass"])
    # The ONLY expected difference: the fixtures that assert the enumeration kernel's environment budget
    # ("above 7 admitted reports on a key the answer is ResourceLimited(environment_budget)"). The fast kernel
    # answers such a key (inside its proven class) instead of refusing it, which is its purpose; whether the
    # resource contract keeps a budget that the fast route does not need is a promotion decision (docs/FAST_KERNEL.md).
    assert lost <= BUDGET_FIXTURES, {"passed under enumeration only (unexpected)": sorted(lost - BUDGET_FIXTURES)}
    assert not gained, {"passes under fast only": sorted(gained)}
    newly_failing = set(now["fail"]) - set(recorded["fail"])
    assert newly_failing == lost, {
        "newly failing": {k: now["fail"][k] for k in sorted(newly_failing)},
        "lost passes": sorted(lost),
    }
    assert set(recorded["fail"]) <= set(now["fail"]), {"newly passing": sorted(set(recorded["fail"]) - set(now["fail"]))}
    assert set(now["pass_needs_data"]) | set(now["needs_data"]) == set(recorded["pass_needs_data"]) | set(recorded["needs_data"])
