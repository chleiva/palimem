"""Deletion with dependency repair (T-C8, S-13).

Row: 'Deletion of a report with dependants -> content gone, tombstone present, dependants repaired'.
No plain value, actor, source, raw reference or client idempotency key may remain in ANY table. The key text of the
erased report is removed from the log, the admissions, the jobs and the tombstone; it remains in the belief index
columns of a key that still exists (documented residual, docs/STORAGE.md section 7).
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from chain_fakes import ChainReviser, chain_schema
from fakes import FakeAdmitter, make_report

from palimem.store import ErasureReason, StoreError
from palimem.store.backend import LimitedRead, NotReconstructable
from palimem.store.views import belief_ref
from palimem.types import (
    Belief,
    Cue,
    KernelStatus,
    Key,
    LogEntry,
    ResourceLimitedReason,
)

P = "patient-4711"  # a sensitive entity name
A = Key(entity=P, attr="employer")
B = Key(entity=P, attr="work_city")
C = Key(entity=P, attr="tax_city")

SECRET_VALUES = ("SecretCorp", "hrdesk", "alice-hr.pdf", "s3://records", "idem-secretcorp-onboarding", "connector:hrdesk")


def add(b, report, idem, reviser=None):  # type: ignore[no-untyped-def]
    return b.append(report, idempotency_key=idem, admitter=FakeAdmitter(), reviser=reviser or ChainReviser())


def secret_report(value: str = "SecretCorp"):  # type: ignore[no-untyped-def]
    return make_report(P, "employer", value, source="hrdesk", actor="connector:hrdesk", raw_ref="s3://records/alice-hr.pdf")


def tables(h) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """Everything the backend holds, as text, per table."""
    if h.kind == "sqlite":
        con = sqlite3.connect(h.path)
        out = {name: repr(con.execute(f"SELECT * FROM {name}").fetchall()) for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        con.close()
        return out
    st = h.backend.storage
    return {
        "log": repr(list(st.log.values())), "admissions": repr(list(st.adm.values())), "beliefs": repr(list(st.beliefs.values())),
        "current_belief": repr(st.current) + repr(st.required), "marks": repr(st.marks), "completion_jobs": repr(st.job_rows),
        "dirty_components": repr(st.dirty), "outbox": repr(list(st.outbox.values())), "subscriptions": repr(st.subs), "inputs": repr(st.inputs),
    }


def assert_no_secret_values(h) -> None:  # type: ignore[no-untyped-def]
    for name, text in tables(h).items():
        for secret in SECRET_VALUES:
            assert secret not in text, f"{secret!r} survives in table {name}"


def status(b: Belief) -> KernelStatus:
    return b.segments[0].kernel_status


def test_erasing_the_only_evidence_repairs_every_dependant_and_leaves_no_trace(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema())
    b.subscribe("plan", [C])
    res = add(b, secret_report(), "idem-secretcorp-onboarding")
    rid, original_hash = res.entry.report.id, res.entry.entry_hash
    assert status(b.read_belief(C)) is KernelStatus.ESTABLISHED  # type: ignore[arg-type,union-attr]
    assert "SecretCorp" in tables(h)["beliefs"]  # sanity: the derived value was materialised before the erasure

    tomb = b.erase(rid, ErasureReason.ERASURE_REQUEST, reviser=ChainReviser())

    assert tomb.entry_hash == original_hash and len(tomb.affected_versions) == 3  # employer, work_city, tax_city v1
    assert b.verify_log().ok  # the chain verifies across the tombstone
    assert_no_secret_values(h)  # values, source, actor, raw_ref, client idempotency key: gone from every table
    t = tables(h)
    assert P not in t["log"] and P not in t["admissions"] and P not in t["completion_jobs"] and P not in repr(tomb.to_dict())
    assert P in t["beliefs"]  # documented residual: the key's own index columns, because the key still exists

    for k in (A, B, C):  # dependants repaired: the derived values rested only on the erased report
        got = b.read_belief(k)
        assert isinstance(got, Belief) and got.version == 2 and status(got) is KernelStatus.UNKNOWN
        assert got.required_generation == got.completed_generation == 2 and got.lsn == b.head().lsn
        row1 = b.storage.belief_row(k, 1)
        assert row1 is not None and row1.reconstructable is False  # the old version is redacted and flagged
        assert b.belief_version(k, 1) is None and b.get_belief_by_ref(belief_ref(k, 1)) is None
        assert b.storage.belief_row(k, 2).origin == "repair"  # type: ignore[union-attr]
    assert status(b.read_belief(A, as_of=1)) is KernelStatus.UNKNOWN  # type: ignore[arg-type,union-attr]  # history as the log now justifies it

    events = b.pending_events()
    old = [e for e in events if e.new_version == 1]
    assert old and all(e.redacted and e.old_view is None and e.new_view is None for e in old)  # the views were redacted
    new = [e for e in events if e.new_version == 2]
    assert len(new) == 1 and new[0].new_view is not None and new[0].new_view.segment.kernel_status is KernelStatus.UNKNOWN

    again = add(b, secret_report(), "idem-secretcorp-onboarding")  # a retry of the erased append: the tombstone, not a new report
    assert again.replayed and again.entry is None and again.tombstone == tomb and b.head().lsn == 1
    assert b.storage.log_by_report(rid).idem_key.startswith("erased:")  # type: ignore[union-attr]
    assert b.verify_beliefs(ChainReviser()).ok and b.recover().ok


def test_erasing_one_of_two_supports_keeps_the_survivor_and_hides_the_erased_value(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema())
    r1 = add(b, secret_report("SecretCorp"), "idem-secretcorp-onboarding")
    add(b, make_report(P, "employer", "Globex", source="s2"), "k2")
    assert status(b.read_belief(A)) is KernelStatus.UNRESOLVED  # type: ignore[arg-type,union-attr]

    tomb = b.erase(r1.entry.report.id, ErasureReason.RETENTION_EXPIRY, reviser=ChainReviser())
    assert len(tomb.affected_versions) == 6  # employer, work_city, tax_city: versions 1 and 2 each

    a = b.read_belief(A)
    assert isinstance(a, Belief) and a.version == 3 and status(a) is KernelStatus.ESTABLISHED
    assert a.segments[0].established is not None and a.segments[0].established.form.value == "Globex"  # type: ignore[union-attr]
    assert b.read_belief(B).segments[0].established.form.value == "derived:Globex"  # type: ignore[union-attr]
    assert b.read_belief(C).segments[0].established.form.value == "derived:derived:Globex"  # type: ignore[union-attr]
    assert_no_secret_values(h)

    gone = b.read_belief(A, as_of=1)  # the version in force at lsn 1 was redacted: that answer cannot be rebuilt
    assert isinstance(gone, NotReconstructable) and (gone.key, gone.version, gone.lsn) == (A, 1, 1)
    assert isinstance(b.read_belief(A, as_of=2), Belief) and b.read_belief(A, as_of=2).version == 3  # type: ignore[union-attr]
    assert b.verify_beliefs(ChainReviser()).ok and b.recover().ok and b.verify_log().ok


def test_erase_without_the_reviser_is_refused_when_beliefs_rest_on_the_report(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema())
    res = add(b, secret_report(), "idem-secretcorp-onboarding")
    with pytest.raises(StoreError, match="reviser"):
        b.erase(res.entry.report.id, ErasureReason.OTHER)
    assert isinstance(b.get_entry(res.entry.report.id), LogEntry)  # nothing changed
    assert b.belief_version(A, 1) is not None and b.verify_log().ok


def test_a_failed_repair_rolls_the_erasure_back(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema())
    res = add(b, secret_report(), "idem-secretcorp-onboarding")
    generation = b.head().generation
    with pytest.raises(RuntimeError):
        b.erase(res.entry.report.id, ErasureReason.ERASURE_REQUEST, reviser=ChainReviser(fail_recompute=True))
    assert isinstance(b.get_entry(res.entry.report.id), LogEntry)  # the report is still there
    assert b.belief_version(A, 1) is not None and b.head().generation == generation  # and so are its beliefs
    assert b.verify_log().ok and b.recover().ok
    b.erase(res.entry.report.id, ErasureReason.ERASURE_REQUEST, reviser=ChainReviser())  # a working reviser then succeeds
    assert_no_secret_values(h)


def test_a_report_that_nothing_rests_on_needs_no_reviser(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema())
    first = add(b, secret_report(), "idem-secretcorp-onboarding")
    w = add(b, make_report(P, "employer", cue=Cue.WITHDRAW, target=first.entry.report.id, source="hrdesk", actor="connector:hrdesk"), "k2")
    tomb = b.erase(w.entry.report.id, ErasureReason.OTHER)  # a withdraw pins nothing
    assert tomb.affected_versions == ()
    assert b.verify_log().ok and b.recover().ok


def test_a_traversal_overflow_during_an_erasure_defers_the_repair_to_a_durable_job(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema())
    res = add(b, secret_report(), "idem-secretcorp-onboarding")
    b._traversal_budget = 2  # type: ignore[attr-defined]  # three keys depend on the report: over budget
    b.erase(res.entry.report.id, ErasureReason.ERASURE_REQUEST, reviser=ChainReviser())

    st = b.storage
    (dirty,) = st.dirty_rows()
    assert sorted(json.loads(dirty.attrs)) == ["employer", "tax_city", "work_city"] and dirty.cleared_lsn is None
    (job,) = st.jobs()
    assert job.state == "pending" and json.loads(job.payload)["kind"] == "repair"
    assert_no_secret_values(h)  # the content is already gone while the repair is deferred
    lim = b.read_belief(A)
    assert isinstance(lim, LimitedRead) and lim.reason is ResourceLimitedReason.STORE_DIRTY
    assert lim.last_complete is None  # the only older version was redacted: nothing leaks through last_complete

    rep = b.complete_pending(ChainReviser())
    assert (rep.jobs_done, rep.keys_stamped, rep.jobs_pending) == (1, 3, 0)
    for k in (A, B, C):
        got = b.read_belief(k)
        assert isinstance(got, Belief) and status(got) is KernelStatus.UNKNOWN
    assert st.dirty_rows()[0].cleared_lsn == b.head().lsn
    t = tables(h)
    assert P not in t["completion_jobs"]  # the finished job keeps no key text
    assert_no_secret_values(h)
    assert b.recover().ok and b.verify_log().ok


def test_the_idempotency_key_is_not_kept_in_the_clear_after_an_erasure(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    res = add(b, secret_report(), "idem-secretcorp-onboarding")
    b.erase(res.entry.report.id, ErasureReason.OTHER, reviser=ChainReviser())
    row = b.storage.log_by_report(res.entry.report.id)
    assert row is not None and row.idem_key.startswith("erased:") and "secretcorp" not in row.idem_key
    if h.kind == "sqlite":
        con = sqlite3.connect(h.path)
        (idem,) = con.execute("SELECT idem_key FROM log WHERE lsn = 1").fetchone()
        con.close()
        assert idem.startswith("erased:")
