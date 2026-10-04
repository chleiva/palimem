"""Notification outbox and subscriptions (T-C5): written inside the revision transaction, at-least-once, stable event ids.

Rows: 'Crash after a belief change commits, before the subscriber is notified -> the notification is re-delivered
from the outbox on recovery with the same event id; the subscriber's idempotent handler produces no duplicate effect'.
"""

from __future__ import annotations

import pytest
from chain_fakes import ChainReviser, chain_schema
from fakes import FakeAdmitter, make_report

from palimem.store import APPEND_STEPS, OutboxEvent
from palimem.store.engine import Engine
from palimem.types import Cue, KernelStatus, Key

E = "alice"
A = Key(entity=E, attr="employer")
B = Key(entity=E, attr="work_city")
C = Key(entity=E, attr="tax_city")


class Boom(Exception):
    pass


def add(b, report, idem, reviser=None):  # type: ignore[no-untyped-def]
    return b.append(report, idempotency_key=idem, admitter=FakeAdmitter(), reviser=reviser or ChainReviser())


def employer(value: str, source: str = "s1"):  # type: ignore[no-untyped-def]
    return make_report(E, "employer", value, source=source)


def test_event_is_written_with_the_append_and_carries_old_and_new_views(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema())
    b.subscribe("plan-1", [A, C])
    assert b.subscriptions("plan-1") == (A, C)
    add(b, employer("acme"), "k1")
    events = {e.key: e for e in b.pending_events()}
    assert set(events) == {A, C}
    ea = events[A]
    assert ea.plan_id == "plan-1" and ea.lsn == 1 and (ea.old_version, ea.new_version) == (None, 1)
    assert ea.event_id == Engine.event_id(A, None, 1)  # hash(key, old version, new version)
    assert ea.old_view is None and ea.new_view is not None and ea.new_view.version == 1
    assert ea.new_view.segment.kernel_status is KernelStatus.ESTABLISHED and not ea.redacted

    add(b, employer("globex", "s2"), "k2")
    second = {e.key: e for e in b.pending_events() if e.new_version == 2}
    e2 = second[A]
    assert e2.event_id == Engine.event_id(A, 1, 2) and e2.event_id != ea.event_id
    assert e2.old_view is not None and e2.old_view.version == 1 and e2.new_view is not None and e2.new_view.version == 2
    assert e2.new_view.segment.kernel_status is KernelStatus.UNRESOLVED


def test_a_derived_key_and_an_invalidation_both_notify(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema())
    first = add(b, employer("acme"), "k1")
    b.subscribe("plan", [C])
    b.subscribe("plan2", [A])
    add(b, employer("acme", "s2"), "k2")
    got = {e.plan_id: e for e in b.pending_events()}
    assert got["plan"].key == C and got["plan"].new_view is not None  # a derived belief changed version
    assert got["plan"].new_view.segment.established is not None

    rid = first.entry.report.id
    add(b, make_report(E, "employer", cue=Cue.WITHDRAW, target=rid, source="s1"), "k3")  # invalidation of one support
    ev = [e for e in b.pending_events() if e.plan_id == "plan2" and e.new_version == 3]
    assert len(ev) == 1 and ev[0].old_version == 2


def test_delivery_is_at_least_once_and_the_event_id_is_stable(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema())
    b.subscribe("plan", [A])
    add(b, employer("acme"), "k1")
    seen: list[OutboxEvent] = []

    def crash(step: str) -> None:
        if step == "after_handler":
            raise Boom

    b.set_fault_hook(crash)
    with pytest.raises(Boom):  # the handler ran, the acknowledgement was never recorded
        b.deliver(seen.append)
    b.set_fault_hook(None)
    assert len(seen) == 1 and len(b.pending_events()) == 1  # still pending

    assert b.deliver(seen.append) == 1  # redelivered
    assert len(seen) == 2 and seen[0].event_id == seen[1].event_id  # same event id
    assert b.pending_events() == () and b.deliver(seen.append) == 0

    effects: set[str] = set()  # an idempotent subscriber: processes on the event id
    for ev in seen:
        effects.add(ev.event_id)
    assert len(effects) == 1  # no duplicate effect


def test_two_plans_on_one_key_get_one_row_each_and_are_acknowledged_separately(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema())
    b.subscribe("p1", [A])
    b.subscribe("p2", [A])
    add(b, employer("acme"), "k1")
    pend = b.pending_events()
    assert sorted(e.plan_id for e in pend) == ["p1", "p2"] and len({e.event_id for e in pend}) == 1
    b.ack_event(pend[0].event_id, "p1")
    assert [e.plan_id for e in b.pending_events()] == ["p2"]


def test_a_committed_change_is_notified_after_a_restart(h) -> None:  # type: ignore[no-untyped-def]
    """The belief change commits; the process dies before anyone is told; on recovery the outbox delivers it."""
    if h.kind != "sqlite":
        pytest.skip("restart needs a file")
    b = h.backend
    b.put_schema(chain_schema())
    b.subscribe("plan", [A])
    add(b, employer("acme"), "k1")
    expected = b.pending_events()[0].event_id
    b.close()
    b2 = h.make()
    assert b2.recover().ok
    got: list[OutboxEvent] = []
    assert b2.deliver(got.append) == 1 and got[0].event_id == expected  # same event id after recovery
    assert b2.pending_events() == ()
    b2.close()


@pytest.mark.parametrize("step", [s for s in APPEND_STEPS if s != "after_commit"])
def test_no_event_without_the_belief_change(h, step: str) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema())
    b.subscribe("plan", [A])

    def crash(s: str) -> None:
        if s == step:
            raise Boom

    b.set_fault_hook(crash)
    with pytest.raises(Boom):
        add(b, employer("acme"), "k1")
    b.set_fault_hook(None)
    assert b.storage.outbox_all() == [] and b.current_belief(A) is None  # the event and the change are one unit
    add(b, employer("acme"), "k1")
    assert len(b.pending_events()) == 1  # exactly one after the retry


def test_a_crash_after_commit_keeps_exactly_one_event(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema())
    b.subscribe("plan", [A])

    def crash(s: str) -> None:
        if s == "after_commit":
            raise Boom

    b.set_fault_hook(crash)
    with pytest.raises(Boom):
        add(b, employer("acme"), "k1")
    b.set_fault_hook(None)
    assert len(b.pending_events()) == 1
    assert add(b, employer("acme"), "k1").replayed and len(b.pending_events()) == 1  # a retry adds no second event


def test_unsubscribe_and_idempotent_subscribe(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema())
    b.subscribe("plan", [A])
    b.subscribe("plan", [A, C])
    assert b.subscriptions("plan") == (A, C)
    b.unsubscribe("plan")
    assert b.subscriptions("plan") == ()
    add(b, employer("acme"), "k1")
    assert b.pending_events() == ()
    with pytest.raises(ValueError):
        b.subscribe("", [A])


def test_a_completion_job_notifies_when_it_stamps_a_key(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema())
    b.subscribe("plan", [C])
    add(b, employer("acme"), "k1")
    add(b, employer("globex", "s2"), "k2", ChainReviser(skip={"tax_city"}))
    before = [e.new_version for e in b.pending_events()]
    assert before == [1]  # C was not revised in the second append: no event for it yet
    b.complete_pending(ChainReviser())
    after = [(e.old_version, e.new_version) for e in b.pending_events()]
    assert after == [(None, 1), (1, 2)]  # the stamped version is announced once, when it exists
    assert b.read_belief(B) is not None
