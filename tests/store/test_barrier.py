"""Generation barrier (T-C4): the barrier rows of the 23-row acceptance table, plus the author's concern H4
(a traversal overflow is scoped to an attribute component, not the whole store).

Every test runs on the in-memory and the SQLite backend.
"""

from __future__ import annotations

import json

import pytest
from chain_fakes import ChainReviser, SchemalessReviser, chain_schema
from fakes import FakeAdmitter, make_report

from palimem.store import barrier
from palimem.store.backend import LimitedRead
from palimem.types import (
    Belief,
    KernelStatus,
    Key,
    ResourceLimited,
    ResourceLimitedReason,
)

E = "alice"
A = Key(entity=E, attr="employer")
B = Key(entity=E, attr="work_city")
C = Key(entity=E, attr="tax_city")
D = Key(entity=E, attr="tax_band")
N = Key(entity=E, attr="nickname")
FAR = 2**62


class Boom(Exception):
    pass


def add(b, report, idem, reviser):  # type: ignore[no-untyped-def]
    return b.append(report, idempotency_key=idem, admitter=FakeAdmitter(), reviser=reviser)


def employer(value: str, source: str = "s1"):  # type: ignore[no-untyped-def]
    return make_report(E, "employer", value, source=source)


def status(b: Belief) -> KernelStatus:
    return b.segments[0].kernel_status


def test_a_complete_append_marks_its_closure_and_serves_normally(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema())
    r = ChainReviser()
    res = add(b, employer("acme"), "k1", r)
    assert {x.key.attr for x in res.beliefs} == {"employer", "work_city", "tax_city"}
    for k in (A, B, C):
        got = b.read_belief(k)
        assert isinstance(got, Belief) and got.required_generation == got.completed_generation == 1
        assert b.storage.required_generation(k) == 1 == b.storage.required_at(k, 1)  # column and history agree
    assert b.read_belief(Key(entity="bob", attr="employer")) is None  # never named: no belief, not "stale"
    assert b.complete_pending(r).jobs_pending == 0
    assert b.storage.jobs() == [] and b.storage.dirty_rows() == []
    assert b.recover().ok


def test_skipped_key_is_stale_resource_limited_and_the_old_value_is_never_current(h) -> None:  # type: ignore[no-untyped-def]
    """Row: 'Budget exhausted on A -> B -> C before C is visited | reads of C return resource_limited until the
    completion job stamps it; the old C is never served as current'."""
    b = h.backend
    b.put_schema(chain_schema())
    add(b, employer("acme"), "k1", ChainReviser())
    assert status(b.read_belief(C)) is KernelStatus.ESTABLISHED  # type: ignore[arg-type,union-attr]
    add(b, employer("globex", "s2"), "k2", ChainReviser(skip={"tax_city"}))

    assert isinstance(b.read_belief(B), Belief)  # revised in the append: complete
    got = b.read_belief(C)
    assert isinstance(got, LimitedRead)
    assert got.reason is ResourceLimitedReason.STALE_DEPENDENCY and got.reason_key == C
    assert (got.required_generation, got.completed_generation) == (2, 1)
    assert got.last_complete is not None and got.last_complete.version == 1 and got.last_complete.lsn == 1  # labelled, older

    ans = got.to_answer()
    assert isinstance(ans, ResourceLimited)
    assert ans.last_complete is not None and ans.last_complete.belief_as_of == 1  # labelled with the older snapshot
    assert not hasattr(ans, "kernel_status") and not hasattr(ans, "segment")  # no status, no segment, never 'unresolved'


def test_a_dependent_of_a_stale_key_is_stale_too(h) -> None:  # type: ignore[no-untyped-def]
    """'Reads of it or of anything depending on it return ResourceLimited(stale_dependency)'."""
    b = h.backend
    b.put_schema(chain_schema(band=True))
    add(b, employer("acme"), "k1", ChainReviser())
    add(b, employer("globex", "s2"), "k2", ChainReviser(skip={"tax_city"}))  # D is returned, computed on the OLD tax_city
    assert isinstance(b.read_belief(C), LimitedRead)
    d_row = b.storage.belief_row(D, 2)
    assert d_row is not None and d_row.completed_generation == d_row.required_generation == 2  # complete as written...
    got = b.read_belief(D)
    assert isinstance(got, LimitedRead)  # ...but it rests on a stale dependency
    assert got.reason is ResourceLimitedReason.STALE_DEPENDENCY and got.reason_key == C
    assert got.last_complete is not None and got.last_complete.version == 2


def test_completion_job_stamps_the_stale_key_and_history_keeps_its_own_barrier(h) -> None:  # type: ignore[no-untyped-def]
    """Rows: 'belief_as_of query into a snapshot that was stale at that time | ResourceLimited for that snapshot,
    not the later completed version'."""
    b = h.backend
    b.put_schema(chain_schema())
    add(b, employer("acme"), "k1", ChainReviser())  # lsn 1, generation 1
    add(b, employer("globex", "s2"), "k2", ChainReviser(skip={"tax_city"}))  # lsn 2: C stale
    add(b, make_report(E, "nickname", "al"), "k3", ChainReviser())  # lsn 3: unrelated, so completion happens at a later head

    assert [j.state for j in b.storage.jobs()] == ["pending"]
    rep = b.complete_pending(ChainReviser())
    assert (rep.jobs_done, rep.keys_stamped, rep.jobs_pending) == (1, 1, 0)

    now = b.read_belief(C)
    assert isinstance(now, Belief) and now.version == 2 and now.lsn == 3  # computed at the head, labelled with it
    assert now.required_generation == now.completed_generation == 2
    assert status(now) is KernelStatus.UNKNOWN  # employer is unresolved after the second source: nothing to derive
    assert b.storage.belief_row(C, 2).origin == "completion"  # type: ignore[union-attr]

    assert isinstance(b.read_belief(C, as_of=1), Belief)  # snapshot 1 was complete
    stale = b.read_belief(C, as_of=2)  # snapshot 2 WAS stale: the later completed version must not answer it
    assert isinstance(stale, LimitedRead) and stale.reason is ResourceLimitedReason.STALE_DEPENDENCY
    assert stale.last_complete is not None and stale.last_complete.version == 1
    again = b.read_belief(C, as_of=3)
    assert isinstance(again, Belief) and again.version == 2
    assert b.belief_at(C, 2).version == 1  # type: ignore[union-attr]  # the raw accessor still resolves by lsn
    assert b.recover().ok and b.verify_beliefs(ChainReviser()).ok


def test_inference_incomplete_is_resource_limited_not_unresolved(h) -> None:  # type: ignore[no-untyped-def]
    """Row: 'Inference budget exceeded -> ResourceLimited with no kernel_status, never unresolved'."""
    b = h.backend
    b.put_schema(chain_schema())
    add(b, employer("acme"), "k1", ChainReviser(incomplete={"work_city"}))
    got = b.read_belief(B)
    assert isinstance(got, LimitedRead) and got.reason is ResourceLimitedReason.INFERENCE_INCOMPLETE
    assert got.completed_generation < got.required_generation
    ans = got.to_answer()
    assert isinstance(ans, ResourceLimited) and ans.decision == "resource_limited"
    assert not hasattr(ans, "kernel_status")
    assert isinstance(b.read_belief(A), Belief)  # the touched key itself is fine
    assert [j.state for j in b.storage.jobs()] == ["pending"]  # a durable job finishes it
    assert b.complete_pending(ChainReviser()).jobs_pending == 0
    assert isinstance(b.read_belief(B), Belief)


def test_an_unfinishable_key_does_not_pile_up_versions(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema())
    add(b, employer("acme"), "k1", ChainReviser())
    add(b, employer("globex", "s2"), "k2", ChainReviser(skip={"work_city", "tax_city"}))
    stuck = ChainReviser(recompute_incomplete={"work_city"})
    for i in range(barrier.MAX_JOB_ATTEMPTS):
        rep = b.complete_pending(stuck)
        # B cannot be finished: the job stays pending (loudly) until the attempt cap, then it is blocked
        assert rep.jobs_pending == (1 if i < barrier.MAX_JOB_ATTEMPTS - 1 else 0)
    assert rep.jobs_blocked == 1
    versions = [b.belief_version(B, v) for v in (1, 2, 3, 4)]
    assert versions[0] is not None and versions[1] is not None and versions[2] is None  # one incomplete attempt, then no pile-up
    assert isinstance(b.read_belief(B), LimitedRead)
    (job,) = [j for j in b.storage.jobs() if j.state == barrier.JOB_BLOCKED]
    payload = barrier.JobPayload.from_json(job.payload)
    assert payload.attempts == barrier.MAX_JOB_ATTEMPTS and payload.blocked is not None and "work_city" in payload.blocked
    # the next runs do nothing: a blocked job is not retried in a loop
    again = b.complete_pending(stuck)
    assert again.jobs_done == 0 and again.keys_stamped == 0 and again.jobs_blocked == 1
    assert isinstance(b.read_belief(B), LimitedRead)  # still reported as limited, never served as current
    # after the cause is fixed an operator puts the job back; a reviser that can finish it does
    assert b.retry_blocked() == 1
    assert b.complete_pending(ChainReviser()).jobs_pending == 0
    assert isinstance(b.read_belief(B), Belief)


def test_traversal_overflow_sets_a_dirty_marker_scoped_to_the_attribute_component(h) -> None:  # type: ignore[no-untyped-def]
    """Rows: 'Traversal budget exhausted mid-cascade -> dirty marker; every read ResourceLimited(store_dirty) until the
    completion job clears it' and concern H4: scoped to the connected component, not the whole store."""
    b = h.make(traversal_budget=2)
    b.put_schema(chain_schema())
    add(b, employer("acme"), "k1", ChainReviser())  # closure A, B, C = 3 keys > 2: nothing marked, nothing revised
    st = b.storage
    assert st.marked_keys() == [] and b.current_belief(A) is None
    (dirty,) = st.dirty_rows()
    assert sorted(json.loads(dirty.attrs)) == ["employer", "tax_city", "work_city"]  # the component, not "*"
    assert dirty.generation == 1 and dirty.set_lsn == 1 and dirty.cleared_lsn is None
    assert [j.state for j in st.jobs()] == ["pending"]

    for k in (A, B, C):
        got = b.read_belief(k)
        assert isinstance(got, LimitedRead) and got.reason is ResourceLimitedReason.STORE_DIRTY
    add(b, make_report(E, "nickname", "al"), "k2", ChainReviser())  # another component: keeps serving
    assert isinstance(b.read_belief(N), Belief)

    rep = b.complete_pending(ChainReviser())
    assert (rep.jobs_done, rep.keys_stamped, rep.jobs_pending) == (1, 3, 0)
    for k in (A, B, C):
        got = b.read_belief(k)
        assert isinstance(got, Belief) and got.version == 1 and got.lsn == 2
        assert got.required_generation == got.completed_generation == 1
    assert st.dirty_rows()[0].cleared_lsn == 2
    assert status(b.read_belief(A)) is KernelStatus.ESTABLISHED  # type: ignore[arg-type,union-attr]
    assert status(b.read_belief(B)) is KernelStatus.ESTABLISHED  # type: ignore[arg-type,union-attr]

    # the barrier of the snapshot: lsn 1 was dirty, lsn 2 (the completion's head) is served
    old = b.read_belief(A, as_of=1)
    assert isinstance(old, LimitedRead) and old.reason is ResourceLimitedReason.STORE_DIRTY
    assert isinstance(b.read_belief(A, as_of=2), Belief)
    assert b.recover().ok


def test_without_a_schema_the_overflow_falls_back_to_a_store_wide_marker(h) -> None:  # type: ignore[no-untyped-def]
    b = h.make(traversal_budget=1)
    r = SchemalessReviser()
    x = Key(entity=E, attr="x")
    add(b, make_report(E, "x", "v1"), "k1", r)  # closure {x}: fits
    assert isinstance(b.read_belief(x), Belief)
    add(b, make_report(E, "x", "v2", source="s2"), "k2", r)  # x -> mirror: a second key, over the budget of 1
    (dirty,) = b.storage.dirty_rows()
    assert json.loads(dirty.attrs) == ["*"]  # last resort: no schema bounds the component
    other = b.read_belief(Key(entity="zed", attr="whatever"))  # nothing stored for it, yet the whole store is dirty
    assert isinstance(other, LimitedRead) and other.reason is ResourceLimitedReason.STORE_DIRTY
    lim = b.read_belief(x)
    assert isinstance(lim, LimitedRead) and lim.reason is ResourceLimitedReason.STORE_DIRTY
    assert lim.last_complete is not None and lim.last_complete.version == 1  # the older complete version, labelled
    b.complete_pending(r)
    assert isinstance(b.read_belief(x), Belief)


def test_a_job_for_generation_g_never_overwrites_work_completed_for_g_plus_1(h) -> None:  # type: ignore[no-untyped-def]
    """Row: 'Completion job for generation g runs after generation g+1 completed the same key -> the job finishes keys
    still incomplete at g and does not overwrite the g+1 version'."""
    b = h.backend
    b.put_schema(chain_schema())
    add(b, employer("acme"), "k1", ChainReviser(skip={"work_city", "tax_city"}))  # g=1: B and C named, not revised
    assert b.storage.required_generation(B) == 1 and b.current_belief(B) is None  # placeholders: marked, no belief yet
    assert isinstance(b.read_belief(B), LimitedRead)  # never-computed but named: stale, not "no belief"
    add(b, employer("globex", "s2"), "k2", ChainReviser(skip={"tax_city"}))  # g=2: A and B completed for g=2; C still not
    b_row = b.storage.belief_row(B, 1)
    assert b_row is not None and b_row.completed_generation == 2

    rep = b.complete_pending(ChainReviser())
    assert rep.jobs_done == 2 and rep.jobs_pending == 0
    assert rep.keys_stamped == 1 and rep.skipped_newer >= 1  # C finished once; B (done at g=2) left alone by job 1
    assert b.belief_version(B, 2) is None  # job g=1 did not write over the g=2 version of B
    c = b.read_belief(C)
    assert isinstance(c, Belief) and c.version == 1 and b.storage.belief_row(C, 1).origin == "completion"  # type: ignore[union-attr]
    assert c.required_generation == c.completed_generation == 2  # stamped for the newest generation naming it
    assert b.recover().ok


def test_barrier_marks_jobs_and_events_are_atomic_with_the_append(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema())
    b.subscribe("plan", [A])

    def crash(step: str) -> None:
        if step == "after_barrier":
            raise Boom

    b.set_fault_hook(crash)
    with pytest.raises(Boom):
        add(b, employer("acme"), "k1", ChainReviser(skip={"tax_city"}))
    b.set_fault_hook(None)
    st = b.storage
    assert b.head().lsn == 0 and st.marked_keys() == [] and st.jobs() == [] and st.dirty_rows() == [] and st.outbox_all() == []
    assert b.current_belief(A) is None and b.recover().ok
    again = add(b, employer("acme"), "k1", ChainReviser(skip={"tax_city"}))  # the retry works and leaves one job, one event
    assert not again.replayed and len(st.jobs()) == 1 and len(st.outbox_all()) == 1


def test_required_generation_column_matches_the_marking_history(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema())
    for i, v in enumerate(["acme", "globex", "initech"]):
        add(b, employer(v, f"s{i}"), f"k{i}", ChainReviser(skip={"tax_city"} if i == 1 else ()))
    for k in b.storage.marked_keys():
        assert b.storage.required_generation(k) == b.storage.required_at(k, FAR)
    assert b.recover().ok


def test_completion_and_delivery_run_safely_beside_concurrent_appends(h) -> None:  # type: ignore[no-untyped-def]
    import threading

    b = h.backend
    b.put_schema(chain_schema())
    for i in range(4):
        b.subscribe(f"plan{i}", [Key(entity=f"e{i}", attr="tax_city")])
    errors: list[BaseException] = []
    stop = threading.Event()

    def writer(i: int) -> None:
        try:
            for n in range(10):
                skip = {"tax_city"} if n % 3 == 0 else set()
                add(b, make_report(f"e{i}", "employer", f"v{n}", source=f"s{i}"), f"w{i}-{n}", ChainReviser(skip=skip))
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    def worker() -> None:
        try:
            while not stop.is_set():
                b.complete_pending(ChainReviser())
                b.deliver(lambda _e: None)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    bg = threading.Thread(target=worker)
    ws = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    bg.start()
    for t in ws:
        t.start()
    for t in ws:
        t.join()
    stop.set()
    bg.join()
    assert not errors, errors
    b.complete_pending(ChainReviser())
    assert b.head().lsn == 40 and b.storage.jobs("pending") == []
    assert b.recover().ok and b.verify_log().ok
    for i in range(4):
        for attr in ("employer", "work_city", "tax_city"):
            assert isinstance(b.read_belief(Key(entity=f"e{i}", attr=attr)), Belief)
    b.deliver(lambda _e: None)
    assert b.pending_events() == ()


def test_the_barrier_survives_a_restart(h) -> None:  # type: ignore[no-untyped-def]
    if h.kind != "sqlite":
        pytest.skip("restart needs a file")
    b = h.make(traversal_budget=2)
    b.put_schema(chain_schema())
    add(b, employer("acme"), "k1", ChainReviser())
    b.close()
    b2 = h.make(traversal_budget=2)
    lim = b2.read_belief(A)
    assert isinstance(lim, LimitedRead) and lim.reason is ResourceLimitedReason.STORE_DIRTY  # the marker is durable
    assert [j.state for j in b2.storage.jobs()] == ["pending"]  # and so is the job
    assert b2.complete_pending(ChainReviser()).jobs_pending == 0  # resumed after the restart
    assert isinstance(b2.read_belief(A), Belief)
    assert b2.recover().ok and b2.verify_log().ok
    b2.close()


def test_traversal_budget_must_be_positive() -> None:
    from palimem.store import InMemoryBackend

    with pytest.raises(ValueError):
        InMemoryBackend(traversal_budget=0)
