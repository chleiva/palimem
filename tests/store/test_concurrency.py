"""Concurrency (T-C2): SQLite has ONE writer at a time (``BEGIN IMMEDIATE``); readers never block.

Writer-lock behaviour, as documented in docs/STORAGE.md:
* two connections (threads, processes or two ``SQLiteBackend`` objects) serialise their appends;
* a writer that cannot get the lock within ``busy_timeout`` raises ``StoreBusy`` and wrote nothing;
* LSNs stay contiguous and the chain intact whichever connection wins.
"""

from __future__ import annotations

import threading

import pytest
from fakes import FakeAdmitter, FakeReviser, make_report

from palimem.store import SQLiteBackend, StoreBusy


def append(b, i, tag):  # type: ignore[no-untyped-def]
    return b.append(make_report("alice", "employer", f"{tag}{i}", source=f"s{tag}"), idempotency_key=f"{tag}-{i}", admitter=FakeAdmitter(), reviser=FakeReviser())


def test_two_connections_interleave_without_gaps_or_forks(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db = tmp_path / "w.db"
    a, b = SQLiteBackend(db), SQLiteBackend(db)
    errors: list[BaseException] = []

    def work(backend, tag):  # type: ignore[no-untyped-def]
        try:
            for i in range(25):
                append(backend, i, tag)
        except BaseException as e:  # noqa: BLE001  # pragma: no cover - collected and asserted below
            errors.append(e)

    ts = [threading.Thread(target=work, args=(a, "a")), threading.Thread(target=work, args=(b, "b"))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors, errors
    c = SQLiteBackend(db)
    assert c.head().lsn == 50
    assert [e.lsn for e in c.scan()] == list(range(1, 51))  # type: ignore[union-attr]
    res = c.verify_log()
    assert res.ok and res.checked == 50
    assert c.recover().ok
    cur = c.current_belief(__import__("palimem.types", fromlist=["Key"]).Key(entity="alice", attr="employer"))
    assert cur is not None and cur.version == 50
    for x in (a, b, c):
        x.close()


def test_one_backend_shared_by_threads(tmp_path) -> None:  # type: ignore[no-untyped-def]
    b = SQLiteBackend(tmp_path / "t.db")
    ts = [threading.Thread(target=lambda n=n: [append(b, i, f"t{n}") for i in range(10)]) for n in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert b.head().lsn == 40 and b.verify_log().ok and b.recover().ok
    b.close()


def test_same_idempotency_key_from_two_connections_writes_once(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db = tmp_path / "i.db"
    a, b = SQLiteBackend(db), SQLiteBackend(db)
    results: list[object] = []

    def go(backend):  # type: ignore[no-untyped-def]
        results.append(backend.append(make_report(), idempotency_key="dup", admitter=FakeAdmitter(), reviser=FakeReviser()))

    ts = [threading.Thread(target=go, args=(x,)) for x in (a, b)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(results) == 2 and sorted(r.replayed for r in results) == [False, True]  # type: ignore[attr-defined]
    assert a.head().lsn == 1
    a.close()
    b.close()


def test_writer_lock_busy_raises_and_writes_nothing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db = tmp_path / "b.db"
    holder = SQLiteBackend(db)
    waiter = SQLiteBackend(db, busy_timeout=0.2)
    with holder.storage.transaction():  # another process is mid-write
        with pytest.raises(StoreBusy):
            append(waiter, 0, "w")
        assert waiter.head().lsn == 0  # readers are not blocked by the writer (WAL)
    append(waiter, 0, "w")  # the lock is free again
    assert waiter.head().lsn == 1 and waiter.recover().ok
    holder.close()
    waiter.close()


def test_wal_mode_and_format_version(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import sqlite3

    db = tmp_path / "f.db"
    SQLiteBackend(db).close()
    con = sqlite3.connect(db)
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert con.execute("PRAGMA user_version").fetchone()[0] == 1
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"log", "admissions", "beliefs", "current_belief", "inputs", "attr_dependents", "completion_jobs", "outbox", "subscriptions", "belief_pins", "belief_deps", "meta"} <= tables
    con.execute("PRAGMA user_version = 99")
    con.close()
    from palimem.store import StoreError

    with pytest.raises(StoreError):
        SQLiteBackend(db)
