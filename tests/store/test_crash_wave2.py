"""Crash injection for the wave-2 operations: completion jobs and erasure are atomic like appends.

A fault inside ``complete_pending`` leaves no partial version and the job pending; a fault inside ``erase`` leaves the
report, its beliefs and its idempotency key exactly as before. On SQLite the process is also really killed."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from chain_fakes import ChainReviser, chain_schema
from conftest import SECRET
from fakes import FakeAdmitter, make_report

from palimem.store import COMPLETION_STEPS, ERASE_STEPS, ErasureReason, SQLiteBackend
from palimem.types import Belief, KernelStatus, Key, LogEntry

TESTS = Path(__file__).parent
E = "alice"
A = Key(entity=E, attr="employer")
B = Key(entity=E, attr="work_city")
C = Key(entity=E, attr="tax_city")


class Boom(Exception):
    pass


def add(b, report, idem, reviser=None):  # type: ignore[no-untyped-def]
    return b.append(report, idempotency_key=idem, admitter=FakeAdmitter(), reviser=reviser or ChainReviser())


def stale_store(b):  # type: ignore[no-untyped-def]
    """Two keys (work_city, tax_city) named by generation 1 but not revised: one pending job."""
    b.put_schema(chain_schema())
    add(b, make_report(E, "employer", "acme", source="s1"), "k1", ChainReviser(skip={"work_city", "tax_city"}))
    assert [j.state for j in b.storage.jobs()] == ["pending"]


@pytest.mark.parametrize("step", COMPLETION_STEPS)
def test_a_fault_inside_a_completion_job_rolls_the_job_back(h, step: str) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    stale_store(b)

    def crash(s: str) -> None:
        if s == step:
            raise Boom

    b.set_fault_hook(crash)
    with pytest.raises(Boom):
        b.complete_pending(ChainReviser())
    b.set_fault_hook(None)
    assert b.belief_version(B, 1) is None and b.belief_version(C, 1) is None  # no partial versions
    assert [j.state for j in b.storage.jobs()] == ["pending"] and b.recover().ok

    rep = b.complete_pending(ChainReviser())  # the retry finishes the job, once
    assert (rep.jobs_done, rep.keys_stamped, rep.jobs_pending) == (1, 2, 0)
    assert b.belief_version(B, 2) is None and b.belief_version(C, 2) is None
    assert isinstance(b.read_belief(C), Belief) and b.recover().ok


@pytest.mark.parametrize("step", ERASE_STEPS)
def test_a_fault_inside_an_erasure_rolls_the_erasure_back(h, step: str) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema())
    res = add(b, make_report(E, "employer", "acme", source="s1"), "k1")
    rid = res.entry.report.id

    def crash(s: str) -> None:
        if s == step:
            raise Boom

    b.set_fault_hook(crash)
    with pytest.raises(Boom):
        b.erase(rid, ErasureReason.ERASURE_REQUEST, reviser=ChainReviser())
    b.set_fault_hook(None)
    assert isinstance(b.get_entry(rid), LogEntry)  # the report is untouched
    assert b.storage.log_by_report(rid).idem_key == "k1"  # type: ignore[union-attr]
    assert b.belief_version(A, 1) is not None and b.storage.belief_row(A, 1).reconstructable  # type: ignore[union-attr]
    assert b.read_belief(A).version == 1 and b.head().generation == 1  # type: ignore[union-attr]
    assert b.verify_log().ok and b.recover().ok

    b.erase(rid, ErasureReason.ERASURE_REQUEST, reviser=ChainReviser())  # and the retry works
    assert b.read_belief(A).segments[0].kernel_status is KernelStatus.UNKNOWN  # type: ignore[union-attr]
    assert b.verify_log().ok and b.verify_beliefs(ChainReviser()).ok


KILL_COMPLETION = """
import os, sys
sys.path.insert(0, sys.argv[3])
from chain_fakes import ChainReviser
from palimem.store import SQLiteBackend

step = sys.argv[2]
def hook(s):
    if s == step:
        os._exit(9)

b = SQLiteBackend(sys.argv[1], fault=hook)
b.complete_pending(ChainReviser())
os._exit(0)
"""

KILL_ERASE = """
import os, sys
sys.path.insert(0, sys.argv[3])
from chain_fakes import ChainReviser
from palimem.store import ErasureReason, SQLiteBackend

step = sys.argv[2]
def hook(s):
    if s == step:
        os._exit(9)

b = SQLiteBackend(sys.argv[1], store_secret=sys.argv[4].encode(), fault=hook)
rid = next(iter(b.scan(1, 1))).report.id
b.erase(rid, ErasureReason.ERASURE_REQUEST, reviser=ChainReviser())
os._exit(0)
"""


@pytest.mark.parametrize("step", COMPLETION_STEPS)
def test_sqlite_process_killed_mid_completion_job(tmp_path, step: str) -> None:  # type: ignore[no-untyped-def]
    db = tmp_path / "kill.db"
    b = SQLiteBackend(db)
    stale_store(b)
    b.close()
    proc = subprocess.run([sys.executable, "-c", KILL_COMPLETION, str(db), step, str(TESTS)], capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 9, proc.stderr
    b = SQLiteBackend(db)
    assert b.belief_version(B, 1) is None and [j.state for j in b.storage.jobs()] == ["pending"]  # nothing partial survived
    assert b.recover().ok and b.verify_log().ok
    assert b.complete_pending(ChainReviser()).jobs_pending == 0  # the durable job is resumed after the kill
    assert isinstance(b.read_belief(C), Belief) and b.recover().ok
    b.close()


@pytest.mark.parametrize("step", ERASE_STEPS)
def test_sqlite_process_killed_mid_erasure(tmp_path, step: str) -> None:  # type: ignore[no-untyped-def]
    db = tmp_path / "kill.db"
    b = SQLiteBackend(db, store_secret=SECRET)
    b.put_schema(chain_schema())
    res = add(b, make_report(E, "employer", "acme", source="s1"), "k1")
    rid = res.entry.report.id
    b.close()
    proc = subprocess.run(
        [sys.executable, "-c", KILL_ERASE, str(db), step, str(TESTS), SECRET.decode()], capture_output=True, text=True, timeout=60, check=False
    )
    assert proc.returncode == 9, proc.stderr
    b = SQLiteBackend(db, store_secret=SECRET)
    assert isinstance(b.get_entry(rid), LogEntry) and b.belief_version(A, 1) is not None  # as if the erasure never started
    assert b.verify_log().ok and b.recover().ok
    b.erase(rid, ErasureReason.ERASURE_REQUEST, reviser=ChainReviser())
    assert b.get_entry(rid).__class__.__name__ == "Tombstone" and b.verify_log().ok  # type: ignore[union-attr]
    b.close()
