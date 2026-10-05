"""Negative evidence through the whole pipeline (author ruling 4 of 2026-10-05), on both backends."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from palimem.memory import Memory
from palimem.types import (
    Cue,
    Decision,
    KernelStatus,
    Key,
    NotValueProp,
    Origin,
    Report,
    Resolved,
    ResourceLimited,
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
def mem(request: pytest.FixtureRequest) -> Iterator[Memory]:
    m = toy_memory(make_backend(request.param, Clock()))
    yield m
    m.close()


def denial(entity: str, attr: str, value: str, source: str = "press") -> Report:
    return Report(
        key=Key(entity=entity, attr=attr), cue=Cue.ASSERT, proposition=NotValueProp(value=value), source=src(source),
        origin=Origin.EXTERNAL_OBSERVATION, origin_group=source, actor=f"connector:{source}",
    )


def forms(ans: Resolved) -> list[tuple[str, object]]:
    return sorted((c.form.form, getattr(c.form, "value", None)) for c in ans.alternatives)


def test_two_compatible_denials_are_unknown_with_both_listed_as_constraints(mem: Memory) -> None:
    mem.append(denial("alex", "employer", "acme", "press"))
    mem.append(denial("alex", "employer", "globex", "registry"))
    ans = current(mem, "alex", "employer")
    assert isinstance(ans, Resolved) and ans.kernel_status is KernelStatus.UNKNOWN
    assert ans.assertion is None and ans.decision is Decision.ABSTAIN  # nothing to commit to: the value is only narrowed
    assert forms(ans) == [("not_value", "acme"), ("not_value", "globex")]  # both survive to the Answer
    assert sorted(s.environment for s in ans.provenance) == sorted((i,) for i in _ids(mem))  # one environment per denial


def _ids(mem: Memory) -> list[str]:
    return [e.report.id for e in mem.backend.scan() if hasattr(e, "report") and e.report.id]  # type: ignore[union-attr]


def test_one_denial_alone_is_established_false(mem: Memory) -> None:
    mem.append(denial("alex", "employer", "acme"))
    ans = current(mem, "alex", "employer")
    assert isinstance(ans, Resolved) and ans.kernel_status is KernelStatus.ESTABLISHED_FALSE
    assert ans.decision is Decision.COMMIT and ans.assertion is not None and ans.assertion.form.form == "not_value"


def test_a_positive_and_a_denial_of_the_same_value_are_unresolved_and_the_survivor_decides_after_a_withdrawal(mem: Memory) -> None:
    r1 = mem.append(assertion("alex", "employer", "acme", source="registry"))
    r2 = mem.append(denial("alex", "employer", "acme", "press"))
    ans = current(mem, "alex", "employer")
    assert isinstance(ans, Resolved) and ans.kernel_status is KernelStatus.UNRESOLVED and ans.decision is Decision.ASK
    assert forms(ans) == [("not_value", "acme"), ("value", "acme")]
    assert r2.entry is not None and r2.entry.report.id is not None and r1.entry is not None and r1.entry.report.id is not None
    # withdrawing the denial (by its own source): the positive alone decides
    mem.withdraw(r2.entry.report.id, source=src("press"), actor="connector:press")
    after = current(mem, "alex", "employer")
    assert isinstance(after, Resolved) and after.kernel_status is KernelStatus.ESTABLISHED and after.decision is Decision.COMMIT


def test_a_denial_of_another_value_is_a_constraint_not_a_rival(mem: Memory) -> None:
    mem.append(assertion("alex", "employer", "acme", source="registry"))
    mem.append(denial("alex", "employer", "globex", "press"))
    ans = current(mem, "alex", "employer")
    assert isinstance(ans, Resolved) and ans.kernel_status is KernelStatus.ESTABLISHED and ans.decision is Decision.COMMIT


def test_a_derived_belief_that_reads_a_key_with_only_denials_has_no_value(mem: Memory) -> None:
    mem.append(denial("alex", "employer", "veltran", "press"))
    mem.append(assertion("veltran", "hq_city", "tessaly", source="registry"))
    ans = current(mem, "alex", "work_city")
    assert isinstance(ans, Resolved) and ans.kernel_status is KernelStatus.UNKNOWN  # a denial gives the rule no employer


def test_historical_queries_see_the_denials_in_force_then(mem: Memory) -> None:
    r1 = mem.append(denial("alex", "employer", "acme", "press"))
    assert r1.entry is not None
    lsn1 = r1.entry.lsn
    mem.append(denial("alex", "employer", "globex", "registry"))
    from palimem.types import Query

    then = mem.query(Query(key=Key(entity="alex", attr="employer"), belief_as_of=lsn1, profile=mem.semantic.profile))
    now = current(mem, "alex", "employer")
    assert isinstance(then, Resolved) and then.kernel_status is KernelStatus.ESTABLISHED_FALSE  # one denial then
    assert isinstance(now, Resolved) and now.kernel_status is KernelStatus.UNKNOWN  # two now
    assert not isinstance(then, ResourceLimited)
