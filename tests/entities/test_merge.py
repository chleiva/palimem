"""Entity merges through the whole pipeline: aggregation, reversal, exactness, withdrawal, crash safety, authority."""

from __future__ import annotations

from pathlib import Path

import pytest

from palimem.entities import (
    ENTITY_MERGE_ATTR,
    Entities,
    EntitiesError,
    MergeRejected,
    UnknownEntity,
)
from palimem.entities.registry import MergeOp, decode_marker, encode_marker
from palimem.memory import Memory
from palimem.store import APPEND_STEPS
from palimem.types import (
    Cue,
    KernelStatus,
    Key,
    MemberProp,
    Origin,
    Query,
    Report,
    Resolved,
)
from tests._pipeline_helpers import (
    Clock,
    assertion,
    current,
    established_value,
    make_backend,
    src,
)
from tests.entities._helpers import core, merge_memory, merge_setup, versions

BACKENDS = ["memory", "sqlite"]


@pytest.fixture(params=BACKENDS)
def ms(request: pytest.FixtureRequest) -> tuple[Memory, Entities]:
    return merge_setup(make_backend(request.param, Clock()))


def seed(m: Memory) -> dict[str, str]:
    """alex works for veltran; the registry says veltran is in tessaly; the wire (a fragmented name) says ashford."""
    ids: dict[str, str] = {}
    for name, rep in (
        ("r1", assertion("alex", "employer", "veltran", source="press")),
        ("r2", assertion("veltran", "hq_city", "tessaly", source="registry")),
        ("r3", assertion("Veltran Inc", "hq_city", "ashford", source="wire")),
    ):
        res = m.append(rep)
        assert res.entry is not None and res.entry.report.id is not None
        ids[name] = res.entry.report.id
    return ids


def status(m: Memory, entity: str, attr: str, **kw: object) -> tuple[KernelStatus, object]:
    ans = current(m, entity, attr, **kw)
    assert isinstance(ans, Resolved)
    return ans.kernel_status, ans


# --------------------------------------------------------------------------- aggregation and reading


def test_a_merge_justifies_the_class_as_one_entity_and_reading_an_alias_reads_the_representative(ms: tuple[Memory, Entities]) -> None:
    m, ent = ms
    seed(m)
    assert status(m, "veltran", "hq_city")[0] is KernelStatus.ESTABLISHED  # tessaly, the fragment is a separate key
    assert status(m, "Veltran Inc", "hq_city")[0] is KernelStatus.ESTABLISHED  # ashford on its own
    rec = ent.merge("Veltran Inc", "veltran", reason="same registered company")
    assert rec.representative == "veltran" and rec.members == ("Veltran Inc", "veltran")
    st, _ans = status(m, "veltran", "hq_city")
    assert st is KernelStatus.UNRESOLVED  # tessaly vs ashford: the merge exposes a real conflict, it does not hide one
    st2, ans2 = status(m, "Veltran Inc", "hq_city")
    assert st2 is KernelStatus.UNRESOLVED and ans2.justified.key == Key(entity="veltran", attr="hq_city")  # type: ignore[attr-defined]
    # the rule reads the representative: work_city(alex) is unresolved too, two steps downstream
    assert status(m, "alex", "work_city")[0] is KernelStatus.UNRESOLVED


def test_belief_as_of_before_the_merge_is_answered_under_the_old_classes(ms: tuple[Memory, Entities]) -> None:
    m, ent = ms
    seed(m)
    before = m.backend.head().lsn
    ent.merge("Veltran Inc", "veltran", reason="r")
    st, ans = status(m, "Veltran Inc", "hq_city", belief_as_of=before)
    assert st is KernelStatus.ESTABLISHED and established_value(ans) == "ashford"  # type: ignore[arg-type]
    assert ans.justified.key == Key(entity="Veltran Inc", attr="hq_city")  # type: ignore[attr-defined]


def test_evidence_on_an_alias_reaches_the_representative_key_when_the_representative_has_none(ms: tuple[Memory, Entities]) -> None:
    m, ent = ms
    m.append(assertion("alex", "employer", "veltran", source="press"))
    m.append(assertion("Veltran Inc", "hq_city", "ashford", source="wire"))
    m.append(assertion("veltran", "affiliations", "x", source="registry"))  # makes `veltran` a known entity
    assert status(m, "veltran", "hq_city")[0] is KernelStatus.UNKNOWN
    ent.merge("Veltran Inc", "veltran", reason="same company")
    st, ans = status(m, "veltran", "hq_city")
    assert st is KernelStatus.ESTABLISHED and established_value(ans) == "ashford"  # type: ignore[arg-type]
    assert status(m, "alex", "work_city")[0] is KernelStatus.ESTABLISHED  # derived key now sees it


def test_a_new_report_on_a_member_updates_the_class_belief(ms: tuple[Memory, Entities]) -> None:
    m, ent = ms
    seed(m)
    ent.merge("Veltran Inc", "veltran", reason="r")
    m.append(assertion("Veltran Inc", "hq_city", "tessaly", source="blog"))  # a second source agrees with the registry
    st, _ans = status(m, "veltran", "hq_city")
    # ashford (wire) still disputes it: three reports, two values, no cue to explain either away
    assert st is KernelStatus.UNRESOLVED
    assert m.backend.verify_beliefs(m.reviser).ok and m.backend.verify_log().ok


# --------------------------------------------------------------------------- reversal (fixture ind-09)


def test_reversing_a_merge_restores_the_beliefs_exactly(ms: tuple[Memory, Entities]) -> None:
    m, ent = ms
    seed(m)
    keys = [("veltran", "hq_city"), ("Veltran Inc", "hq_city"), ("alex", "work_city"), ("alex", "employer")]
    pre = {k: core(m, *k) for k in keys}
    rec = ent.merge("Veltran Inc", "veltran", reason="mistake")
    assert core(m, "veltran", "hq_city") != pre[("veltran", "hq_city")]
    ent.unmerge(rec.id, reason="they are two different companies")
    assert ent.members("veltran") == ("veltran",)
    for k in keys:
        assert core(m, *k) == pre[k], k  # segments, pins and dependencies: byte-identical content
    st, ans = status(m, "veltran", "hq_city")
    assert st is KernelStatus.ESTABLISHED and established_value(ans) == "tessaly"  # type: ignore[arg-type]
    assert m.backend.verify_beliefs(m.reviser).ok and m.backend.verify_log().ok


def test_a_merge_and_its_reversal_rewrite_exactly_the_beliefs_pinned_to_the_merge(ms: tuple[Memory, Entities]) -> None:
    m, ent = ms
    ids = seed(m)
    m.append(assertion("Veltran Inc", "affiliations", "x", source="wire"))  # evidence on the alias only
    m.append(assertion("veltran", "employer", "acme", source="blog"))  # on the representative only
    v0 = versions(m)
    rec = ent.merge("Veltran Inc", "veltran", reason="r")
    v1 = versions(m)
    changed = {k for k in v1 if v1[k] != v0.get(k)}
    # the marker's own key, the keys that consumed evidence across the merge, and the derived key reading hq_city
    assert changed == {
        ("Veltran Inc", ENTITY_MERGE_ATTR), ("veltran", "hq_city"), ("veltran", "affiliations"), ("alex", "work_city"),
    }, changed
    pinned = {
        (k.entity, k.attr)
        for k in (Key(entity=e, attr=a) for e, a in v1)
        if any(p.report_id == rec.id for p in m.backend.current_belief(k).pinned)  # type: ignore[union-attr]
    }
    # the beliefs pinned to the merge are exactly the ones the merge rewrote: the class keys that consumed evidence
    # across it, the derived key built on them (it inherits their pins), and the marker's own key
    assert pinned == changed, (pinned, changed)
    assert ids  # (the seed ids are not needed here)
    ent.unmerge(rec.id, reason="mistaken merge")
    v2 = versions(m)
    changed2 = {k for k in v2 if v2[k] != v1.get(k)}
    assert changed2 == {
        ("Veltran Inc", ENTITY_MERGE_ATTR), ("veltran", "hq_city"), ("veltran", "affiliations"), ("alex", "work_city"),
    }, changed2


def test_unmerging_something_that_is_not_an_active_merge_is_refused(ms: tuple[Memory, Entities]) -> None:
    m, ent = ms
    seed(m)
    with pytest.raises(EntitiesError):
        ent.unmerge("01ARZ3NDEKTSV4RRFFQ69G5FAV", reason="nope")
    rec = ent.merge("Veltran Inc", "veltran", reason="r")
    ent.unmerge(rec.id, reason="undo")
    with pytest.raises(EntitiesError):
        ent.unmerge(rec.id, reason="again")


def test_merge_guards(ms: tuple[Memory, Entities]) -> None:
    m, ent = ms
    seed(m)
    with pytest.raises(UnknownEntity):
        ent.merge("Nobody", "veltran", reason="r")
    with pytest.raises(EntitiesError):
        ent.merge("veltran", "veltran", reason="r")
    ent.merge("Veltran Inc", "veltran", reason="r")
    with pytest.raises(EntitiesError):
        ent.merge("veltran", "Veltran Inc", reason="again")  # already one class


def test_chained_merges_and_reversing_the_middle_one(ms: tuple[Memory, Entities]) -> None:
    m, ent = ms
    for name, city, source in (
        ("Veltran Inc", "ashford", "wire"), ("Veltran Corp", "brook", "press"), ("veltran", "tessaly", "registry"),
    ):
        m.append(assertion(name, "hq_city", city, source=source))
    a = ent.merge("Veltran Inc", "Veltran Corp", reason="1")
    b = ent.merge("Veltran Corp", "veltran", reason="2")
    assert ent.members("Veltran Inc") == ("Veltran Corp", "Veltran Inc", "veltran") and ent.canonical("Veltran Inc") == "veltran"
    assert status(m, "veltran", "hq_city")[0] is KernelStatus.UNRESOLVED
    pins = {p.report_id for p in m.backend.current_belief(Key(entity="veltran", attr="hq_city")).pinned}  # type: ignore[union-attr]
    assert {a.id, b.id} <= pins  # evidence from the far alias crossed both merges
    ent.unmerge(a.id, reason="undo the first")  # Veltran Inc leaves; Corp and veltran stay merged
    assert ent.members("veltran") == ("Veltran Corp", "veltran")
    st, _ans = status(m, "veltran", "hq_city")
    assert st is KernelStatus.UNRESOLVED  # brook vs tessaly
    assert status(m, "Veltran Inc", "hq_city")[0] is KernelStatus.ESTABLISHED  # back to its own ashford
    assert m.backend.verify_beliefs(m.reviser).ok


# --------------------------------------------------------------------------- withdrawal across a merge


def test_withdrawing_a_report_on_an_alias_repairs_the_merged_belief_and_what_depends_on_it(ms: tuple[Memory, Entities]) -> None:
    m, ent = ms
    ids = seed(m)
    ent.merge("Veltran Inc", "veltran", reason="r")
    assert status(m, "alex", "work_city")[0] is KernelStatus.UNRESOLVED
    m.withdraw(ids["r3"], source=src("wire"), actor="connector:wire")
    st, ans = status(m, "veltran", "hq_city")
    assert st is KernelStatus.ESTABLISHED and established_value(ans) == "tessaly"  # type: ignore[arg-type]
    wc = status(m, "alex", "work_city")
    assert wc[0] is KernelStatus.ESTABLISHED and established_value(wc[1]) == "tessaly"  # type: ignore[arg-type]
    assert m.backend.verify_beliefs(m.reviser).ok


# --------------------------------------------------------------------------- integrity


def test_hash_chain_and_beliefs_verify_after_merges(ms: tuple[Memory, Entities]) -> None:
    m, ent = ms
    seed(m)
    rec = ent.merge("Veltran Inc", "veltran", reason="r")
    assert m.backend.verify_log().ok and m.backend.verify_beliefs(m.reviser).ok and m.backend.recover().ok
    ent.unmerge(rec.id, reason="u")
    assert m.backend.verify_log().ok and m.backend.verify_beliefs(m.reviser).ok and m.backend.recover().ok


def test_a_reopened_store_keeps_resolving_merged_entities(tmp_path: Path) -> None:
    clock = Clock()
    m = merge_memory(make_backend("sqlite", clock, tmp_path))
    ent = Entities(m)
    seed(m)
    rec = ent.merge("Veltran Inc", "veltran", reason="r")
    pre = core(m, "veltran", "hq_city")
    exported = list(m.backend.export_jsonl())
    assert sum(1 for x in exported if ENTITY_MERGE_ATTR in x) >= 1  # the decision is an ordinary log row: exported with the log
    m.close()

    m2 = merge_memory(make_backend("sqlite", clock, tmp_path))  # a new process: nothing in memory, the schema opts in
    assert m2.pipeline.layer is not None
    ent2 = Entities(m2)
    assert ent2.members("veltran") == ("Veltran Inc", "veltran") and ent2.merges()[0].id == rec.id
    assert status(m2, "Veltran Inc", "hq_city")[0] is KernelStatus.UNRESOLVED  # the alias still reads the class
    assert core(m2, "veltran", "hq_city") == pre
    m2.append(assertion("Veltran Inc", "hq_city", "ashford", source="blog"))  # and new reports keep aggregating
    assert m2.backend.verify_beliefs(m2.reviser).ok


@pytest.mark.parametrize("step", APPEND_STEPS)
def test_a_crash_in_the_middle_of_a_merge_leaves_no_trace_and_the_retry_converges(step: str, tmp_path: Path) -> None:
    clock = Clock()
    crash = {"on": False}

    def fault(name: str) -> None:
        if crash["on"] and name == step:
            raise RuntimeError(f"injected crash at {name}")

    m = merge_memory(make_backend("sqlite", clock, tmp_path, fault=fault))
    ent = Entities(m)
    seed(m)
    head = m.backend.head().lsn
    crash["on"] = True
    with pytest.raises(RuntimeError, match="injected"):
        ent.merge("Veltran Inc", "veltran", reason="r", idempotency_key="merge-1")
    crash["on"] = False
    m.close()

    m2 = merge_memory(make_backend("sqlite", clock, tmp_path))
    ent2 = Entities(m2)
    assert m2.backend.recover().ok
    committed = step == "after_commit"
    assert m2.backend.head().lsn == (head + 1 if committed else head)
    assert ent2.members("veltran") == (("Veltran Inc", "veltran") if committed else ("veltran",))
    if not committed:
        ent2.merge("Veltran Inc", "veltran", reason="r", idempotency_key="merge-1")
    else:  # the retry replays the committed decision instead of duplicating it
        text = encode_marker(MergeOp.MERGE, into="veltran", reason="r", method="manual")
        ent2._append("Veltran Inc", text, "merge-1")  # same idempotency key: a replay, not a second decision
    assert m2.backend.head().lsn == head + 1
    assert ent2.members("veltran") == ("Veltran Inc", "veltran")
    assert status(m2, "veltran", "hq_city")[0] is KernelStatus.UNRESOLVED
    assert m2.backend.verify_beliefs(m2.reviser).ok and m2.backend.verify_log().ok


# --------------------------------------------------------------------------- authority


def marker(actor: str, source_id: str, *, origin: Origin = Origin.EXTERNAL_OBSERVATION, cls: str = "trusted") -> Report:
    return Report(
        key=Key(entity="Veltran Inc", attr=ENTITY_MERGE_ATTR), cue=Cue.ASSERT,
        proposition=MemberProp(value=encode_marker(MergeOp.MERGE, into="veltran", reason="forged", method="manual")),
        source=src(source_id, cls), origin=origin, origin_group=source_id, actor=actor,
    )


@pytest.mark.parametrize(
    ("actor", "source_id", "origin"),
    [
        ("agent:planner", "agent:planner", Origin.EXTERNAL_OBSERVATION),  # an agent principal
        ("connector:press", "connector:press", Origin.EXTERNAL_OBSERVATION),  # a connector is not a merge authority
        ("agent:planner", "system:entity-resolution", Origin.EXTERNAL_OBSERVATION),  # the source id cannot launder an agent actor
        ("system:entity-resolution", "system:entity-resolution", Origin.AGENT_STATEMENT),  # agent-class origin is never admissible
    ],
)
def test_only_a_host_principal_can_merge(ms: tuple[Memory, Entities], actor: str, source_id: str, origin: Origin) -> None:
    m, ent = ms
    seed(m)
    m.append(marker(actor, source_id, origin=origin))
    assert ent.members("veltran") == ("veltran",)
    assert ent.merges() == ()
    assert status(m, "veltran", "hq_city")[0] is KernelStatus.ESTABLISHED  # nothing merged


def test_a_quarantined_host_source_is_not_honoured(ms: tuple[Memory, Entities]) -> None:
    m, ent = ms
    seed(m)
    m.append(marker("system:entity-resolution", "system:entity-resolution", cls="quarantined"))
    assert ent.members("veltran") == ("veltran",)


def test_a_malformed_marker_is_ignored(ms: tuple[Memory, Entities]) -> None:
    m, ent = ms
    seed(m)
    bad = Report(
        key=Key(entity="Veltran Inc", attr=ENTITY_MERGE_ATTR), cue=Cue.ASSERT, proposition=MemberProp(value="not json"),
        source=src("system:entity-resolution", "trusted"), origin=Origin.EXTERNAL_OBSERVATION,
        origin_group="system:entity-resolution", actor="system:entity-resolution",
    )
    m.append(bad)
    assert ent.merges() == ()
    assert decode_marker(bad) is None


def test_a_rejected_marker_is_reported_by_the_host_api(ms: tuple[Memory, Entities]) -> None:
    m, _ = ms
    seed(m)
    from palimem.admission import (
        AdmissionConfig,  # noqa: F401  (documents the dependency: admission decides)
    )

    other = Entities(m, actor="connector:evil", source=src("connector:evil", "trusted"))
    with pytest.raises(MergeRejected):
        other.merge("Veltran Inc", "veltran", reason="r")


def test_the_marker_attribute_is_not_a_queryable_belief_of_an_agent_scope(ms: tuple[Memory, Entities]) -> None:
    m, ent = ms
    seed(m)
    ent.merge("Veltran Inc", "veltran", reason="r")
    ans = m.query(Query(key=Key(entity="Veltran Inc", attr=ENTITY_MERGE_ATTR), profile=m.semantic.profile))
    assert isinstance(ans, Resolved)  # the host can read it (it is an ordinary multi-valued attribute) ...
    # ... and it is never treated as an entity class: querying it must not recurse into the merge layer
    assert ans.justified.key.attr == ENTITY_MERGE_ATTR


# --------------------------------------------------------------------------- documented limits (docs/ENTITIES.md)


def test_a_derived_key_that_names_an_alias_value_follows_the_merge_and_later_reports_on_the_representative(ms: tuple[Memory, Entities]) -> None:
    m, ent = ms
    m.append(assertion("alex", "employer", "Veltran Inc", source="press"))  # the employer is named by the *alias*
    m.append(assertion("Veltran Inc", "hq_city", "ashford", source="wire"))
    m.append(assertion("veltran", "affiliations", "x", source="registry"))
    ent.merge("Veltran Inc", "veltran", reason="r")
    st, ans = status(m, "alex", "work_city")
    assert st is KernelStatus.ESTABLISHED and established_value(ans) == "ashford"  # type: ignore[arg-type]
    # a later report lands on the representative: the rule names the alias, the revision must still reach alex
    m.append(assertion("veltran", "hq_city", "tessaly", source="registry"))
    st2, _ = status(m, "alex", "work_city")
    assert st2 is KernelStatus.UNRESOLVED  # ashford (wire) vs tessaly (registry), seen through the alias value
    assert m.backend.verify_beliefs(m.reviser).ok


@pytest.mark.xfail(strict=True, reason="known gap: admission is per raw key, so a confirmation never crosses a merge")
def test_known_gap_confirmation_does_not_cross_aliases(ms: tuple[Memory, Entities]) -> None:
    from palimem.admission import AdmissionConfig, SourceStatus

    m, ent = ms
    cfg = AdmissionConfig(profile=m.semantic.profile, source_status={"blog": SourceStatus.QUARANTINED})
    m.set_admission(cfg)
    m.append(assertion("Veltran Inc", "hq_city", "ashford", source="blog"))  # quarantined: not admitted on its own
    m.append(assertion("veltran", "hq_city", "ashford", source="wire"))  # an independent report of the same value
    ent.merge("Veltran Inc", "veltran", reason="r")
    # once the names are one entity, the wire report should confirm the blog's; admission still reads two raw keys
    assert len(m.admitted()) == 2
