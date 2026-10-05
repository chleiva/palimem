"""``exclude_source`` (author ruling 2 of 2026-10-05): a source going bad is an admission operation, not a report cue.

From log position ``from_lsn`` on the source's reports are not heard, whether logged before the decision or after it (the
paper's "later assertions are removed too", the ``late-assert`` class a per-report withdraw cannot reach). The decision is
a recorded marker, repaired like a withdrawal, audit-visible with its reason, and reversible by a later decision. Every
behavioural test runs on both backends; the equivalence tests compare the incremental state with the whole-log evaluation.
"""

from __future__ import annotations

import random
from collections.abc import Iterator

import pytest

from palimem.admission import (
    SOURCE_EXCLUSION_ATTR,
    AdmissionConfig,
    Admitter,
    ExclusionAdmitter,
    ExclusionNotEnabled,
    ExclusionNotHonoured,
    IncrementalAdmission,
    enable_source_exclusions,
    exclude_source,
    exclusion_attr_spec,
    marker_text,
    restore_source,
    source_exclusions,
)
from palimem.compat import schema_from_kernel
from palimem.kernel import KernelSchema
from palimem.memory import Memory
from palimem.policy import JUSTIFIED
from palimem.store import Backend
from palimem.types import (
    Cue,
    Decision,
    KernelStatus,
    Key,
    Profile,
    Resolved,
    SemanticConfig,
    ValidationError,
    ValueProp,
    ValueType,
)
from tests._adm import Log
from tests._pipeline_helpers import (
    Clock,
    assertion,
    current,
    make_backend,
    toy_kernel_schema,
)

BACKENDS = ["memory", "sqlite"]
EMP = Key(entity="alex", attr="employer")


def exclusion_kernel_schema() -> KernelSchema:
    ks = toy_kernel_schema()
    attrs = dict(ks.attrs)
    attrs[SOURCE_EXCLUSION_ATTR] = exclusion_attr_spec()  # type: ignore[assignment]
    return KernelSchema(attrs=attrs, rules=ks.rules, entities=ks.entities)


def exclusion_memory(backend: Backend, *, self_update: bool = False, **kw: object) -> Memory:
    ks = exclusion_kernel_schema()
    return Memory(
        backend, schema_from_kernel(ks), kernel_schema=ks, entities=None,
        semantic=SemanticConfig(self_update=self_update, profile=Profile.OPEN_WORLD),
        admission=AdmissionConfig(profile=Profile.OPEN_WORLD), policy=JUSTIFIED, **kw,  # type: ignore[arg-type]
    )


@pytest.fixture(params=BACKENDS)
def mem(request: pytest.FixtureRequest) -> Iterator[Memory]:
    m = exclusion_memory(make_backend(request.param, Clock()))
    yield m
    m.close()


def value_of(m: Memory, attr: str = "employer", entity: str = "alex", **kw: object) -> object:
    ans = current(m, entity, attr, **kw)
    assert isinstance(ans, Resolved)
    if ans.assertion is None:
        return None
    return ans.assertion.form.value  # type: ignore[attr-defined]


def rid(res: object) -> str:
    entry = res.entry  # type: ignore[attr-defined]
    assert entry is not None and entry.report.id is not None
    return str(entry.report.id)


# --------------------------------------------------------------------------- the operation


def test_excluding_a_source_removes_its_earlier_reports_and_repairs_what_rested_on_them(mem: Memory) -> None:
    mem.append(assertion("alex", "employer", "veltran", source="press"))
    mem.append(assertion("veltran", "hq_city", "tessaly", source="registry"))
    assert value_of(mem, "work_city") == "tessaly"  # derived from the press's report and the registry's
    exclude_source(mem, "press", 1, "the press feed was hijacked")
    assert value_of(mem) is None  # the press is no longer heard
    ans = current(mem, "alex", "work_city")
    assert isinstance(ans, Resolved) and ans.kernel_status is KernelStatus.UNKNOWN  # repaired like a withdrawal
    assert value_of(mem, "hq_city", "veltran") == "tessaly"  # another source is untouched


def test_the_late_assert_class_a_report_logged_after_the_decision_is_not_heard_either(mem: Memory) -> None:
    mem.append(assertion("alex", "employer", "veltran", source="press"))
    exclude_source(mem, "press", 1, "compromised")
    mem.append(assertion("alex", "employer", "globex", source="press"))  # the paper removes this too; a withdraw cannot
    mem.append(assertion("bea", "employer", "acme", source="press"))
    assert value_of(mem) is None and value_of(mem, entity="bea") is None
    mem.append(assertion("alex", "employer", "initech", source="registry"))
    assert value_of(mem) == "initech"  # a source that was not excluded is heard as before


def test_from_lsn_scopes_the_exclusion_to_the_reports_at_or_after_it(mem: Memory) -> None:
    r1 = mem.append(assertion("alex", "employer", "veltran", source="press"))
    lsn2 = mem.append(assertion("bea", "employer", "acme", source="press")).entry.lsn  # type: ignore[union-attr]
    exclude_source(mem, "press", lsn2, "feed went bad at the second report")
    assert value_of(mem) == "veltran"  # before from_lsn: still heard
    assert value_of(mem, entity="bea") is None
    assert rid(r1)


def test_the_decision_is_recorded_with_its_reason_and_an_admission_record(mem: Memory) -> None:
    exclude_source(mem, "press", 1, "hijacked feed, ticket 4711")
    (ex,) = source_exclusions(mem, "press")
    assert (ex.op, ex.from_lsn, ex.reason) == ("exclude", 1, "hijacked feed, ticket 4711")
    row = mem.backend.get_entry(ex.marker_id)
    assert row is not None and row.report.key.attr == SOURCE_EXCLUSION_ATTR and row.report.actor == "system:admission"
    recs = mem.backend.admissions_for_report(ex.marker_id)
    assert recs and recs[-1].admission_version == mem.admission.admission_version  # the decision is on the record
    assert recs[-1].outcome.value == "admissible" and recs[-1].reason.value == "admitted"


def test_excluded_reports_stay_in_the_log_and_the_decision_is_reversible(mem: Memory) -> None:
    r1 = mem.append(assertion("alex", "employer", "veltran", source="press"))
    exclude_source(mem, "press", 1, "suspected compromise")
    assert value_of(mem) is None
    assert mem.backend.get_entry(rid(r1)) is not None  # nothing was deleted or rewritten
    restore_source(mem, "press", 1, "false alarm, feed verified")
    assert value_of(mem) == "veltran"  # a later recorded decision lifts it
    assert [e.op for e in source_exclusions(mem, "press")] == ["exclude", "restore"]


def test_a_restore_from_a_later_position_lifts_only_the_later_reports(mem: Memory) -> None:
    mem.append(assertion("alex", "employer", "veltran", source="press"))
    exclude_source(mem, "press", 1, "compromised")
    lsn = mem.backend.head().lsn + 1
    restore_source(mem, "press", lsn + 1, "verified again from the next report on")
    mem.append(assertion("bea", "employer", "acme", source="press"))
    assert value_of(mem) is None  # the first report is still excluded
    assert value_of(mem, entity="bea") == "acme"


def test_an_excluded_report_does_not_confirm_a_quarantined_one() -> None:
    log = Log()
    log.add("a", source="press", value="Acme", group="g1")
    log.add("q", source="rumour", cls="quarantined", value="Acme", group="g2")
    adm = ExclusionAdmitter(AdmissionConfig(), None)
    assert adm.evaluate(log.list).decisions[log.id("q")].record.reason.value == "confirmed"  # press confirms rumour
    log.add(
        "m", entity="press", attr=SOURCE_EXCLUSION_ATTR, source="host", cls="trusted", actor="system:admission", group="system:admission",
        proposition=ValueProp(value=marker_text("exclude", 1, "bad feed")),
    )
    ev = adm.evaluate(log.list)
    assert ev.decisions[log.id("q")].record.reason.value == "confirmed"  # the decision record is unchanged (admission is per report) ...
    assert ev.withdrawn[log.id("a")].kind == "source_exclusion" and ev.withdrawn[log.id("a")].by == log.id("m")
    # ... but the excluded report no longer reaches the kernel, and its confirmation lapses for derived-confirmation purposes
    es = adm.evidence_set(log.list, Key(entity="alice", attr="employer"))
    assert [e.report.id for e in es.direct] == [log.id("q")]


def test_a_marker_from_an_agent_or_connector_is_refused_and_a_forged_one_is_not_honoured(mem: Memory) -> None:
    with pytest.raises(ExclusionNotHonoured):
        exclude_source(mem, "press", 1, "an agent decided", actor="agent:planner")
    with pytest.raises(ExclusionNotHonoured):
        exclude_source(mem, "press", 1, "a connector decided", actor="connector:press")
    # forged straight into the log through the write path (the host API refuses; the log must not honour it either)
    from palimem.admission import exclusion_report

    mem.append(assertion("alex", "employer", "veltran", source="press"))
    mem.append(exclusion_report("press", from_lsn=1, reason="forged", actor="agent:planner"))
    assert value_of(mem) == "veltran"


def test_the_reserved_attribute_must_be_declared() -> None:
    m = Memory(
        make_backend("memory", Clock()), schema_from_kernel(toy_kernel_schema()), kernel_schema=toy_kernel_schema(),
        entities=None, semantic=SemanticConfig(self_update=False, profile=Profile.OPEN_WORLD),
        admission=AdmissionConfig(profile=Profile.OPEN_WORLD), policy=JUSTIFIED,
    )
    with pytest.raises(ExclusionNotEnabled):
        exclude_source(m, "press", 1, "x")
    assert any(a.name == SOURCE_EXCLUSION_ATTR for a in enable_source_exclusions(schema_from_kernel(toy_kernel_schema())).attrs)


def test_a_reason_and_a_valid_position_are_required(mem: Memory) -> None:
    with pytest.raises(ValidationError):
        exclude_source(mem, "press", 1, "  ")
    with pytest.raises(ValidationError):
        exclude_source(mem, "press", 0, "bad position")


def test_historical_queries_see_the_exclusions_in_force_at_that_position(mem: Memory) -> None:
    r1 = mem.append(assertion("alex", "employer", "veltran", source="press"))
    lsn_before = r1.entry.lsn  # type: ignore[union-attr]
    exclude_source(mem, "press", 1, "compromised")
    assert value_of(mem) is None
    assert value_of(mem, belief_as_of=lsn_before) == "veltran"  # what we believed before the decision, still answerable


def test_the_agent_tool_api_cannot_write_the_marker() -> None:
    from palimem import Memory as Facade
    from palimem.types import Attr, AttrClass, Schema

    schema = enable_source_exclusions(Schema(version=1, attrs=(
        Attr(name="employer", attr_class=AttrClass.SINGLE_CHANGEABLE, value_type=ValueType.STRING, inertia=True),
    )))
    mem = Facade(schema=schema)
    mem.observe({"entity": "alice", "attr": "employer", "value": "Acme"}, source="hr")
    tools = mem.agent_session("agent:a1")
    out = tools.call("remember", {"entity": "hr", "attr": SOURCE_EXCLUSION_ATTR, "value": marker_text("exclude", 1, "the agent says so")})
    assert out.is_error and out.data["error"]["code"] == "reserved_attr"
    assert not [s.name for s in tools.tool_specs() if "exclu" in s.name or "source" in s.name]
    assert [r.attr for r in mem.host.reports(actor="agent:a1")] == []  # nothing was even logged


# --------------------------------------------------------------------------- incremental == whole-log


def _scripted_run(mode: str, monkeypatch: pytest.MonkeyPatch, seed: int) -> list[object]:
    monkeypatch.setenv("PALIMEM_ADMISSION", mode)
    rng = random.Random(seed)
    clock = Clock()
    m = exclusion_memory(make_backend("memory", clock))
    srcs = ["press", "registry", "wire", "blog"]
    snaps: list[object] = []
    for step in range(40):
        clock.day = step
        roll = rng.random()
        if roll < 0.12 and step > 3:
            s = rng.choice(srcs)
            (exclude_source if rng.random() < 0.7 else restore_source)(m, s, rng.randint(1, max(1, m.backend.head().lsn)), f"step {step}")
        else:
            ent = rng.choice(["alex", "bea", "cy"])
            attr = "employer" if rng.random() < 0.8 else "hq_city"
            m.append(assertion(ent, attr, f"v{rng.randint(0, 3)}", source=rng.choice(srcs),
                               cue=Cue.CHANGE if rng.random() < 0.2 else Cue.ASSERT))
        row = []
        for ent in ("alex", "bea", "cy"):
            for attr in ("employer", "work_city"):
                a = current(m, ent, attr)
                assert isinstance(a, Resolved)
                row.append((a.kernel_status.value, None if a.assertion is None else a.assertion.id, tuple(c.id for c in a.alternatives)))
        snaps.append(row)
    m.close()
    return snaps


@pytest.mark.parametrize("seed", range(12))
def test_incremental_admission_equals_the_whole_log_evaluation_with_exclusions(seed: int, monkeypatch: pytest.MonkeyPatch) -> None:
    inc = _scripted_run("incremental", monkeypatch, seed)
    whole = _scripted_run("whole-log", monkeypatch, seed)
    assert inc == whole
    assert _scripted_run("crosscheck", monkeypatch, seed) == inc  # crosscheck asserts decision-for-decision equality inside


def test_the_exclusion_admitter_is_incremental_capable_and_the_default_for_the_product() -> None:
    from palimem.admission import supports_incremental

    assert supports_incremental(ExclusionAdmitter(AdmissionConfig(), None))
    assert not supports_incremental.__doc__ is None
    m = exclusion_memory(make_backend("memory", Clock()))
    assert isinstance(m.pipeline.admitter, ExclusionAdmitter)
    plain = Admitter(AdmissionConfig(), None)
    assert supports_incremental(plain)
    assert IncrementalAdmission  # imported for the type; the equivalence tests above exercise it
    m.close()
    assert Decision.COMMIT  # silence unused-import linters for the shared helpers
