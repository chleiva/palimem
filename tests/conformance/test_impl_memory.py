"""The conformance suite against the ``Memory`` adapter, as a ratchet.

``memory_status.json`` records which fixtures pass today and the cause of each known failure. The test fails when a
fixture that passed stops passing (a regression) or when a fixture fails that is not on the known list (a new failure).
Fixtures are never edited to make them pass: a failure leaves the list only by the implementation improving. When it
does, regenerate with ``python -m tests.conformance.impl_memory`` and review the diff.
"""

from __future__ import annotations

import json

from .impl_memory import STATUS_FILE, current_status


def test_no_conformance_regression_and_no_unlisted_failure() -> None:
    recorded = json.loads(STATUS_FILE.read_text())
    now = current_status()
    regressions = sorted(set(recorded["pass"]) - set(now["pass"]))
    unlisted = sorted(set(now["fail"]) - set(recorded["fail"]))
    assert not regressions, f"fixtures that used to pass no longer do: {regressions}"
    assert not unlisted, f"new failing fixtures (not on the known list): { {k: now['fail'][k] for k in unlisted} }"


def test_every_known_failure_names_its_cause() -> None:
    recorded = json.loads(STATUS_FILE.read_text())
    assert recorded["fail"], "the baseline records the failures it tolerates"
    assert all(cause for cause in recorded["fail"].values())


def test_the_adapter_passes_a_meaningful_core_of_the_suite() -> None:
    now = current_status()
    core = {
        "ind-04-retrospective-correction-two-axes", "ind-06-agent-repeats-own-hypothesis",
        "ind-08a-environment-budget-resource-limited", "ind-14-cascade-budget-stale-c",
        "s06-01-default-environment-budget-is-seven", "s06-02-over-budget-is-never-unresolved",
        "compat-01-authority-coincide", "compat-02-per-slot-type-closed-world", "sec-27-sql-metacharacters-round-trip",
        "sec-30-tombstone-leaks-no-key-text", "s10-05-shared-ancestor-rule-refused", "s12-03-rule-depth-nine-refused",
    }
    assert core <= set(now["pass"]), sorted(core - set(now["pass"]))
