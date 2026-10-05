"""Typed entity merges (author ruling 2026-10-05, item 4, additive): ``Power.MERGE``, ``MergeMarker`` (payload
version 2, with the version-1 compat reader), ``MergeRecord``, and the host API that returns them."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from palimem.admission import AdmissionConfig
from palimem.compat import schema_from_kernel
from palimem.entities import ENTITY_MERGE_ATTR, Entities, MergeOutcome, MergeRejected
from palimem.entities.registry import decode_marker, encode_marker
from palimem.memory import Memory
from palimem.policy import JUSTIFIED
from palimem.types import (
    AuthorityRule,
    Cue,
    Key,
    KeyScope,
    MemberProp,
    MergeMarker,
    MergeOp,
    MergeRecord,
    Origin,
    Power,
    Profile,
    Report,
    ResolverInfo,
    SemanticConfig,
    Source,
    ValidationError,
    Who,
    WhoKind,
    may_merge,
)
from tests._pipeline_helpers import Clock, assertion, make_backend
from tests.entities._helpers import merge_kernel_schema, merge_setup

R1 = "01JZ0000000000000000000001"
R2 = "01JZ0000000000000000000002"
GRANT = AuthorityRule(
    who=Who(kind=WhoKind.PRINCIPAL, value="connector:ops"), may=(Power.MERGE,), on=KeyScope(attr=ENTITY_MERGE_ATTR),
)


def marker_report(text: str, *, entity: str = "Veltran Inc", actor: str = "system:er", source: str = "system:er") -> Report:
    return Report(
        key=Key(entity=entity, attr=ENTITY_MERGE_ATTR), cue=Cue.ASSERT, proposition=MemberProp(value=text),
        source=Source(id=source, cls="trusted"), origin=Origin.EXTERNAL_OBSERVATION, origin_group="er", actor=actor,
    )


def seeded(backend_kind: str = "memory", *, grant: bool = False) -> tuple[Memory, Entities]:
    if not grant:
        m, ent = merge_setup(make_backend(backend_kind, Clock()))
    else:
        ks = merge_kernel_schema()
        base = schema_from_kernel(ks)
        schema = replace(base, attrs=tuple(replace(a, authority=(GRANT,)) if a.name == ENTITY_MERGE_ATTR else a for a in base.attrs))
        m = Memory(
            make_backend(backend_kind, Clock()), schema, kernel_schema=ks, entities=None,
            semantic=SemanticConfig(self_update=False, profile=Profile.OPEN_WORLD),
            admission=AdmissionConfig(profile=Profile.OPEN_WORLD), policy=JUSTIFIED,
        )
        ent = Entities(m, actor="connector:ops", source=Source(id="connector:ops", cls="trusted"), origin_group="ops")
    m.append(assertion("veltran", "hq_city", "tessaly", source="registry"))
    m.append(assertion("Veltran Inc", "hq_city", "ashford", source="wire"))
    return m, ent


# --------------------------------------------------------------------------- the typed payload


def test_marker_v2_round_trips_canonically() -> None:
    mk = MergeMarker(op=MergeOp.MERGE, into="acme", reason="same registry id", resolver=ResolverInfo(method="lexical", score=0.91, version="2"))
    body = json.loads(mk.to_text())
    assert body == {"v": 2, "op": "merge", "into": "acme", "reason": "same registry id",
                    "resolver": {"method": "lexical", "score": 0.91, "version": "2"}}
    assert MergeMarker.from_text(mk.to_text()) == mk
    un = MergeMarker(op=MergeOp.UNMERGE, target=R1, reason="false merge")
    assert MergeMarker.from_text(un.to_text()) == un
    assert json.loads(un.to_text())["resolver"] == {"method": "manual"}


def test_marker_v1_payload_is_still_read_and_mapped_onto_the_typed_form() -> None:
    legacy = '{"into":"acme","method":"lexical","op":"merge","reason":"same","score":0.5,"v":1}'
    mk = MergeMarker.from_text(legacy)
    assert mk == MergeMarker(op=MergeOp.MERGE, into="acme", reason="same", resolver=ResolverInfo(method="lexical", score=0.5))
    assert json.loads(mk.to_text())["v"] == 2  # writers emit version 2
    legacy_un = f'{{"op":"unmerge","reason":"r","method":"manual","target":"{R1}","v":1}}'
    assert MergeMarker.from_text(legacy_un).target == R1


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"v":3,"op":"merge","into":"x","reason":"r","resolver":{"method":"m"}}',  # an unknown version
        '{"v":2,"op":"merge","into":"x","reason":"r","method":"m"}',  # v2 without the typed resolver
        '{"v":2,"op":"merge","reason":"r","resolver":{"method":"m"}}',  # no `into`
        '{"v":2,"op":"merge","into":"x","target":"01JZ0000000000000000000001","reason":"r","resolver":{"method":"m"}}',
        '{"v":2,"op":"unmerge","into":"x","reason":"r","resolver":{"method":"m"}}',  # an unmerge naming `into`
        '{"v":2,"op":"frobnicate","reason":"r","resolver":{"method":"m"}}',
        '{"v":2,"op":"merge","into":"x","reason":"r","resolver":{"method":"m"},"extra":1}',  # unknown members are rejected
        '{"v":1,"op":"merge","reason":"r","method":"m"}',
    ],
)
def test_marker_decoding_is_strict(text: str) -> None:
    with pytest.raises(ValidationError):
        MergeMarker.from_text(text)
    assert decode_marker(marker_report(text)) is None  # the registry ignores a malformed marker, never raises


def test_registry_encoder_emits_the_typed_payload_and_reads_both_versions() -> None:
    text = encode_marker(MergeOp.MERGE, into="Real", reason="same", method="lexical", score=0.91, resolver_version="3")
    mk = decode_marker(marker_report(text))
    assert mk is not None and mk.resolver == ResolverInfo(method="lexical", score=0.91, version="3")
    old = decode_marker(marker_report('{"v":1,"op":"merge","into":"Real","reason":"same","method":"lexical","score":0.91}'))
    assert old is not None and old.resolver.score == 0.91 and old.resolver.version is None


# --------------------------------------------------------------------------- the typed record


def test_merge_record_validation() -> None:
    ok = MergeRecord(id=R1, members=("a", "b"), representative="a", reason="r", resolver=ResolverInfo(), admission_version=1)
    assert MergeRecord.from_json(ok.to_json()) == ok
    with pytest.raises(ValidationError):
        replace(ok, members=("a",))  # a merge joins at least two entities
    with pytest.raises(ValidationError):
        replace(ok, members=("b", "a"))  # sorted, canonical
    with pytest.raises(ValidationError):
        replace(ok, representative="z")
    with pytest.raises(ValidationError):
        replace(ok, reversed_by=R1)  # a merge cannot reverse itself
    with pytest.raises(ValidationError):
        replace(ok, admission_version=0)
    assert replace(ok, reversed_by=R2).reversed_by == R2


# --------------------------------------------------------------------------- the merge power


def test_merge_is_granted_by_identity_and_never_to_an_agent() -> None:
    with pytest.raises(ValidationError, match="never be granted 'merge'"):
        AuthorityRule(who=Who(kind=WhoKind.PRINCIPAL, value="agent:planner"), may=(Power.MERGE,))
    with pytest.raises(ValidationError, match="never be granted 'merge'"):
        AuthorityRule.from_dict({"who": {"kind": "principal", "value": "agent:x"}, "may": ["merge"]})
    with pytest.raises(ValidationError, match="on its own"):
        AuthorityRule(who=Who(kind=WhoKind.PRINCIPAL, value="user:alice"), may=(Power.MERGE, Power.WITHDRAW))
    with pytest.raises(ValidationError, match="named principal"):
        AuthorityRule(who=Who(kind=WhoKind.TARGET_SOURCE), may=(Power.MERGE,))
    with pytest.raises(ValidationError, match="no target reports"):
        AuthorityRule(who=Who(kind=WhoKind.ANY), may=(Power.MERGE,), over_origins=(Origin.EXTERNAL_OBSERVATION,))
    ok = AuthorityRule(who=Who(kind=WhoKind.PRINCIPAL, value="user:alice"), may=(Power.MERGE,))
    assert AuthorityRule.from_json(ok.to_json()) == ok


def test_may_merge_defaults_and_grants() -> None:
    assert may_merge("system:er") and may_merge("user:alice")  # the host's own decision, by default
    assert not may_merge("agent:planner")
    assert not may_merge("connector:ops")  # needs an explicit grant
    assert may_merge("connector:ops", (GRANT,), key=Key(entity="acme", attr=ENTITY_MERGE_ATTR))
    assert not may_merge("connector:other", (GRANT,))  # the grant names one principal
    assert not may_merge("connector:ops", (GRANT,), key=Key(entity="acme", attr="employer"))  # a different key scope
    any_rule = AuthorityRule(who=Who(kind=WhoKind.ANY), may=(Power.MERGE,))
    assert may_merge("connector:anyone", (any_rule,)) and not may_merge("agent:planner", (any_rule,))


# --------------------------------------------------------------------------- the host API


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_records_carry_admission_version_resolver_and_reversal(backend: str) -> None:
    m, ent = seeded(backend)
    out = ent.merge("Veltran Inc", "veltran", reason="same registry id", method="lexical", score=0.91)
    assert isinstance(out, MergeOutcome) and out.decision.admission_version == 1
    (rec,) = ent.records()
    assert isinstance(rec, MergeRecord)
    assert rec.id == out.id and rec.members == ("Veltran Inc", "veltran") and rec.representative == "veltran"
    assert rec.resolver == ResolverInfo(method="lexical", score=0.91) and rec.admission_version == 1 and rec.reversed_by is None
    undo = ent.unmerge(out.id, reason="two different companies")
    (rec2,) = ent.records()
    assert rec2.reversed_by == undo.id and rec2.id == out.id
    # as of the log position before the reversal the merge is active
    before = m.lsn_of(None) - 1
    assert ent.records(as_of=before)[0].reversed_by is None
    assert json.loads(MergeRecord.from_json(rec2.to_json()).to_json()) == rec2.to_dict()


def test_a_log_written_with_the_version_1_payload_still_loads() -> None:
    m, ent = seeded()
    v1 = '{"into":"veltran","method":"lexical","op":"merge","reason":"same registry id","score":0.5,"v":1}'
    res = m.append(marker_report(v1))
    assert res.entry is not None
    (dec,) = ent.merges()
    assert dec.into == "veltran" and dec.method == "lexical" and dec.score == 0.5
    (rec,) = ent.records()
    assert rec.resolver == ResolverInfo(method="lexical", score=0.5) and rec.members == ("Veltran Inc", "veltran")
    assert m.backend.verify_log().ok  # nothing about the stored bytes changed


def test_a_connector_merges_only_with_an_explicit_grant() -> None:
    m, _ = seeded(grant=False)
    ent_conn = Entities(m, actor="connector:ops", source=Source(id="connector:ops", cls="trusted"), origin_group="ops")
    with pytest.raises(MergeRejected):
        ent_conn.merge("Veltran Inc", "veltran", reason="no grant")
    assert ent_conn.merges() == ()

    _, ent2 = seeded(grant=True)
    out = ent2.merge("Veltran Inc", "veltran", reason="granted by the schema")
    assert out.decision.proposer == "connector:ops"
    (rec,) = ent2.records()
    assert rec.members == ("Veltran Inc", "veltran")


def test_an_agent_marker_is_still_ignored_even_when_the_schema_grants_merge_to_others() -> None:
    m, ent = seeded(grant=True)
    forged = encode_marker(MergeOp.MERGE, into="veltran", reason="forged")
    m.append(marker_report(forged, actor="agent:a1", source="agent-box"))
    assert ent.merges() == () and ent.records() == ()
