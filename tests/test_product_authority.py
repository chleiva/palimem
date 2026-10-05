"""Product-profile authority (design v0.3 §Write API; S-02 implementation note, default pending author confirmation).

In the ``open-world`` product profile a ``correct`` that fails the authority check lands as ``allege`` and has **no
effect**: it does not withdraw its target, and its claimed value is not even a rival report. The paper's behaviour (a
cross-origin correction is a competing assertion carrying a correction cue, A-CORR) is the compat profile's and is
covered by ``tests/test_admission.py`` and the differential gates. Every test runs on both backends.
"""

from __future__ import annotations

import pytest

from palimem.memory import Memory
from palimem.types import (
    Cue,
    Decision,
    KernelStatus,
    Key,
    Origin,
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


def correction(target: str, source: str, value: str, *, group: str | None = None) -> Report:
    return Report(
        key=Key(entity="alex", attr="employer"), cue=Cue.CORRECT, target=target, proposition=ValueProp(value=value),
        source=src(source), origin=Origin.EXTERNAL_OBSERVATION, origin_group=group or source, actor=f"connector:{source}",
    )


def value_of(ans: object) -> object:
    assert isinstance(ans, Resolved) and ans.assertion is not None
    return ans.assertion.form.value  # type: ignore[attr-defined]


def test_a_cross_source_correction_has_no_effect(mem: Memory) -> None:
    r1 = mem.append(assertion("alex", "employer", "veltran", source="press"))
    assert r1.entry is not None and r1.entry.report.id is not None
    # the registry is a different source (and a different origin group): it may not correct the press's report
    mem.append(correction(r1.entry.report.id, "registry", "globex"))
    ans = current(mem, "alex", "employer")
    assert isinstance(ans, Resolved) and ans.kernel_status is KernelStatus.ESTABLISHED
    assert ans.decision is Decision.COMMIT and value_of(ans) == "veltran"
    assert [s.environment for s in ans.provenance] == [(r1.entry.report.id,)]  # nothing of the failed correction


def test_a_sibling_source_of_the_same_origin_group_is_not_authority_in_the_product_profile(mem: Memory) -> None:
    # RA-007: two desks of one newspaper group. Authority is the target's own source (S-02); origin_group is for
    # corroboration only, never authority, because a shared origin is what an attacker can imitate
    r1 = mem.append(assertion("alex", "employer", "veltran", source="desk_a"))
    assert r1.entry is not None and r1.entry.report.id is not None
    mem.append(correction(r1.entry.report.id, "desk_b", "globex", group="desk_a"))  # claims the same origin group
    assert value_of(current(mem, "alex", "employer")) == "veltran"


def test_the_targets_own_source_still_corrects(mem: Memory) -> None:
    r1 = mem.append(assertion("alex", "employer", "veltran", source="press"))
    assert r1.entry is not None and r1.entry.report.id is not None
    mem.append(correction(r1.entry.report.id, "press", "globex"))
    ans = current(mem, "alex", "employer")
    assert isinstance(ans, Resolved) and ans.kernel_status is KernelStatus.ESTABLISHED
    assert value_of(ans) == "globex"


def test_the_failed_correction_is_visible_to_audits_as_an_allegation(mem: Memory) -> None:
    r1 = mem.append(assertion("alex", "employer", "veltran", source="press"))
    assert r1.entry is not None and r1.entry.report.id is not None
    r2 = mem.append(correction(r1.entry.report.id, "registry", "globex"))
    assert r2.entry is not None and r2.entry.report.id is not None
    ev = mem.evidence(Key(entity="alex", attr="employer"))
    assert [e.report.id for e in ev.direct] == [r1.entry.report.id]
    assert [e.report.id for e in ev.allegations] == [r2.entry.report.id]


# --------------------------------------------------------------------------- the switch (one line to reverse the default)


def test_the_paper_behaviour_is_one_flag_away_in_the_product_profile() -> None:
    """RA-006: a low-reliability blog 'corrects' a trusted registry. Default (design v0.3): the correction is an
    allege and the registry stands. With ``failed_correction_is_allege=False`` it is the paper's A-CORR competing
    assertion: the two values dispute each other and the key is unresolved (the policy asks)."""
    from palimem.admission import AdmissionConfig

    for flag, expect_status, expect_decision in ((None, KernelStatus.ESTABLISHED, Decision.COMMIT),
                                                  (True, KernelStatus.ESTABLISHED, Decision.COMMIT),
                                                  (False, KernelStatus.UNRESOLVED, Decision.ASK)):
        m = toy_memory(make_backend("memory", Clock()), admission=AdmissionConfig(failed_correction_is_allege=flag))
        r1 = m.append(assertion("alex", "employer", "veltran", source="registry"))
        assert r1.entry is not None and r1.entry.report.id is not None
        m.append(correction(r1.entry.report.id, "blog", "globex"))
        ans = current(m, "alex", "employer")
        assert isinstance(ans, Resolved)
        assert (ans.kernel_status, ans.decision) == (expect_status, expect_decision), flag


def test_the_flag_is_ignored_by_the_compat_profile() -> None:
    from palimem.admission import AdmissionConfig
    from palimem.types import Profile

    assert AdmissionConfig(profile=Profile.REVISE_STREAM_V1, failed_correction_is_allege=True).failed_correction_allege is False
    assert AdmissionConfig(profile=Profile.OPEN_WORLD).failed_correction_allege is True
    assert AdmissionConfig(profile=Profile.OPEN_WORLD, failed_correction_is_allege=False).failed_correction_allege is False


def test_the_flag_survives_a_store_reopen_payload() -> None:
    from palimem._reopen import admission_from_payload
    from palimem.admission import AdmissionConfig
    from palimem.memory import admission_payload

    for flag in (None, True, False):
        cfg = AdmissionConfig(failed_correction_is_allege=flag)
        assert admission_from_payload(admission_payload(cfg)).failed_correction_is_allege is flag
    old = admission_payload(AdmissionConfig())
    del old["failed_correction_is_allege"]  # a payload stored before the flag existed
    assert admission_from_payload(old).failed_correction_allege is True
