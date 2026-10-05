"""An authorised dispute in the product profile (author ruling 3 of 2026-10-05).

The disputed target's candidate becomes ``unresolved`` against "disputed" (the dispute is read as a denial of the target's
value), with no value asserted, until confirmation from another origin group or withdrawal. The compat profile is unchanged.
Behavioural tests run on both backends; the equivalence tests compare the incremental state with the whole-log evaluation.
"""

from __future__ import annotations

import random
from collections.abc import Iterator

import pytest

from palimem.admission import AdmissionConfig, Admitter
from palimem.memory import Memory
from palimem.types import (
    DEFAULT_RULES,
    AuthorityRule,
    Cue,
    Decision,
    KernelStatus,
    Key,
    Origin,
    Power,
    Profile,
    Report,
    Resolved,
    Who,
    WhoKind,
)
from tests._adm import Log
from tests._pipeline_helpers import (
    Clock,
    assertion,
    current,
    make_backend,
    src,
    toy_memory,
)

BACKENDS = ["memory", "sqlite"]
KEY = Key(entity="alex", attr="employer")
GRANT = AuthorityRule(who=Who(kind=WhoKind.PRINCIPAL, value="user:alice"), may=(Power.DISPUTE,))


def mem_with(kind: str, **kw: object) -> Memory:
    cfg = AdmissionConfig(rules=(*DEFAULT_RULES, GRANT), **kw)  # type: ignore[arg-type]
    return toy_memory(make_backend(kind, Clock()), admission=cfg)


@pytest.fixture(params=BACKENDS)
def mem(request: pytest.FixtureRequest) -> Iterator[Memory]:
    m = mem_with(request.param)
    yield m
    m.close()


def dispute(target: str, *, actor: str = "user:alice", source: str = "alice_src", group: str = "g_alice",
            origin: Origin = Origin.EXTERNAL_OBSERVATION) -> Report:
    return Report(
        key=KEY, cue=Cue.DISPUTE, target=target, source=src(source), origin=origin, origin_group=group, actor=actor,
    )


def rid(res: object) -> str:
    e = res.entry  # type: ignore[attr-defined]
    assert e is not None and e.report.id is not None
    return str(e.report.id)


def forms(ans: Resolved) -> list[tuple[str, object]]:
    return sorted((c.form.form, c.form.value) for c in ans.alternatives)  # type: ignore[union-attr]


def test_a_granted_dispute_makes_the_target_unresolved_against_disputed_with_no_value_asserted(mem: Memory) -> None:
    r1 = mem.append(assertion("alex", "employer", "veltran", source="registry"))
    assert value(current(mem, "alex", "employer")) == "veltran"
    mem.append(dispute(rid(r1)))
    ans = current(mem, "alex", "employer")
    assert isinstance(ans, Resolved) and ans.kernel_status is KernelStatus.UNRESOLVED
    assert ans.assertion is None and ans.decision is Decision.ASK  # no value asserted: the policy asks
    assert forms(ans) == [("not_value", "veltran"), ("value", "veltran")]  # the claim against "disputed"
    assert sorted(s.environment for s in ans.provenance)  # one environment each: the target and the dispute


def value(ans: object) -> object:
    assert isinstance(ans, Resolved) and ans.assertion is not None
    return ans.assertion.form.value  # type: ignore[attr-defined]


def test_a_dispute_without_a_grant_is_an_allege_with_no_kernel_effect(mem: Memory) -> None:
    r1 = mem.append(assertion("alex", "employer", "veltran", source="registry"))
    mem.append(dispute(rid(r1), actor="user:mallory", source="mallory_src", group="g_mallory"))
    assert value(current(mem, "alex", "employer")) == "veltran"


def test_confirmation_from_another_origin_group_overrides_the_dispute(mem: Memory) -> None:
    r1 = mem.append(assertion("alex", "employer", "veltran", source="registry"))
    mem.append(dispute(rid(r1)))
    assert current(mem, "alex", "employer").kernel_status is KernelStatus.UNRESOLVED  # type: ignore[union-attr]
    mem.append(assertion("alex", "employer", "veltran", source="press"))  # another origin group says the same
    ans = current(mem, "alex", "employer")
    assert isinstance(ans, Resolved) and ans.kernel_status is KernelStatus.ESTABLISHED and ans.decision is Decision.COMMIT


def test_a_report_from_the_targets_own_group_or_the_disputers_group_does_not_confirm(mem: Memory) -> None:
    r1 = mem.append(assertion("alex", "employer", "veltran", source="registry", group="g_reg"))
    mem.append(dispute(rid(r1)))
    mem.append(assertion("alex", "employer", "veltran", source="registry2", group="g_reg"))  # the target's own group
    assert current(mem, "alex", "employer").kernel_status is KernelStatus.UNRESOLVED  # type: ignore[union-attr]
    mem.append(assertion("alex", "employer", "veltran", source="alice_other", group="g_alice"))  # the disputer's group
    assert current(mem, "alex", "employer").kernel_status is KernelStatus.UNRESOLVED  # type: ignore[union-attr]


def test_withdrawing_the_dispute_restores_the_target(mem: Memory) -> None:
    r1 = mem.append(assertion("alex", "employer", "veltran", source="registry"))
    d = mem.append(dispute(rid(r1)))
    assert current(mem, "alex", "employer").kernel_status is KernelStatus.UNRESOLVED  # type: ignore[union-attr]
    mem.withdraw(rid(d), source=src("alice_src"), actor="user:alice", origin_group="g_alice")
    assert value(current(mem, "alex", "employer")) == "veltran"


def test_withdrawing_the_target_leaves_the_dispute_with_nothing_to_dispute(mem: Memory) -> None:
    r1 = mem.append(assertion("alex", "employer", "veltran", source="registry"))
    mem.append(dispute(rid(r1)))
    mem.withdraw(rid(r1), source=src("registry"), actor="connector:registry")
    ans = current(mem, "alex", "employer")
    assert isinstance(ans, Resolved) and ans.kernel_status is KernelStatus.UNKNOWN  # no lone denial is manufactured


def test_an_agent_dispute_with_a_named_grant_has_the_same_effect(mem: Memory) -> None:
    cfg = AdmissionConfig(rules=(*DEFAULT_RULES, AuthorityRule(who=Who(kind=WhoKind.PRINCIPAL, value="agent:a1"), may=(Power.DISPUTE,))))
    m = toy_memory(make_backend("memory", Clock()), admission=cfg)
    r1 = m.append(assertion("alex", "employer", "veltran", source="registry"))
    m.append(dispute(rid(r1), actor="agent:a1", source="agent:a1", group="agent:a1", origin=Origin.AGENT_STATEMENT))
    ans = current(m, "alex", "employer")
    assert isinstance(ans, Resolved) and ans.kernel_status is KernelStatus.UNRESOLVED and ans.assertion is None


def test_a_dispute_the_kernel_cannot_model_is_inert_and_never_fails_the_append(mem: Memory) -> None:
    # the target carries a `change` cue: a denial beside it has no oracle yet, so the dispute stays audit-visible only
    r1 = mem.append(assertion("alex", "employer", "veltran", source="registry", cue=Cue.CHANGE))
    mem.append(dispute(rid(r1)))  # must not raise
    assert value(current(mem, "alex", "employer")) == "veltran"
    assert [e.report.id for e in mem.evidence(KEY).disputes]  # still on record for audits and the inquiry


def test_the_compat_profile_ignores_disputes() -> None:
    assert AdmissionConfig(profile=Profile.REVISE_STREAM_V1, dispute_is_denial=True).dispute_denial is False
    assert AdmissionConfig(profile=Profile.OPEN_WORLD).dispute_denial is True
    assert AdmissionConfig(profile=Profile.OPEN_WORLD, dispute_is_denial=False).dispute_denial is False
    log = Log()
    log.add("a", source="press", value="Acme")
    log.add("d", Cue.DISPUTE, source="press", target="a", actor="connector:press")
    for cfg, n in ((AdmissionConfig(), 2), (AdmissionConfig(dispute_is_denial=False), 1)):
        es = Admitter(cfg).evidence_set(log.list, Key(entity="alice", attr="employer"))
        assert len(es.direct) == n  # the switch turns the denial view on and off; the log is untouched


def test_the_switch_can_turn_the_effect_off_for_a_whole_admission_version(mem: Memory) -> None:
    m = mem_with("memory", dispute_is_denial=False)
    r1 = m.append(assertion("alex", "employer", "veltran", source="registry"))
    m.append(dispute(rid(r1)))
    assert value(current(m, "alex", "employer")) == "veltran"
    m.close()


# --------------------------------------------------------------------------- incremental == whole-log


def _scripted(mode: str, monkeypatch: pytest.MonkeyPatch, seed: int) -> list[object]:
    monkeypatch.setenv("PALIMEM_ADMISSION", mode)
    rng = random.Random(seed)
    clock = Clock()
    m = mem_with("memory")
    ids: list[str] = []
    snaps: list[object] = []
    for step in range(11):
        clock.day = step
        roll = rng.random()
        if roll < 0.22 and ids:
            m.append(dispute(rng.choice(ids)))
        elif roll < 0.32 and ids:
            m.withdraw(rng.choice(ids), source=src("alice_src"), actor="user:alice", origin_group="g_alice")
        else:
            source = rng.choice(["registry", "press", "wire"])
            res = m.append(assertion("alex", "employer", f"v{rng.randint(0, 1)}", source=source))
            ids.append(rid(res))
        a = current(m, "alex", "employer")
        if not isinstance(a, Resolved):
            snaps.append(("limited", a.reason.value))  # type: ignore[union-attr]
            continue
        snaps.append((a.kernel_status.value, a.decision.value, tuple(sorted(c.id for c in a.alternatives)),
                      None if a.assertion is None else a.assertion.id))
    m.close()
    return snaps


@pytest.mark.parametrize("seed", range(16))
def test_incremental_admission_equals_the_whole_log_evaluation_with_disputes(seed: int, monkeypatch: pytest.MonkeyPatch) -> None:
    inc = _scripted("incremental", monkeypatch, seed)
    assert inc == _scripted("whole-log", monkeypatch, seed)
    assert inc == _scripted("crosscheck", monkeypatch, seed)  # crosscheck compares decision for decision inside
