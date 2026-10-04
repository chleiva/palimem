"""Backend interface contract (T-C1/T-C2/T-C3): every test runs on the in-memory and the SQLite backend."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from fakes import DERIVED_PREFIX, FakeAdmitter, FakeReviser, make_report

from palimem.store import (
    Backend,
    CapabilityError,
    IdempotencyConflict,
    InMemoryBackend,
    InputKind,
    InvalidRevision,
    StoreError,
    StoreView,
)
from palimem.types import (
    Attr,
    AttrClass,
    Cue,
    KernelStatus,
    Key,
    Rule,
    Schema,
    ValueForm,
    ValueType,
)

K = Key(entity="alice", attr="employer")


def append(h, report, idem, admitter=None, reviser=None):  # type: ignore[no-untyped-def]
    return h.backend.append(report, idempotency_key=idem, admitter=admitter or FakeAdmitter(), reviser=reviser or FakeReviser())


def _attr(name: str, cls: AttrClass, rule: Rule | None = None) -> Attr:
    return Attr(name=name, attr_class=cls, value_type=ValueType.STRING, inertia=cls is AttrClass.SINGLE_CHANGEABLE, rule=rule)


def schema_v(version: int) -> Schema:
    return Schema(
        version=version,
        attrs=(
            _attr("employer", AttrClass.SINGLE_CHANGEABLE),
            _attr("work_city", AttrClass.DERIVED, rule=Rule(reads=("employer",), fn="hq_city")),
        ),
    )


def test_is_a_backend(h) -> None:  # type: ignore[no-untyped-def]
    assert isinstance(h.backend, Backend)
    assert isinstance(h.backend, StoreView)
    assert "verify_log" in h.backend.capabilities and "export_head" in h.backend.capabilities


def test_append_assigns_lsn_id_and_chain(h) -> None:  # type: ignore[no-untyped-def]
    r1 = append(h, make_report(value="acme"), "k1")
    h.clock.tick()
    r2 = append(h, make_report(value="globex", source="press"), "k2")
    assert (r1.entry.lsn, r2.entry.lsn) == (1, 2)
    assert r1.entry.report.id and r2.entry.report.id and r1.entry.report.id != r2.entry.report.id
    assert r2.entry.prev_hash == r1.entry.entry_hash  # chained, on the log row
    assert r1.generation == 1 and r2.generation == 2
    assert not r1.replayed
    assert r1.entry.report.to_dict().keys() >= {"key", "cue"}
    assert "entry_hash" not in r1.entry.report.to_dict()  # the chain is not a Report field
    head = h.backend.head()
    assert (head.lsn, head.generation, head.admission_seq) == (2, 2, 2)


def test_beliefs_versions_and_current_index(h) -> None:  # type: ignore[no-untyped-def]
    a = append(h, make_report(value="acme"), "k1")
    b = append(h, make_report(value="globex", source="press"), "k2")
    assert [x.version for x in a.beliefs] == [1] and [x.version for x in b.beliefs] == [2]
    cur = h.backend.current_belief(K)
    assert cur is not None and cur.version == 2
    assert cur.segments[0].kernel_status is KernelStatus.UNRESOLVED
    v1 = h.backend.belief_version(K, 1)
    assert v1 is not None and v1.segments[0].established is not None and isinstance(v1.segments[0].established.form, ValueForm)
    assert h.backend.current_belief(Key(entity="bob", attr="employer")) is None


def test_idempotent_retry_replays_without_a_second_row(h) -> None:  # type: ignore[no-untyped-def]
    rep = make_report(value="acme")
    admitter, reviser = FakeAdmitter(), FakeReviser()
    first = append(h, rep, "same-key", admitter, reviser)
    again = append(h, rep, "same-key", admitter, reviser)
    assert again.replayed and again.entry == first.entry
    assert again.admissions == first.admissions and again.beliefs == first.beliefs
    assert admitter.calls == 1 and reviser.calls == 1
    assert h.backend.head().lsn == 1


def test_idempotency_key_reused_for_a_different_report_conflicts(h) -> None:  # type: ignore[no-untyped-def]
    append(h, make_report(value="acme"), "k")
    with pytest.raises(IdempotencyConflict):
        append(h, make_report(value="globex"), "k")
    assert h.backend.head().lsn == 1


def test_inputs_are_validated(h) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        append(h, make_report(), "")
    assigned = append(h, make_report(), "ok").entry.report
    with pytest.raises(ValueError):
        append(h, assigned, "again")  # ids are assigned by the log


def test_belief_as_of_accepts_an_lsn_or_a_timestamp(h) -> None:  # type: ignore[no-untyped-def]
    t1 = h.clock.now
    append(h, make_report(value="acme"), "k1")
    t2 = h.clock.tick(10)
    append(h, make_report(value="globex", source="press"), "k2")
    t3 = h.clock.tick(10)
    append(h, make_report(value="initech", source="blog"), "k3")
    b = h.backend
    assert [b.belief_at(K, n).version if b.belief_at(K, n) else None for n in (0, 1, 2, 3, 99)] == [None, 1, 2, 3, 3]
    assert b.lsn_at(t1 - timedelta(seconds=1)) == 0
    assert (b.lsn_at(t1), b.lsn_at(t2), b.lsn_at(t3)) == (1, 2, 3)
    assert b.lsn_at(t2 - timedelta(seconds=1)) == 1  # a timestamp maps to the last LSN at or before it
    at = b.belief_at(K, t2 + timedelta(seconds=5))
    assert at is not None and at.version == 2
    assert b.belief_at(K, t1 - timedelta(days=1)) is None
    with pytest.raises(TypeError):
        b.belief_at(K, True)


def test_recorded_at_is_clamped_non_decreasing_on_clock_skew(h) -> None:  # type: ignore[no-untyped-def]
    a = append(h, make_report(value="acme"), "k1")
    h.clock.now -= timedelta(hours=1)  # the clock goes backwards
    b = append(h, make_report(value="globex", source="press"), "k2")
    assert b.entry.recorded_at >= a.entry.recorded_at
    assert h.backend.lsn_at(a.entry.recorded_at) == 2  # both at the same instant: the later LSN wins, deterministically


def test_scan_entries_and_admissions(h) -> None:  # type: ignore[no-untyped-def]
    append(h, make_report("alice", "employer", "acme"), "k1")
    append(h, make_report("bob", "employer", "globex", source="press"), "k2")
    append(h, make_report("alice", "employer", "initech", source="blog"), "k3")
    b = h.backend
    assert [e.lsn for e in b.scan()] == [1, 2, 3]  # type: ignore[union-attr]
    assert [e.lsn for e in b.scan(2, 3)] == [2, 3]  # type: ignore[union-attr]
    assert [e.lsn for e in b.entries_for_key(K)] == [1, 3]
    assert [e.lsn for e in b.entries_for_key(K, to_lsn=2)] == [1]
    ids = [e.report.id for e in b.entries_for_key(K)]
    assert all(b.admissions_for_report(i)[0].report_id == i for i in ids if i)  # type: ignore[arg-type]
    assert len(b.admissions_for_key(K)) == 2
    assert b.get_entry(ids[0]).lsn == 1  # type: ignore[union-attr,arg-type]
    assert b.get_entry("0" * 26) is None


def test_dependency_lookup_and_cascade(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(schema_v(1))
    assert b.attr_dependents("employer") == ("work_city",)
    assert b.attr_dependents("work_city") == ()
    r = append(h, make_report(value="acme"), "k1")
    derived = Key(entity="alice", attr="work_city")
    assert {x.key for x in r.beliefs} == {K, derived}
    assert b.key_dependents(K) == (derived,)
    d1 = b.current_belief(derived)
    assert d1 is not None and d1.depends_on[0].key == K
    assert d1.segments[0].established is not None
    assert d1.segments[0].established.form == ValueForm(value=DERIVED_PREFIX + "acme")
    # withdrawing the only support repairs the derived belief in the same revision
    h.clock.tick()
    w = append(h, make_report(cue=Cue.WITHDRAW, target=r.entry.report.id, source="registry"), "k2")  # type: ignore[arg-type]
    assert {x.key for x in w.beliefs} == {K, derived}
    assert b.current_belief(K).segments[0].kernel_status is KernelStatus.UNKNOWN  # type: ignore[union-attr]
    assert b.current_belief(derived).segments[0].kernel_status is KernelStatus.UNKNOWN  # type: ignore[union-attr]
    assert b.belief_at(derived, 1).segments[0].kernel_status is KernelStatus.ESTABLISHED  # type: ignore[union-attr]


def test_versioned_inputs_apply_from_the_next_append(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    assert b.schema() is None and b.input_at(InputKind.POLICY) is None
    b.put_schema(schema_v(1))
    append(h, make_report(value="acme"), "k1")
    b.put_schema(schema_v(2))
    append(h, make_report(value="globex", source="press"), "k2")
    assert b.schema().version == 2  # type: ignore[union-attr]
    assert b.schema(as_of=1).version == 1  # type: ignore[union-attr]  # LSN 1 was decided under schema v1
    assert b.schema(as_of=2).version == 2  # type: ignore[union-attr]
    with pytest.raises(StoreError):
        b.put_schema(schema_v(2))  # versions only increase
    b.put_input(InputKind.POLICY, 1, {"abstain_threshold": 0.5})
    b.put_input(InputKind.ADMISSION, 1, {"grants": []})
    assert b.input_at(InputKind.POLICY) == (1, {"abstain_threshold": 0.5})
    with pytest.raises(ValueError):
        b.put_input(InputKind.SCHEMA, 3, {})
    assert b.attr_dependents("employer", as_of=0) == ()  # no schema was in force before LSN 1


class _BadVersion(FakeReviser):
    def revise(self, ctx):  # type: ignore[no-untyped-def]
        out = list(super().revise(ctx))
        out[0] = replace(out[0], version=out[0].version + 1)
        return out


class _NoRecord(FakeAdmitter):
    def admit(self, ctx):  # type: ignore[no-untyped-def]
        return ()


def test_invalid_revision_or_admission_writes_nothing(h) -> None:  # type: ignore[no-untyped-def]
    append(h, make_report(value="acme"), "k1")
    before = h.backend.head()
    with pytest.raises(InvalidRevision):
        append(h, make_report(value="globex", source="press"), "k2", reviser=_BadVersion())
    with pytest.raises(InvalidRevision):
        append(h, make_report(value="globex", source="press"), "k2", admitter=_NoRecord())
    assert h.backend.head() == before
    assert h.backend.recover().ok
    ok = append(h, make_report(value="globex", source="press"), "k2")  # the key is not burnt by a failed attempt
    assert ok.entry.lsn == 2 and not ok.replayed


def test_chainless_backend_is_contract_conformant() -> None:
    b = InMemoryBackend(chain=False)
    admitter, reviser = FakeAdmitter(), FakeReviser()
    r = b.append(make_report(value="acme"), idempotency_key="k", admitter=admitter, reviser=reviser)
    assert r.entry.entry_hash is None and r.entry.prev_hash is None
    assert b.current_belief(K) is not None and b.recover().ok
    assert b.append(make_report(value="acme"), idempotency_key="k", admitter=admitter, reviser=reviser).replayed
    assert "verify_log" not in b.capabilities
    with pytest.raises(CapabilityError):
        b.verify_log()
    with pytest.raises(CapabilityError):
        b.export_head()
