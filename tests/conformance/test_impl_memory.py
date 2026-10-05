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
    # a check recorded as passing with the frozen data must pass where the data exists and may only be *skipped for
    # missing data* where it does not (CI's plain test job): never failed, never silently dropped
    must_pass = set(recorded["pass"]) | set(recorded["pass_needs_data"])
    may_skip_for_data = set(recorded["pass_needs_data"])
    regressions = sorted(must_pass - set(now["pass"]) - set(now["pass_needs_data"]) - (may_skip_for_data & set(now["needs_data"])))
    unlisted = sorted(set(now["fail"]) - set(recorded["fail"]))
    assert not regressions, f"fixtures that used to pass no longer do: {regressions}"
    assert not unlisted, f"new failing fixtures (not on the known list): { {k: now['fail'][k] for k in unlisted} }"


def test_a_check_without_its_data_is_skipped_not_failed_and_the_baseline_is_stable() -> None:
    """The same baseline must hold on a machine without the frozen data: the data-dependent checks move from
    ``pass_needs_data`` to ``needs_data`` and nothing else changes."""
    recorded = json.loads(STATUS_FILE.read_text())
    now = current_status()
    assert set(now["needs_data"]) | set(now["pass_needs_data"]) == set(recorded["pass_needs_data"])
    assert not (set(now["needs_data"]) & set(now["fail"]))


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
        "compat-02-per-slot-type-closed-world", "sec-27-sql-metacharacters-round-trip",
        "sec-30-tombstone-leaks-no-key-text", "s10-05-shared-ancestor-rule-refused", "s12-03-rule-depth-nine-refused",
    }
    assert core <= set(now["pass"]), sorted(core - set(now["pass"]))
    # the data-dependent check passes where the frozen data exists and is skipped (never failed) where it does not
    assert "compat-01-authority-coincide" in set(now["pass_needs_data"]) | set(now["needs_data"])
