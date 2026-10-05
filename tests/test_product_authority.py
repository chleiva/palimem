"""Product-profile authority (design v0.3 §Write API as amended by the author's ruling of 2026-10-05, S-02).

In the ``open-world`` product profile a ``correct`` that fails the authority check loses its effect on the *target*
(``effective_cue`` is ``allege``: the target is neither withdrawn nor forced to ``ERR``), but its proposition is still a
claim by its own source and, when that source is admissible, is admitted as an ordinary ``assert``. The paper's behaviour
(a cross-origin correction is a competing assertion carrying a correction cue, A-CORR) is the compat profile's and the
``failed_correction_is_allege=False`` switch's. Every test runs on both backends.
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


def test_a_cross_source_correction_does_not_withdraw_its_target_but_its_content_is_a_rival_assert(mem: Memory) -> None:
    r1 = mem.append(assertion("alex", "employer", "veltran", source="press"))
    assert r1.entry is not None and r1.entry.report.id is not None
    # the registry is a different source (and a different origin group): it may not correct the press's report ...
    r2 = mem.append(correction(r1.entry.report.id, "registry", "globex"))
    assert r2.entry is not None and r2.entry.report.id is not None
    # ... so the press report stands (it is not withdrawn), and the registry's claim is admitted as an ordinary assert:
    # two admissible reports disagree and nothing cues either, so the key is unresolved and the policy asks
    ans = current(mem, "alex", "employer")
    assert isinstance(ans, Resolved) and ans.kernel_status is KernelStatus.UNRESOLVED
    assert ans.decision is Decision.ASK and ans.assertion is None
    assert sorted(c.form.value for c in ans.alternatives) == ["globex", "veltran"]  # type: ignore[attr-defined]
    assert sorted(s.environment for s in ans.provenance) == sorted([(r1.entry.report.id,), (r2.entry.report.id,)])


def test_a_correction_is_never_worth_less_than_the_same_statement_as_an_assert(mem: Memory) -> None:
    """The point of the ruling: otherwise an attacker simply uses ``assert``. A cross-source correction and a plain
    assert of the same value from the same source give the same answer."""
    other = toy_memory(make_backend("memory", Clock()))
    a1 = other.append(assertion("alex", "employer", "veltran", source="press"))
    assert a1.entry is not None and a1.entry.report.id is not None
    other.append(assertion("alex", "employer", "globex", source="registry"))
    r1 = mem.append(assertion("alex", "employer", "veltran", source="press"))
    assert r1.entry is not None and r1.entry.report.id is not None
    mem.append(correction(r1.entry.report.id, "registry", "globex"))
    x, y = current(mem, "alex", "employer"), current(other, "alex", "employer")
    assert isinstance(x, Resolved) and isinstance(y, Resolved)
    assert (x.kernel_status, x.decision) == (y.kernel_status, y.decision)
    assert sorted(c.form.value for c in x.alternatives) == sorted(c.form.value for c in y.alternatives)  # type: ignore[attr-defined]


def test_a_sibling_source_of_the_same_origin_group_is_not_authority_in_the_product_profile(mem: Memory) -> None:
    # RA-007: two desks of one newspaper group. Authority is the target's own source (S-02); origin_group is for
    # corroboration only, never authority, because a shared origin is what an attacker can imitate. The first desk's
    # report is NOT withdrawn; the second desk's content is admitted as an assert from an admissible source
    r1 = mem.append(assertion("alex", "employer", "veltran", source="desk_a"))
    assert r1.entry is not None and r1.entry.report.id is not None
    mem.append(correction(r1.entry.report.id, "desk_b", "globex", group="desk_a"))  # claims the same origin group
    ev = mem.evidence(Key(entity="alex", attr="employer"))
    assert ev.direct[0].report.id == r1.entry.report.id and not ev.withdrawn
    ans = current(mem, "alex", "employer")
    assert isinstance(ans, Resolved) and ans.kernel_status is KernelStatus.UNRESOLVED  # P0c: no self-update


def test_a_sibling_source_content_supersedes_under_self_update() -> None:
    # the same scenario under P0cSU (self_update on): the same origin group's later value supersedes its earlier one,
    # so the content admitted from the sibling desk is the established answer: RA-007's default gold
    clock = Clock()
    m = toy_memory(make_backend("memory", clock), self_update=True)
    r1 = m.append(assertion("alex", "employer", "veltran", source="desk_a", group="newsco"))
    assert r1.entry is not None and r1.entry.report.id is not None
    clock.day = 4  # the sibling desk reports later (A-SU needs the earlier anchor to be strictly earlier)
    m.append(correction(r1.entry.report.id, "desk_b", "globex", group="newsco"))
    ans = current(m, "alex", "employer")
    assert isinstance(ans, Resolved) and ans.kernel_status is KernelStatus.ESTABLISHED and value_of(ans) == "globex"


def test_the_targets_own_source_still_corrects(mem: Memory) -> None:
    r1 = mem.append(assertion("alex", "employer", "veltran", source="press"))
    assert r1.entry is not None and r1.entry.report.id is not None
    mem.append(correction(r1.entry.report.id, "press", "globex"))
    ans = current(mem, "alex", "employer")
    assert isinstance(ans, Resolved) and ans.kernel_status is KernelStatus.ESTABLISHED
    assert value_of(ans) == "globex"


def test_the_failed_correction_is_visible_to_audits_as_an_allegation_and_the_kernel_reads_an_assert(mem: Memory) -> None:
    r1 = mem.append(assertion("alex", "employer", "veltran", source="press"))
    assert r1.entry is not None and r1.entry.report.id is not None
    r2 = mem.append(correction(r1.entry.report.id, "registry", "globex"))
    assert r2.entry is not None and r2.entry.report.id is not None
    ev = mem.evidence(Key(entity="alex", attr="employer"))
    assert [e.report.id for e in ev.direct] == [r1.entry.report.id, r2.entry.report.id]
    assert [e.report.cue for e in ev.direct] == [Cue.ASSERT, Cue.ASSERT]  # the kernel read an assert, not a correction
    assert [e.report.id for e in ev.allegations] == [r2.entry.report.id]  # the target part is on record for audits
    # the log itself is untouched: the stored report is still the correction the source sent
    row = mem.backend.get_entry(r2.entry.report.id)
    assert row is not None and row.report.cue is Cue.CORRECT and row.report.target == r1.entry.report.id


# --------------------------------------------------------------------------- the switch (one line to reverse the default)


def test_the_paper_behaviour_is_one_flag_away_in_the_product_profile() -> None:
    """RA-006: a low-reliability blog 'corrects' a trusted registry. Under both settings the two values dispute each
    other, the key is unresolved and the policy asks (the registered gold). What differs is what the kernel reads: by
    default (the 2026-10-05 ruling) an ordinary assert, so the correction cannot force its target to ERR; with
    ``failed_correction_is_allege=False`` the paper's A-CORR, a competing assertion that carries the correction cue."""
    from palimem.admission import AdmissionConfig

    for flag, expect_cue in ((None, Cue.ASSERT), (True, Cue.ASSERT), (False, Cue.CORRECT)):
        m = toy_memory(make_backend("memory", Clock()), admission=AdmissionConfig(failed_correction_is_allege=flag))
        r1 = m.append(assertion("alex", "employer", "veltran", source="registry"))
        assert r1.entry is not None and r1.entry.report.id is not None
        m.append(correction(r1.entry.report.id, "blog", "globex"))
        ans = current(m, "alex", "employer")
        assert isinstance(ans, Resolved)
        assert (ans.kernel_status, ans.decision) == (KernelStatus.UNRESOLVED, Decision.ASK), flag
        assert m.evidence(Key(entity="alex", attr="employer")).direct[1].report.cue is expect_cue, flag


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
