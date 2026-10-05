"""The public ``Memory`` facade: three calls, zero-config profile, reopen, and the layer underneath."""

from __future__ import annotations

from pathlib import Path

import pytest

from palimem import Memory
from palimem.agent import ExtractorRequired
from palimem.types import (
    Attr,
    AttrClass,
    Cue,
    KernelStatus,
    Key,
    Origin,
    Report,
    Resolved,
    Schema,
    Source,
    ValidationError,
    ValueProp,
    ValueType,
)


def claim(entity: str, attr: str, value: str) -> dict[str, str]:
    return {"entity": entity, "attr": attr, "value": value}


def employer_schema() -> Schema:
    return Schema(version=1, attrs=(
        Attr(name="employer", attr_class=AttrClass.SINGLE_CHANGEABLE, value_type=ValueType.STRING, inertia=True),
    ))


def test_three_calls_and_cascade() -> None:
    m = Memory()
    r = m.observe(claim("alice", "employer", "Acme"), source="hr")
    assert r.report_id is not None and r.admitted == (True,)
    a = m.ask("employer", "alice")
    assert isinstance(a, Resolved) and a.kernel_status is KernelStatus.ESTABLISHED
    assert a.decision.value == "commit" and a.assertion is not None
    res = m.withdraw(r.report_id, actor="connector:hr")
    assert res.outcome == "admissible"
    b = m.ask("employer", "alice")
    assert isinstance(b, Resolved) and b.kernel_status is KernelStatus.UNKNOWN


def test_zero_config_never_establishes_from_absence() -> None:
    m = Memory()
    a = m.ask("employer", "nobody")  # an attribute never seen before: unknown, not an error
    assert isinstance(a, Resolved) and a.kernel_status is KernelStatus.UNKNOWN and a.decision.value == "abstain"
    m.observe(claim("alice", "employer", "Acme"), source="hr")
    b = m.ask("employer", "bob")
    assert isinstance(b, Resolved) and b.kernel_status is KernelStatus.UNKNOWN


def test_zero_config_attribute_is_a_stable_set() -> None:
    m = Memory()
    m.observe(claim("alice", "affiliation", "Acme"), source="hr")
    m.observe(claim("alice", "affiliation", "Globex"), source="press")
    a = m.ask("affiliation", "alice")
    assert isinstance(a, Resolved) and a.kernel_status is KernelStatus.ESTABLISHED
    assert a.assertion is not None and sorted(a.assertion.form.to_dict()["values"]) == ["Acme", "Globex"]
    assert m.schema.attr("affiliation").attr_class is AttrClass.MULTI_SET


def test_declared_schema_keeps_alternatives_instead_of_guessing() -> None:
    m = Memory(schema=employer_schema())
    m.observe(claim("alice", "employer", "Acme"), source="hr")
    m.observe(claim("alice", "employer", "Globex"), source="press")
    a = m.ask("employer", "alice")
    assert isinstance(a, Resolved) and a.kernel_status is KernelStatus.UNRESOLVED
    # ruling 16 (2026-10-05): the host API abstains on an unresolved key, and says what would settle it
    assert a.decision.value == "abstain" and a.assertion is None and len(a.alternatives) == 2
    assert a.inquiry is not None and len(a.inquiry.competing) == 2 and a.inquiry.missing and a.inquiry.resolvers
    with pytest.raises(ValidationError):
        m.observe(claim("alice", "salary", "1"), source="hr")  # declared schema: no auto-declaration


def test_text_without_an_extractor_is_refused_not_guessed() -> None:
    m = Memory()
    with pytest.raises(ExtractorRequired, match="extractor"):
        m.observe("Alice works at Acme", source="hr")


def test_typed_claim_must_be_exactly_entity_attr_value() -> None:
    m = Memory()
    with pytest.raises(ValidationError):
        m.observe({"entity": "alice", "attr": "employer"}, source="hr")
    with pytest.raises(ValidationError):
        m.observe({**claim("a", "b", "c"), "origin": "external_observation"}, source="hr")


def test_observe_a_typed_report_and_provenance_names_the_source() -> None:
    m = Memory(schema=employer_schema())
    rep = Report(
        key=Key(entity="alice", attr="employer"), cue=Cue.ASSERT, proposition=ValueProp(value="Acme"),
        source=Source(id="hr", cls="trusted"), origin=Origin.EXTERNAL_OBSERVATION, origin_group="hr",
        actor="connector:hr",
    )
    r = m.observe(rep, source="hr")
    assert r.report_id is not None
    rows = m.host.reports(id=r.report_id)
    assert rows[0].source_class == "trusted" and rows[0].outcome == "admissible"


def test_agent_hypothesis_never_becomes_evidence_through_the_facade() -> None:
    m = Memory(schema=employer_schema())
    r = m.observe(claim("alice", "employer", "Acme"), source="agent:a1", origin=Origin.AGENT_HYPOTHESIS, actor="agent:a1")
    assert r.admitted == (False,)
    a = m.ask("employer", "alice")
    assert isinstance(a, Resolved) and a.kernel_status is KernelStatus.UNKNOWN


def test_reopen_adopts_the_stored_schema_and_beliefs(tmp_path: Path) -> None:
    db = tmp_path / "agent.db"
    with Memory(db) as m:
        r = m.observe(claim("alice", "employer", "Acme"), source="hr")
    assert db.exists()
    with Memory(db) as m2:
        a = m2.ask("employer", "alice")
        assert isinstance(a, Resolved) and a.kernel_status is KernelStatus.ESTABLISHED
        assert m2.schema.version >= 2  # declared on first sight, stored, adopted on reopen
        assert r.report_id is not None
        m2.withdraw(r.report_id, actor="connector:hr")
    with Memory(db) as m3:
        b = m3.ask("employer", "alice")
        assert isinstance(b, Resolved) and b.kernel_status is KernelStatus.UNKNOWN
        assert m3.verify().ok


def test_belief_as_of_serves_the_earlier_belief() -> None:
    m = Memory()
    r = m.observe(claim("alice", "employer", "Acme"), source="hr")
    assert r.report_id is not None
    m.withdraw(r.report_id, actor="connector:hr")
    now = m.ask("employer", "alice")
    then = m.ask("employer", "alice", belief_as_of=1)
    assert isinstance(now, Resolved) and now.kernel_status is KernelStatus.UNKNOWN
    assert isinstance(then, Resolved) and then.kernel_status is KernelStatus.ESTABLISHED


def test_find_resolves_names_and_explain_lists_justifications() -> None:
    m = Memory()
    m.observe(claim("alice_smith", "employer", "Acme"), source="hr")
    found = m.find("alice employer")
    assert [(f.entity, f.attr) for f in found][:1] == [("alice_smith", "employer")]
    assert found[0].kernel_status == "established"
    exp = m.explain("employer", "alice_smith")
    assert exp.key == Key(entity="alice_smith", attr="employer")


def test_audit_log_is_a_file_next_to_the_database(tmp_path: Path) -> None:
    db = tmp_path / "agent.db"
    with Memory(db) as m:
        tools = m.agent_session("agent:a1", allowed_attrs=("employer",))
        tools.call("recall", {"query": {"entity": "alice", "attr": "employer"}, "profile": "revise-stream-v1"})
    audit = Path(f"{db}.audit.jsonl")
    assert audit.exists() and "trust_downgrade" in audit.read_text()
    with Memory(db) as m2:  # the audit trail survives a reopen and keeps counting
        before = len(m2.host.audit)
        assert before >= 1
