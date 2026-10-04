"""Hash-chain verification (T-C10) and erasure: SEC-25, SEC-26, SEC-30, S-13 fixtures.

Tampering goes around the engine: for SQLite by editing the file with the guard triggers dropped (an
attacker with file access can do that), for the in-memory backend by editing the storage rows.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import replace

import pytest
from fakes import FakeAdmitter, FakeReviser, make_report

from palimem.store import (
    CapabilityError,
    ErasureReason,
    Head,
    InMemoryBackend,
    StoreError,
)
from palimem.types import Belief, Key

K = Key(entity="alice", attr="employer")


def add(h, report, idem):  # type: ignore[no-untyped-def]
    h.clock.tick()
    return h.backend.append(report, idempotency_key=idem, admitter=FakeAdmitter(), reviser=FakeReviser())


def populate(h, n=4):  # type: ignore[no-untyped-def]
    out = []
    for i in range(n):
        out.append(add(h, make_report("alice", "employer", f"co{i}", source=f"s{i}"), f"k{i}"))
    return out


def kinds(result):  # type: ignore[no-untyped-def]
    return {p.kind for p in result.problems}


def edit_row(h, lsn, **changes):  # type: ignore[no-untyped-def]
    if h.kind == "sqlite":
        sets = ", ".join(f"{k} = ?" for k in changes)
        h.tamper(f"UPDATE log SET {sets} WHERE lsn = ?", *changes.values(), lsn)
    else:
        h.tamper(lambda st: st.log.__setitem__(lsn, replace(st.log[lsn], **changes)))


def test_clean_log_verifies_and_head_is_exportable(h) -> None:  # type: ignore[no-untyped-def]
    populate(h)
    res = h.backend.verify_log()
    assert res.ok and res.checked == 4 and not res.problems
    assert all(r.linked and r.content_verified and not r.tombstoned for r in res.rows)
    head = h.backend.export_head()
    assert head.lsn == 4 and head.entry_hash and head.admission_seq == 4
    assert Head.from_json(head.to_json()) == head
    assert h.backend.verify_log(2, 3).checked == 2
    assert h.backend.verify_log(anchor=head).ok
    assert h.backend.recover().ok


def test_edited_content_is_detected(h) -> None:  # type: ignore[no-untyped-def]
    populate(h)
    stored = h.backend.storage.log_by_lsn(2).content  # type: ignore[attr-defined]
    assert "co1" in stored
    edit_row(h, 2, content=stored.replace("co1", "evil"))
    res = h.backend.verify_log()
    assert not res.ok and "commitment_mismatch" in kinds(res)
    assert [p.lsn for p in res.problems if p.kind == "commitment_mismatch"] == [2]


def test_edited_metadata_is_detected(h) -> None:  # type: ignore[no-untyped-def]
    populate(h)
    edit_row(h, 3, recorded_us=1)
    res = h.backend.verify_log()
    assert "entry_hash_mismatch" in kinds(res) and not res.ok


def test_deleted_row_is_detected(h) -> None:  # type: ignore[no-untyped-def]
    populate(h)
    if h.kind == "sqlite":
        h.tamper("DELETE FROM log WHERE lsn = 2")
    else:
        h.tamper(lambda st: st.log.pop(2))
    res = h.backend.verify_log()
    assert not res.ok and {"missing_lsn", "chain_break"} <= kinds(res)


def test_reordered_rows_are_detected(h) -> None:  # type: ignore[no-untyped-def]
    populate(h)
    if h.kind == "sqlite":
        h.tamper("UPDATE log SET lsn = 99 WHERE lsn = 2; UPDATE log SET lsn = 2 WHERE lsn = 3; UPDATE log SET lsn = 3 WHERE lsn = 99;")
    else:
        def swap(st):  # type: ignore[no-untyped-def]
            a, b = st.log[2], st.log[3]
            st.log[2], st.log[3] = replace(b, lsn=2), replace(a, lsn=3)

        h.tamper(swap)
    res = h.backend.verify_log()
    assert not res.ok and "chain_break" in kinds(res)


def test_indexed_key_edited_away_from_the_committed_content_is_detected(h) -> None:  # type: ignore[no-untyped-def]
    populate(h)
    if h.kind == "sqlite":
        h.tamper("UPDATE log SET key_entity = 'mallory' WHERE lsn = 2")
    else:
        h.tamper(lambda st: st.log.__setitem__(2, replace(st.log[2], key=Key(entity="mallory", attr="employer"))))
    res = h.backend.verify_log()
    assert not res.ok and "index_mismatch" in kinds(res)


def test_edited_admission_record_is_detected(h) -> None:  # type: ignore[no-untyped-def]
    populate(h)
    row = h.backend.storage.adm_by_seq(2)  # type: ignore[attr-defined]
    forged = row.record.replace('"admitted"', '"confirmed"')
    assert forged != row.record
    if h.kind == "sqlite":
        h.tamper("UPDATE admissions SET record = ? WHERE seq = 2", forged)
    else:
        h.tamper(lambda st: st.adm.__setitem__(2, replace(st.adm[2], record=forged)))
    res = h.backend.verify_log()
    assert not res.ok and "admission_hash_mismatch" in kinds(res)


# ---------------------------------------------------------------- SEC-25: belief rows


def _unknown_belief_json(b: Belief) -> str:
    d = b.to_dict()
    d["segments"] = [{"valid_from": None, "valid_to": None, "kernel_status": "unknown", "established": None, "alternatives": [], "support": {}}]
    return Belief.from_dict(d).to_json()


def test_sec25_belief_row_edited_directly_is_named_by_verify(h) -> None:  # type: ignore[no-untyped-def]
    populate(h, 3)
    cur = h.backend.current_belief(K)
    assert cur is not None and cur.version == 3
    forged = _unknown_belief_json(cur)
    if h.kind == "sqlite":
        h.tamper("UPDATE beliefs SET belief = ? WHERE entity = 'alice' AND attr = 'employer' AND version = 3", forged)
    else:
        def fn(st):  # type: ignore[no-untyped-def]
            k = ("alice", "employer", 3)
            st.beliefs[k] = replace(st.beliefs[k], belief=forged)

        h.tamper(fn)
    assert h.backend.verify_log().ok  # the evidence log is intact: only belief verification can see this
    res = h.backend.verify_beliefs(FakeReviser())
    assert not res.ok
    (p,) = res.problems
    assert (p.kind, p.key, p.version) == ("belief_mismatch", K, 3)


def test_sec25_current_version_index_pointing_at_an_old_version(h) -> None:  # type: ignore[no-untyped-def]
    populate(h, 3)
    if h.kind == "sqlite":
        h.tamper("UPDATE current_belief SET version = 1 WHERE entity = 'alice'")
    else:
        h.tamper(lambda st: st.current.__setitem__(K, 1))
    res = h.backend.verify_beliefs(FakeReviser())
    assert "index_mismatch" in kinds(res) and not res.ok
    assert h.backend.recover().ok  # still a structurally valid index; only recomputation shows it is stale


def test_untampered_beliefs_verify(h) -> None:  # type: ignore[no-untyped-def]
    populate(h)
    assert h.backend.verify_beliefs(FakeReviser()).ok


# ---------------------------------------------------------------- SEC-26: restored older copy


def fresh(h, tag):  # type: ignore[no-untyped-def]
    return h.make() if h.kind == "memory" else h.make(path=h.path.with_name(f"{tag}.db"))


def test_sec26_older_copy_fails_against_an_exported_head(h) -> None:  # type: ignore[no-untyped-def]
    populate(h, 4)
    anchor = h.backend.export_head()
    older = fresh(h, "older")
    for i in range(2):
        older.append(make_report("alice", "employer", f"co{i}", source=f"s{i}"), idempotency_key=f"k{i}", admitter=FakeAdmitter(), reviser=FakeReviser())
    assert older.verify_log().ok  # internally consistent...
    res = older.verify_log(anchor=anchor)  # ...but behind the anchored head
    assert not res.ok and "anchor_ahead_of_head" in kinds(res)


def test_sec26_rewritten_history_fails_against_an_exported_head(h) -> None:  # type: ignore[no-untyped-def]
    populate(h, 4)
    anchor = h.backend.export_head()
    rewritten = fresh(h, "rewritten")
    for i in range(4):
        rewritten.append(make_report("alice", "employer", f"forged{i}", source=f"s{i}"), idempotency_key=f"k{i}", admitter=FakeAdmitter(), reviser=FakeReviser())
    res = rewritten.verify_log(anchor=anchor)
    assert not res.ok and "anchor_mismatch" in kinds(res)


def test_sec26_sqlite_file_restored_from_an_older_copy(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from palimem.store import SQLiteBackend

    db = tmp_path / "live.db"
    b = SQLiteBackend(db)
    for i in range(2):
        b.append(make_report("alice", "employer", f"co{i}", source=f"s{i}"), idempotency_key=f"k{i}", admitter=FakeAdmitter(), reviser=FakeReviser())
    b.close()
    shutil.copy(db, tmp_path / "backup.db")
    b = SQLiteBackend(db)
    for i in range(2, 5):
        b.append(make_report("alice", "employer", f"co{i}", source=f"s{i}"), idempotency_key=f"k{i}", admitter=FakeAdmitter(), reviser=FakeReviser())
    anchor = b.export_head()
    b.close()
    for suffix in ("-wal", "-shm"):
        (tmp_path / f"live.db{suffix}").unlink(missing_ok=True)
    shutil.copy(tmp_path / "backup.db", db)  # the operator restores yesterday's file
    restored = SQLiteBackend(db)
    assert restored.verify_log().ok  # a restored copy is self-consistent
    res = restored.verify_log(anchor=anchor)
    assert not res.ok and "anchor_ahead_of_head" in kinds(res)
    restored.close()


# ---------------------------------------------------------------- SEC-30 / S-13: erasure


def sensitive(h):  # type: ignore[no-untyped-def]
    add(h, make_report("alice", "employer", "acme", source="s0"), "k0")
    r = add(h, make_report("alice", "health_condition", "diabetes", source="clinic", raw_ref="s3://records/alice-medical.pdf"), "k1")
    add(h, make_report("alice", "employer", "globex", source="s2"), "k2")
    return r


def log_text(h) -> str:  # type: ignore[no-untyped-def]
    """Everything the log and admission tables hold, as text (beliefs are repaired separately, see erase())."""
    st = h.backend.storage
    parts = []
    for r in st.log_range(1, None):
        parts.append(repr(r))
    for a in st.adm_range():
        parts.append(repr(a))
    return " ".join(parts)


def test_sec30_erased_report_leaves_a_pseudonymised_tombstone_and_a_valid_chain(h) -> None:  # type: ignore[no-untyped-def]
    r = sensitive(h)
    rid = r.entry.report.id
    original_hash = r.entry.entry_hash
    before = h.backend.verify_log()
    assert before.ok
    tomb = h.backend.erase(rid, ErasureReason.ERASURE_REQUEST, reviser=FakeReviser())  # type: ignore[arg-type]
    assert tomb.entry_hash == original_hash  # the tombstone carries the ORIGINAL entry hash
    assert tomb.report_id == rid and tomb.lsn == 2 and tomb.reason_class is ErasureReason.ERASURE_REQUEST
    blob = repr(tomb.to_dict()) + log_text(h)
    for secret in ("health_condition", "diabetes", "s3://records", "alice-medical", "clinic", "connector:clinic"):
        assert secret not in blob, secret
    # the chain still verifies across the tombstone, and the row is reported as linked but not content-verified
    after = h.backend.verify_log()
    assert after.ok
    row = {s.lsn: s for s in after.rows}[2]
    assert (row.linked, row.content_verified, row.tombstoned) == (True, False, True)
    assert all(s.content_verified for s in after.rows if s.lsn != 2)
    nxt = next(iter(h.backend.scan(3, 3)))
    assert nxt.prev_hash == original_hash  # type: ignore[union-attr]  # lsn 3 still links to the erased row's original hash
    # content is unreadable; entries_for_key skips it; scan yields the tombstone
    assert h.backend.get_entry(rid) == tomb  # type: ignore[arg-type]
    assert [e.lsn for e in h.backend.entries_for_key(Key(entity="alice", attr="health_condition"))] == []
    assert h.backend.recover().ok


def test_s13_salt_is_erased_with_the_content(h) -> None:  # type: ignore[no-untyped-def]
    r = sensitive(h)
    st = h.backend.storage
    assert st.log_by_lsn(2).salt is not None
    h.backend.erase(r.entry.report.id, ErasureReason.OTHER, reviser=FakeReviser())  # type: ignore[arg-type]
    row = st.log_by_lsn(2)
    assert row.salt is None and row.content is None and row.key is None
    assert row.commitment and row.entry_hash  # kept: they are what the chain links through
    if h.kind == "sqlite":
        db = sqlite3.connect(h.path)
        cols = db.execute("SELECT content, salt, key_entity, key_attr FROM log WHERE lsn = 2").fetchone()
        db.close()
        assert cols == (None, None, None, None)


def test_s13_erase_flags_affected_versions_and_is_idempotent(h) -> None:  # type: ignore[no-untyped-def]
    r = sensitive(h)
    rid = r.entry.report.id
    t1 = h.backend.erase(rid, ErasureReason.RETENTION_EXPIRY, reviser=FakeReviser())  # type: ignore[arg-type]
    hk = Key(entity="alice", attr="health_condition")
    assert len(t1.affected_versions) == 1 and t1.affected_versions[0][1] == 1
    assert "health_condition" not in repr(t1.affected_versions)
    assert h.backend.storage.belief_row(hk, 1).reconstructable is False  # type: ignore[attr-defined]
    assert h.backend.erase(rid, ErasureReason.OTHER, reviser=FakeReviser()) == t1  # type: ignore[arg-type]
    with pytest.raises(StoreError):
        h.backend.erase("0" * 26, ErasureReason.OTHER, reviser=FakeReviser())


def test_s13_idempotent_replay_of_an_erased_append_returns_the_tombstone(h) -> None:  # type: ignore[no-untyped-def]
    r = sensitive(h)
    h.backend.erase(r.entry.report.id, ErasureReason.ERASURE_REQUEST, reviser=FakeReviser())  # type: ignore[arg-type]
    again = h.backend.append(make_report("alice", "health_condition", "diabetes", source="clinic"), idempotency_key="k1", admitter=FakeAdmitter(), reviser=FakeReviser())
    assert again.replayed and again.entry is None and again.tombstone is not None
    assert h.backend.head().lsn == 3


def test_erase_needs_a_host_supplied_secret() -> None:
    b = InMemoryBackend()  # no store_secret
    r = b.append(make_report(), idempotency_key="k", admitter=FakeAdmitter(), reviser=FakeReviser())
    assert "erase" not in b.capabilities
    with pytest.raises(CapabilityError):
        b.erase(r.entry.report.id, ErasureReason.OTHER, reviser=FakeReviser())  # type: ignore[arg-type]


def test_chainless_backend_erases_without_an_entry_hash() -> None:
    b = InMemoryBackend(chain=False, store_secret=b"s")
    r = b.append(make_report("alice", "health_condition", "diabetes"), idempotency_key="k", admitter=FakeAdmitter(), reviser=FakeReviser())
    t = b.erase(r.entry.report.id, ErasureReason.ERASURE_REQUEST, reviser=FakeReviser())  # type: ignore[arg-type]
    assert t.entry_hash is None and b.recover().ok


def test_erase_of_a_tombstone_guard_in_sqlite_rejects_unrelated_updates(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Defence in depth: the append-only triggers refuse edits and deletes (an attacker can drop them)."""
    from palimem.store import SQLiteBackend

    db = tmp_path / "g.db"
    b = SQLiteBackend(db)
    b.append(make_report(), idempotency_key="k", admitter=FakeAdmitter(), reviser=FakeReviser())
    b.close()
    con = sqlite3.connect(db)
    for sql in ("DELETE FROM log", "UPDATE log SET content = 'x'", "DELETE FROM admissions", "UPDATE admissions SET record = 'x'",
                "DELETE FROM beliefs", "UPDATE beliefs SET belief = 'x'"):
        with pytest.raises(sqlite3.DatabaseError):
            con.execute(sql)
    con.close()
