"""Crash injection (T-C2, E1.4): a fault at any step of the append transaction leaves no trace; a retry
with the same idempotency key neither duplicates the report nor leaves a partial revision.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from fakes import FakeAdmitter, FakeReviser, make_report

from palimem.store import APPEND_STEPS, SQLiteBackend
from palimem.types import Key

K = Key(entity="alice", attr="employer")
BEFORE_COMMIT = [s for s in APPEND_STEPS if s != "after_commit"]
TESTS = Path(__file__).parent


class Crash(BaseException):
    """BaseException, like KeyboardInterrupt or a killed worker: nothing may catch and 'handle' it."""


def crash_at(step: str):  # type: ignore[no-untyped-def]
    def hook(s: str) -> None:
        if s == step:
            raise Crash(step)

    return hook


def append(b, value, idem, source="s1"):  # type: ignore[no-untyped-def]
    return b.append(make_report("alice", "employer", value, source=source), idempotency_key=idem, admitter=FakeAdmitter(), reviser=FakeReviser())


def state(b):  # type: ignore[no-untyped-def]
    cur = b.current_belief(K)
    return (b.head(), None if cur is None else cur.version, len(list(b.scan())), b.storage.adm_head().seq if b.storage.adm_head() else 0)


def test_steps_are_ordered_and_complete() -> None:
    assert APPEND_STEPS[0] == "begin" and APPEND_STEPS[-1] == "after_commit"
    assert {"after_log_insert", "after_admission", "after_revision", "after_beliefs", "after_index", "before_commit"} <= set(APPEND_STEPS)


@pytest.mark.parametrize("step", BEFORE_COMMIT)
def test_crash_before_commit_leaves_no_trace_and_retry_succeeds_once(h, step) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    append(b, "acme", "k0")
    before = state(b)
    b.set_fault_hook(crash_at(step))
    with pytest.raises(Crash):
        append(b, "globex", "k1", source="s2")
    b.set_fault_hook(None)
    assert state(b) == before  # no log row, no admission, no belief, no generation bump, no index change
    assert b.storage.log_by_idem("k1") is None  # the idempotency key is not burnt
    assert b.recover().ok and b.verify_log().ok and b.verify_beliefs(FakeReviser()).ok
    r = append(b, "globex", "k1", source="s2")
    assert r.entry.lsn == 2 and not r.replayed
    again = append(b, "globex", "k1", source="s2")
    assert again.replayed and again.entry == r.entry and again.beliefs == r.beliefs
    assert len(list(b.scan())) == 2 and b.current_belief(K).version == 2  # type: ignore[union-attr]
    assert b.recover().ok and b.verify_log().ok


def test_crash_after_commit_is_durable_and_retry_replays(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    append(b, "acme", "k0")
    b.set_fault_hook(crash_at("after_commit"))
    with pytest.raises(Crash):
        append(b, "globex", "k1", source="s2")
    b.set_fault_hook(None)
    assert b.head().lsn == 2  # the append is committed
    r = append(b, "globex", "k1", source="s2")  # the client retries because it never saw the answer
    assert r.replayed and r.entry.lsn == 2 and len(r.beliefs) == 1
    assert len(list(b.scan())) == 2  # no duplicate report
    assert b.recover().ok and b.verify_log().ok


def test_sqlite_state_survives_reopening_after_a_crash(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db = tmp_path / "c.db"
    b = SQLiteBackend(db)
    append(b, "acme", "k0")
    b.set_fault_hook(crash_at("after_beliefs"))
    with pytest.raises(Crash):
        append(b, "globex", "k1", source="s2")
    b.close()  # the connection is dropped without anyone cleaning up
    b2 = SQLiteBackend(db)
    assert b2.head().lsn == 1 and b2.recover().ok and b2.verify_log().ok
    assert append(b2, "globex", "k1", source="s2").entry.lsn == 2
    b2.close()


KILL_SCRIPT = """
import os, sys
sys.path.insert(0, sys.argv[3])
from fakes import FakeAdmitter, FakeReviser, make_report
from palimem.store import SQLiteBackend

step = sys.argv[2]
def hook(s):
    if s == step:
        os._exit(9)   # the process dies: no rollback, no finally, no close

b = SQLiteBackend(sys.argv[1], fault=hook)
b.append(make_report('alice', 'employer', 'globex', source='s2'), idempotency_key='k1',
         admitter=FakeAdmitter(), reviser=FakeReviser())
os._exit(0)
"""


@pytest.mark.parametrize("step", APPEND_STEPS)
def test_sqlite_process_killed_mid_append(tmp_path, step) -> None:  # type: ignore[no-untyped-def]
    db = tmp_path / "kill.db"
    b = SQLiteBackend(db)
    append(b, "acme", "k0")
    b.close()
    proc = subprocess.run([sys.executable, "-c", KILL_SCRIPT, str(db), step, str(TESTS)], capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 9, proc.stderr
    b = SQLiteBackend(db)
    committed = step == "after_commit"
    assert b.head().lsn == (2 if committed else 1)
    assert b.recover().ok and b.verify_log().ok and b.verify_beliefs(FakeReviser()).ok
    assert (b.storage.log_by_idem("k1") is not None) is committed
    r = append(b, "globex", "k1", source="s2")  # the client's retry
    assert r.replayed is committed and r.entry.lsn == 2
    assert len(list(b.scan())) == 2
    assert b.current_belief(K).version == 2  # type: ignore[union-attr]
    assert len(b.storage.adm_for_lsn(2)) == 1  # exactly one admission record for the one report
    b.close()
