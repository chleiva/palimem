"""Exact provenance through the full pipeline (T-B4 wired into Lane M, decision S-12).

The kernel's per-candidate subset-minimal environments are stored in belief versions (base keys directly, derived keys
as joins of the stored base supports) and served as ``Resolved.provenance``; the explanation budget cuts the response and
never a decision; ``explain`` reads the stored supports (full closure) or recomputes (a depth limit); the v1 profile's
flat provenance is a separate projection. Every test runs on both backends.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from palimem.compat import flat_provenance_v1, key_provenance_v1
from palimem.kernel import day_of
from palimem.memory import Memory, cut_explanation
from palimem.types import (
    BeliefOfProp,
    Cue,
    Decision,
    ExplainMode,
    ExplainQuery,
    ExplanationState,
    KernelStatus,
    Key,
    Origin,
    Query,
    Report,
    Resolved,
    ValueProp,
)
from tests._pipeline_helpers import (
    Clock,
    assertion,
    current,
    make_backend,
    src,
    toy_memory,
)

BACKENDS = ["memory", "sqlite"]


@pytest.fixture(params=BACKENDS)
def mem(request: pytest.FixtureRequest) -> Memory:
    return toy_memory(make_backend(request.param, Clock()))


def rid(res: object) -> str:
    entry = res.entry  # type: ignore[attr-defined]
    assert entry is not None and entry.report.id is not None
    return str(entry.report.id)


def envs(ans: object) -> list[list[str]]:
    assert isinstance(ans, Resolved)
    return sorted(sorted(s.environment) for s in ans.provenance)


# --------------------------------------------------------------------------- stored supports


def test_a_base_belief_stores_its_support_and_the_answer_serves_it(mem: Memory) -> None:
    r1 = rid(mem.append(assertion("alex", "employer", "veltran", source="press")))
    ans = current(mem, "alex", "employer")
    assert isinstance(ans, Resolved) and ans.kernel_status is KernelStatus.ESTABLISHED
    assert envs(ans) == [[r1]]
    assert ans.justified.segment.support and not ans.justified.ref.startswith("virtual:")


def test_a_derived_belief_stores_the_join_of_the_base_supports(mem: Memory) -> None:
    r1 = rid(mem.append(assertion("alex", "employer", "veltran", source="press")))
    r2 = rid(mem.append(assertion("veltran", "hq_city", "tessaly", source="registry")))
    ans = current(mem, "alex", "work_city")
    assert envs(ans) == [sorted([r1, r2])]  # over BASE reports: derivation pins are explanatory, not evidence


def test_stored_supports_equal_the_replay_for_base_and_derived_keys(mem: Memory) -> None:
    mem.append(assertion("alex", "employer", "veltran", source="press"))
    mem.append(assertion("veltran", "hq_city", "tessaly", source="registry"))
    mem.append(assertion("alex", "employer", "acme", source="wire", cue=Cue.CHANGE))
    for ent, attr in (("alex", "employer"), ("veltran", "hq_city"), ("alex", "work_city")):
        key = Key(entity=ent, attr=attr)
        ans = current(mem, ent, attr)
        assert isinstance(ans, Resolved)
        j = mem.justification(key)
        day = day_of(ans.justified.segment.valid_from) if ans.justified.segment.valid_from else 0
        assert dict(ans.justified.segment.support) == dict(j.segment_at(day).support)  # type: ignore[union-attr]


def test_withdrawing_the_leaf_removes_the_derived_support(mem: Memory) -> None:
    r1 = rid(mem.append(assertion("alex", "employer", "veltran", source="press")))
    mem.append(assertion("veltran", "hq_city", "tessaly", source="registry"))
    mem.withdraw(r1, source=src("press"), actor="connector:press")
    ans = current(mem, "alex", "work_city")
    assert isinstance(ans, Resolved) and ans.kernel_status is KernelStatus.UNKNOWN
    assert ans.provenance == () and not ans.justified.segment.support


def test_the_pre_withdrawal_supports_are_still_served_under_belief_as_of(mem: Memory) -> None:
    r1 = rid(mem.append(assertion("alex", "employer", "veltran", source="press")))
    r2 = rid(mem.append(assertion("veltran", "hq_city", "tessaly", source="registry")))
    mem.withdraw(r1, source=src("press"), actor="connector:press")
    old = current(mem, "alex", "work_city", belief_as_of=2)
    assert envs(old) == [sorted([r1, r2])]


def test_a_stored_belief_does_not_pretend_to_carry_per_report_values(mem: Memory) -> None:
    """The oracle's flat rule needs (id, value) pairs, which only the audit path has: the stored provider refuses."""
    from palimem.engine.pipeline import Resolver, _BeliefProvider

    prov = _BeliefProvider(Resolver(mem.backend))
    with pytest.raises(NotImplementedError):
        prov.reports(Key(entity="alex", attr="employer"))


# --------------------------------------------------------------------------- unresolved keys: several candidates


def _unresolved(mem: Memory) -> tuple[str, str]:
    a = rid(mem.append(assertion("alex", "employer", "veltran", source="press")))
    b = rid(mem.append(assertion("alex", "employer", "acme", source="wire")))
    return a, b


def test_an_unresolved_key_lists_the_environment_of_every_alternative(mem: Memory) -> None:
    a, b = _unresolved(mem)
    ans = current(mem, "alex", "employer")
    assert isinstance(ans, Resolved) and ans.kernel_status is KernelStatus.UNRESOLVED
    assert envs(ans) == sorted([[a], [b]])


def test_the_explanation_budget_cuts_provenance_and_the_view_but_never_a_decision(mem: Memory) -> None:
    _unresolved(mem)
    full = current(mem, "alex", "employer")
    cut = current(mem, "alex", "employer", explanation_budget=1)
    assert isinstance(full, Resolved) and isinstance(cut, Resolved)
    assert full.explanation is ExplanationState.COMPLETE and cut.explanation is ExplanationState.TRUNCATED
    assert len(full.provenance) == 2 and len(cut.provenance) == 1
    assert sum(len(v) for v in cut.justified.segment.support.values()) == 1  # the embedded view is cut too
    for f in ("kernel_status", "decision", "assertion", "alternatives", "policy"):
        assert getattr(cut, f) == getattr(full, f)
    assert cut.justified.segment.established == full.justified.segment.established
    assert cut.justified.segment.alternatives == full.justified.segment.alternatives


def test_a_budget_that_covers_everything_changes_nothing(mem: Memory) -> None:
    _unresolved(mem)
    full = current(mem, "alex", "employer")
    assert current(mem, "alex", "employer", explanation_budget=2) == full
    assert current(mem, "alex", "employer", explanation_budget=50) == full


def test_a_zero_budget_empties_the_explanation_and_says_so(mem: Memory) -> None:
    _unresolved(mem)
    zero = current(mem, "alex", "employer", explanation_budget=0)
    assert isinstance(zero, Resolved)
    assert zero.provenance == () and not zero.justified.segment.support
    assert zero.explanation is ExplanationState.TRUNCATED and zero.kernel_status is KernelStatus.UNRESOLVED


def test_truncation_never_changes_the_decision_of_a_committing_policy() -> None:
    """The cut is applied after ``decide`` on the full supports: a policy that reads the supports (the single-origin
    marking, the origin-group score) decides identically with and without a budget."""
    from palimem.policy import RECENCY

    m = toy_memory(make_backend("memory", Clock()), policy=RECENCY, self_update=True)
    m.append(assertion("alex", "employer", "veltran", source="press"))
    m.append(assertion("alex", "employer", "acme", source="wire"))
    full = current(m, "alex", "employer")
    for budget in (0, 1, 2):
        cut = current(m, "alex", "employer", explanation_budget=budget)
        assert isinstance(full, Resolved) and isinstance(cut, Resolved)
        assert (cut.kernel_status, cut.decision, cut.assertion) == (full.kernel_status, full.decision, full.assertion)


def test_cut_explanation_is_the_identity_when_nothing_is_cut(mem: Memory) -> None:
    mem.append(assertion("alex", "employer", "veltran", source="press"))
    ans = current(mem, "alex", "employer")
    assert isinstance(ans, Resolved)
    assert cut_explanation(ans, 10) is ans
    assert replace(ans) == ans


# --------------------------------------------------------------------------- explain


def _valid_at() -> datetime:
    return datetime(2026, 6, 1, tzinfo=UTC)


def test_explain_full_closure_lists_the_base_reports_and_is_complete(mem: Memory) -> None:
    r1 = rid(mem.append(assertion("alex", "employer", "veltran", source="press")))
    r2 = rid(mem.append(assertion("veltran", "hq_city", "tessaly", source="registry")))
    ex = mem.explain(ExplainQuery(key=Key(entity="alex", attr="work_city")))
    assert ex.state is ExplanationState.COMPLETE and ex.depth is None
    assert [sorted(s.environment) for s in ex.environments] == [sorted([r1, r2])]


def test_explain_with_a_depth_limit_is_recomputed_and_marked_truncated(mem: Memory) -> None:
    mem.append(assertion("alex", "employer", "veltran", source="press"))
    mem.append(assertion("veltran", "hq_city", "tessaly", source="registry"))
    ex = mem.explain(ExplainQuery(key=Key(entity="alex", attr="work_city"), depth=1))
    assert ex.state is ExplanationState.TRUNCATED and ex.depth == 1


def test_explain_mode_one_is_the_lexicographically_least_environment(mem: Memory) -> None:
    a, b = _unresolved(mem)
    allx = mem.explain(ExplainQuery(key=Key(entity="alex", attr="employer"), mode=ExplainMode.ALL))
    one = mem.explain(ExplainQuery(key=Key(entity="alex", attr="employer"), mode=ExplainMode.ONE))
    assert len(allx.environments) == 2 and len(one.environments) == 1
    assert list(one.environments[0].environment) == sorted(
        min(allx.environments, key=lambda s: sorted(s.environment)).environment
    )
    assert one.environments[0].environment == (min(a, b),)


def test_explain_of_an_unknown_key_has_no_environments(mem: Memory) -> None:
    ex = mem.explain(ExplainQuery(key=Key(entity="alex", attr="employer")))
    assert ex.environments == () and ex.state is ExplanationState.COMPLETE


# --------------------------------------------------------------------------- attributions


def _attributed(source: str, group: str, value: str = "acme") -> Report:
    return Report(
        key=Key(entity="bob", attr="employer"), cue=Cue.ASSERT,
        proposition=BeliefOfProp(holder="alice", proposition=ValueProp(value=value)), source=src(source),
        origin=Origin.ATTRIBUTED, origin_group=group, actor=f"connector:{source}",
    )


def test_attribution_support_is_one_environment_per_origin_group(mem: Memory) -> None:
    r1 = rid(mem.append(_attributed("press", "g_press")))
    r2 = rid(mem.append(_attributed("wire", "g_wire")))
    r3 = rid(mem.append(_attributed("press2", "g_press")))  # a copy: the same origin group
    ans = current(mem, "bob", "employer")
    assert isinstance(ans, Resolved) and ans.kernel_status is KernelStatus.ESTABLISHED
    assert envs(ans) == sorted([[r1], [r2]]) and r3 not in {x for e in envs(ans) for x in e}
    # an attribution is never committed as a value: the policy asks (attribution safety)
    assert ans.decision is Decision.ASK and ans.assertion is None


# --------------------------------------------------------------------------- the v1 profile projection


def test_the_profile_projection_is_the_oracles_flat_set_not_the_environments(mem: Memory) -> None:
    r1 = rid(mem.append(assertion("alex", "employer", "veltran", source="press")))
    r2 = rid(mem.append(assertion("veltran", "hq_city", "tessaly", source="registry")))
    lsn = mem.backend.head().lsn
    assert flat_provenance_v1(mem, Key(entity="alex", attr="work_city"), 10, lsn) == {r1, r2}
    assert flat_provenance_v1(mem, Key(entity="alex", attr="employer"), 10, lsn) == {r1}
    assert key_provenance_v1(mem, Key(entity="alex", attr="employer"), lsn) == {r1}


def test_the_flat_set_lists_every_alternative_of_an_unresolved_key_the_environments_list_each_apart(mem: Memory) -> None:
    a, b = _unresolved(mem)
    lsn = mem.backend.head().lsn
    assert flat_provenance_v1(mem, Key(entity="alex", attr="employer"), 10, lsn) == {a, b}


def test_query_object_accepts_the_budget_field() -> None:
    q = Query(key=Key(entity="alex", attr="employer"), explanation_budget=3)
    assert q.explanation_budget == 3
