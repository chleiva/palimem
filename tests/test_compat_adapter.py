"""The ``revise-stream-v1`` compatibility module: projections, the profile config and the compat-only conventions."""

from __future__ import annotations

import pytest

from palimem.admission import AdmissionConfig
from palimem.compat import (
    CHANGE_FROM_PREFIX,
    SOURCE_STATUS_ATTR,
    CompatAdmitter,
    CompatError,
    answer_v1,
    change_from_of,
    compat_admission_config,
    compat_semantic,
    kernel_schema_with_marker,
    reported_v1,
    schema_from_kernel,
    segment_v1,
    source_retraction_report,
    with_change_from,
    yesno_v1,
)
from palimem.kernel import AttrSpec, KernelSchema
from palimem.memory import Memory
from palimem.policy import JUSTIFIED
from palimem.store import InMemoryBackend
from palimem.types import (
    Candidate,
    Cue,
    EmptyForm,
    KernelStatus,
    Key,
    Profile,
    Query,
    Resolved,
    ResourceLimited,
    ResourceLimitedReason,
    SetForm,
    ValueForm,
)
from palimem.types import Segment as PSegment
from tests._pipeline_helpers import (
    Clock,
    assertion,
    established_value,
    toy_kernel_schema,
)

K = Key(entity="alex", attr="employer")


def _seg(status: KernelStatus, est: object = None, alts: tuple[object, ...] = ()) -> PSegment:
    return PSegment(
        valid_from=None, valid_to=None, kernel_status=status,
        established=None if est is None else Candidate(key=K, form=est),  # type: ignore[arg-type]
        alternatives=tuple(Candidate(key=K, form=a) for a in alts),  # type: ignore[arg-type]
    )


def test_segment_projection_per_slot_type() -> None:
    assert segment_v1(_seg(KernelStatus.UNKNOWN), False) == {"status": "unknown", "assertion": None, "alternatives": []}
    assert segment_v1(_seg(KernelStatus.ESTABLISHED, ValueForm(value="acme")), False)["assertion"] == "acme"
    assert segment_v1(_seg(KernelStatus.ESTABLISHED, SetForm(values=("b", "a"))), True)["assertion"] == ["a", "b"]
    # the paper's closed world for a multi-valued slot: established_empty projects to established with an empty set
    assert segment_v1(_seg(KernelStatus.ESTABLISHED_EMPTY, EmptyForm()), True) == {
        "status": "established", "assertion": [], "alternatives": []}
    un = segment_v1(_seg(KernelStatus.UNRESOLVED, alts=(ValueForm(value="a"), ValueForm(value="b"))), False)
    assert un["status"] == "unresolved" and sorted(un["alternatives"]) == ["a", "b"]


def test_possible_is_adapter_only_and_comes_from_mixed_truth_sets() -> None:
    assert yesno_v1([True, True])["assertion"] is True and yesno_v1([True, True])["status"] == "established"
    assert yesno_v1([False, False]) == {"status": "established", "assertion": False, "alternatives": []}
    assert yesno_v1([True, False])["status"] == "possible"
    assert yesno_v1([])["status"] == "unknown"
    assert "possible" not in {s.value for s in KernelStatus}


def test_resource_limited_has_no_v1_projection() -> None:
    rl = ResourceLimited(
        reason=ResourceLimitedReason.ENVIRONMENT_BUDGET, required_generation=2, completed_generation=1, reason_key=K)
    with pytest.raises(CompatError):
        answer_v1(rl, multi=False)


def test_change_from_round_trips_through_the_report_field() -> None:
    r = assertion("alex", "employer", "acme", cue=Cue.CHANGE)
    assert change_from_of(r) is None
    r2 = with_change_from(r, "veltran")
    assert r2.change_from == "veltran" and r2.raw_ref is None
    assert change_from_of(r2) == "veltran"
    assert change_from_of(with_change_from(r, 7)) == 7  # json keeps int distinct from str


def test_change_from_in_the_legacy_raw_ref_carrier_is_still_read() -> None:
    from dataclasses import replace

    r = assertion("alex", "employer", "acme", cue=Cue.CHANGE)
    legacy = replace(r, raw_ref=CHANGE_FROM_PREFIX + '"veltran"')
    assert change_from_of(legacy) == "veltran"
    assert change_from_of(replace(legacy, change_from="x")) == "x"  # the field wins
    # only a change cue ever carried it
    assert change_from_of(replace(assertion("alex", "employer", "acme"), raw_ref=CHANGE_FROM_PREFIX + '"x"')) is None


def test_compat_profile_config() -> None:
    sem, adm = compat_semantic(), compat_admission_config()
    assert sem.profile is Profile.REVISE_STREAM_V1 and not sem.self_update
    assert compat_semantic(self_update=True).self_update
    assert adm.profile is Profile.REVISE_STREAM_V1 and not adm.must_be_live  # withdrawn actors keep acting
    assert AdmissionConfig(profile=Profile.OPEN_WORLD).must_be_live  # the product default


def test_schema_from_kernel_maps_what_the_contract_can_express() -> None:
    ks = toy_kernel_schema()
    sch = schema_from_kernel(ks)
    by = {a.name: a for a in sch.attrs}
    assert by["employer"].attr_class.value == "single_changeable" and by["hq_city"].attr_class.value == "single_stable"
    assert by["affiliations"].attr_class.value == "multi_set"
    assert by["work_city"].attr_class.value == "derived" and by["work_city"].rule is not None
    assert set(by["work_city"].rule.reads) == {"employer", "hq_city"}
    assert all(a.inertia for a in sch.attrs)  # the paper applies inertia to every attribute


def _compat_memory() -> Memory:
    ks = kernel_schema_with_marker(
        KernelSchema(
            attrs={"employer": AttrSpec("employer", "single", True)}, rules=(), entities=("alex",)))
    return Memory(
        InMemoryBackend(clock=Clock()), schema_from_kernel(ks), kernel_schema=ks, entities=("alex",),
        semantic=compat_semantic(), admission=compat_admission_config(), policy=JUSTIFIED,
        admitter_class=CompatAdmitter,
    )


def test_source_retraction_marker_removes_a_sources_reports_before_and_after_it() -> None:
    m = _compat_memory()
    m.append(assertion("alex", "employer", "veltran", source="press"))
    ans = m.query(Query(key=K, profile=Profile.REVISE_STREAM_V1))
    assert isinstance(ans, Resolved) and established_value(ans) == "veltran"

    m.append(source_retraction_report("press"))
    ans2 = m.query(Query(key=K, profile=Profile.REVISE_STREAM_V1))
    assert isinstance(ans2, Resolved) and ans2.kernel_status is KernelStatus.UNKNOWN

    # a later assertion by the retracted source is removed as well (the paper's behaviour; a per-report withdraw
    # cannot express this because the report does not exist yet), another source's report is not affected
    m.append(assertion("alex", "employer", "acme", source="press", cue=Cue.CHANGE))
    m.append(assertion("alex", "employer", "globex", source="wire", cue=Cue.CHANGE))
    ans3 = m.query(Query(key=K, profile=Profile.REVISE_STREAM_V1))
    assert isinstance(ans3, Resolved) and established_value(ans3) == "globex"
    # the earlier snapshot is still served
    old = m.query(Query(key=K, profile=Profile.REVISE_STREAM_V1, belief_as_of=1))
    assert isinstance(old, Resolved) and established_value(old) == "veltran"


def test_the_marker_is_an_ordinary_visible_belief() -> None:
    m = _compat_memory()
    m.append(source_retraction_report("press"))
    ans = m.query(Query(key=Key(entity="press", attr=SOURCE_STATUS_ATTR), profile=Profile.REVISE_STREAM_V1))
    assert isinstance(ans, Resolved) and established_value(ans) == "retracted"


def test_reported_projection_lists_values_with_study_ids() -> None:
    m = _compat_memory()
    a = m.append(assertion("alex", "employer", "veltran", source="press"))
    b = m.append(assertion("alex", "employer", "acme", source="wire"))
    assert a.entry is not None and b.entry is not None
    ev = m.evidence(K)
    out = reported_v1(ev.direct, lambda rid: {a.entry.report.id: "o1", b.entry.report.id: "o2"}[rid])  # type: ignore[index]
    assert out["assertion"] == {"acme": ["o2"], "veltran": ["o1"]}
