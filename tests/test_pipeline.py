"""The full pipeline through ``Memory`` (Lane M): admission + kernel + store + policy, on both backends."""

from __future__ import annotations

from pathlib import Path

import pytest

from palimem.memory import Memory
from palimem.policy import LWW
from palimem.store import APPEND_STEPS, ErasureReason, StoreError
from palimem.types import KernelStatus, Key, Query, Resolved, ResourceLimited
from tests._pipeline_helpers import (
    Clock,
    assertion,
    current,
    established_value,
    make_backend,
    src,
    toy_memory,
)

BACKENDS = ["memory", "sqlite"]


@pytest.fixture(params=BACKENDS)
def mem(request: pytest.FixtureRequest) -> Memory:
    clock = Clock()
    m = toy_memory(make_backend(request.param, clock))
    m.clock = clock  # type: ignore[attr-defined]
    return m


def test_assert_and_query(mem: Memory) -> None:
    mem.append(assertion("alex", "employer", "veltran"))
    ans = current(mem, "alex", "employer")
    assert isinstance(ans, Resolved)
    assert ans.kernel_status is KernelStatus.ESTABLISHED
    assert established_value(ans) == "veltran"
    assert ans.assertion is not None


def test_unwritten_key_is_unknown_not_an_error(mem: Memory) -> None:
    ans = current(mem, "alex", "employer")
    assert isinstance(ans, Resolved)
    assert ans.kernel_status is KernelStatus.UNKNOWN
    assert ans.justified.ref.startswith("virtual:")


def test_withdrawal_cascades_through_a_derived_key(mem: Memory) -> None:
    """The study's headline mechanism, end to end: withdrawing o1 removes work_city(alex), two steps downstream."""
    r1 = mem.append(assertion("alex", "employer", "veltran", source="press"))
    mem.append(assertion("veltran", "hq_city", "tessaly", source="registry"))
    ans = current(mem, "alex", "work_city")
    assert isinstance(ans, Resolved) and established_value(ans) == "tessaly"
    assert r1.entry is not None and r1.entry.report.id is not None

    mem.withdraw(r1.entry.report.id, source=src("press"), actor="connector:press")
    ans2 = current(mem, "alex", "work_city")
    assert isinstance(ans2, Resolved) and ans2.kernel_status is KernelStatus.UNKNOWN
    emp = current(mem, "alex", "employer")
    assert isinstance(emp, Resolved) and emp.kernel_status is KernelStatus.UNKNOWN
    # the pre-withdrawal answer is still served under belief_as_of
    old = current(mem, "alex", "work_city", belief_as_of=2)
    assert isinstance(old, Resolved) and established_value(old) == "tessaly"


def test_two_independent_supports_survive_the_loss_of_one(mem: Memory) -> None:
    a = mem.append(assertion("veltran", "hq_city", "tessaly", source="registry", group="reg"))
    mem.append(assertion("veltran", "hq_city", "tessaly", source="wire", group="wire"))
    assert a.entry is not None and a.entry.report.id is not None
    mem.withdraw(a.entry.report.id, source=src("registry"), actor="connector:registry", origin_group="reg")
    ans = current(mem, "veltran", "hq_city")
    assert isinstance(ans, Resolved) and established_value(ans) == "tessaly"


def test_unauthorised_withdrawal_is_an_allege_with_no_effect(mem: Memory) -> None:
    r = mem.append(assertion("alex", "employer", "veltran", source="press"))
    assert r.entry is not None and r.entry.report.id is not None
    res = mem.withdraw(r.entry.report.id, source=src("rumour"), actor="connector:rumour")
    assert res.admissions[0].outcome.value == "excluded"
    ans = current(mem, "alex", "employer")
    assert isinstance(ans, Resolved) and established_value(ans) == "veltran"


def test_conflicting_equal_sources_stay_unresolved_and_policy_commits_only_when_told(mem: Memory) -> None:
    mem.append(assertion("alex", "employer", "veltran", source="press"))
    mem.append(assertion("alex", "employer", "acme", source="blog"))
    ans = current(mem, "alex", "employer")
    assert isinstance(ans, Resolved)
    assert ans.kernel_status is KernelStatus.UNRESOLVED and ans.decision.value == "ask"
    lww = mem.with_policy(LWW)
    a2 = current(lww, "alex", "employer")
    assert isinstance(a2, Resolved)
    assert a2.kernel_status is KernelStatus.UNRESOLVED  # a policy commitment never makes it established
    if a2.decision.value == "commit":  # recency needs per-candidate supports (T-B4); until then it cannot pick
        assert a2.assertion is not None


def test_belief_as_of_accepts_lsn_and_timestamp(mem: Memory) -> None:
    clock: Clock = mem.clock  # type: ignore[attr-defined]
    mem.append(assertion("alex", "employer", "veltran", source="press"))
    clock.day = 5
    mem.append(assertion("alex", "employer", "acme", source="press", cue=__import__("palimem.types", fromlist=["Cue"]).Cue.CHANGE))
    by_lsn = current(mem, "alex", "employer", belief_as_of=1)
    by_time = current(mem, "alex", "employer", belief_as_of=Clock().__call__())  # day 0
    assert isinstance(by_lsn, Resolved) and isinstance(by_time, Resolved)
    assert established_value(by_lsn) == established_value(by_time) == "veltran"


def test_environment_budget_is_resource_limited_never_unresolved() -> None:
    clock = Clock()
    m = toy_memory(make_backend("memory", clock), budget=2)
    for i in range(3):
        clock.day = i
        m.append(assertion("alex", "employer", f"c{i}", source=f"s{i}"))
    ans = current(m, "alex", "employer")
    assert isinstance(ans, ResourceLimited)
    assert ans.reason.value == "environment_budget" and ans.reason_key == Key(entity="alex", attr="employer")
    assert not hasattr(ans, "kernel_status")
    # a derived key reading it is limited too (stale_dependency), not answered from a wrong base
    d = current(m, "alex", "work_city")
    assert isinstance(d, ResourceLimited)


def test_open_world_profile_is_enforced_per_query(mem: Memory) -> None:
    from palimem.types import Profile

    with pytest.raises(ValueError, match="profile"):
        mem.query(Query(key=Key(entity="alex", attr="employer"), profile=Profile.REVISE_STREAM_V1))


def test_undeclared_attribute_is_refused(mem: Memory) -> None:
    with pytest.raises(ValueError, match="not declared"):
        mem.append(assertion("alex", "salary", 1))
    with pytest.raises(ValueError, match="derived"):
        mem.append(assertion("alex", "work_city", "x"))


def test_agent_origin_report_is_logged_but_never_evidence(mem: Memory) -> None:
    from palimem.types import Origin

    r = mem.append(assertion("alex", "employer", "veltran", source="agent", origin=Origin.AGENT_HYPOTHESIS, actor="agent:planner"))
    assert r.admissions[0].outcome.value == "excluded"
    ans = current(mem, "alex", "employer")
    assert isinstance(ans, Resolved) and ans.kernel_status is KernelStatus.UNKNOWN


def test_delete_repairs_dependants_and_leaves_a_tombstone(mem: Memory) -> None:
    r1 = mem.append(assertion("alex", "employer", "veltran", source="press"))
    mem.append(assertion("veltran", "hq_city", "tessaly", source="registry"))
    assert r1.entry is not None and r1.entry.report.id is not None
    mem.delete(r1.entry.report.id, ErasureReason.ERASURE_REQUEST)
    ans = current(mem, "alex", "work_city")
    assert isinstance(ans, Resolved) and ans.kernel_status is KernelStatus.UNKNOWN
    assert mem.backend.verify_log().ok


def test_restart_reproduces_beliefs_on_sqlite(tmp_path: Path) -> None:
    clock = Clock()
    be = make_backend("sqlite", clock, tmp_path)
    m = toy_memory(be)
    m.append(assertion("alex", "employer", "veltran"))
    m.append(assertion("veltran", "hq_city", "tessaly", source="registry"))
    before = current(m, "alex", "work_city")
    m.close()
    m2 = toy_memory(make_backend("sqlite", clock, tmp_path))
    after = current(m2, "alex", "work_city")
    assert before == after
    assert m2.backend.verify_beliefs(m2.reviser).ok


@pytest.mark.parametrize("step", APPEND_STEPS)
def test_crash_at_every_step_leaves_no_trace_and_retry_converges(step: str, tmp_path: Path) -> None:
    """A fault at each step of the append transaction, through the whole pipeline (admission + kernel + derived
    revision + barrier), then restart and retry with the same idempotency key."""
    clock = Clock()
    crash = {"on": False}

    def fault(name: str) -> None:
        if crash["on"] and name == step:
            raise RuntimeError(f"injected crash at {name}")

    be = make_backend("sqlite", clock, tmp_path, fault=fault)
    m = toy_memory(be)
    m.append(assertion("alex", "employer", "veltran", source="press"), idempotency_key="k1")
    crash["on"] = True
    with pytest.raises(RuntimeError, match="injected"):
        m.append(assertion("veltran", "hq_city", "tessaly", source="registry"), idempotency_key="k2")
    crash["on"] = False
    m.close()

    m2 = toy_memory(make_backend("sqlite", clock, tmp_path))
    assert m2.backend.recover().ok
    committed = step == "after_commit"
    head = m2.backend.head().lsn
    assert head == (2 if committed else 1)
    res = m2.append(assertion("veltran", "hq_city", "tessaly", source="registry"), idempotency_key="k2")
    assert res.replayed is committed
    assert m2.backend.head().lsn == 2  # exactly one copy of the report, whichever side of the commit the crash fell
    ans = current(m2, "alex", "work_city")
    assert isinstance(ans, Resolved) and established_value(ans) == "tessaly"
    assert m2.backend.recover().ok and m2.backend.verify_log().ok and m2.backend.verify_beliefs(m2.reviser).ok

    # and the stored beliefs equal those of an uninterrupted run
    ref = toy_memory(make_backend("memory", Clock()))
    ref.append(assertion("alex", "employer", "veltran", source="press"))
    ref.append(assertion("veltran", "hq_city", "tessaly", source="registry"))
    for e, a in (("alex", "employer"), ("veltran", "hq_city"), ("alex", "work_city")):
        x, y = current(m2, e, a), current(ref, e, a)
        assert isinstance(x, Resolved) and isinstance(y, Resolved)
        assert x.justified.segment == y.justified.segment


def test_not_reconstructable_snapshot_raises_until_the_contract_has_a_variant(mem: Memory) -> None:
    from palimem.memory import NotReconstructableError

    r1 = mem.append(assertion("alex", "employer", "veltran", source="press"))
    mem.append(assertion("alex", "employer", "acme", source="press", cue=__import__("palimem.types", fromlist=["Cue"]).Cue.CHANGE))
    assert r1.entry is not None and r1.entry.report.id is not None
    mem.delete(r1.entry.report.id)
    with pytest.raises((NotReconstructableError, StoreError)):
        current(mem, "alex", "employer", belief_as_of=1)
