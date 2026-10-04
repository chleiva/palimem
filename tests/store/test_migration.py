"""Store-format versions and the migration hook (T-C9): a format-1 file is upgraded in one transaction at open."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from chain_fakes import ChainReviser, chain_schema
from fakes import FakeAdmitter, make_report

from palimem.store import MIGRATIONS, STORE_FORMAT_VERSION, SQLiteBackend, StoreError
from palimem.types import Belief, Key

E = "alice"
A = Key(entity=E, attr="employer")

DOWNGRADE_TO_V1 = """
DROP TRIGGER IF EXISTS beliefs_flag_only;
DROP TRIGGER IF EXISTS log_erase_only;
ALTER TABLE beliefs DROP COLUMN origin;
ALTER TABLE current_belief DROP COLUMN required_generation;
DROP TABLE marks;
DROP TABLE dirty_components;
DROP TABLE completion_jobs;
DROP TABLE outbox;
CREATE TABLE completion_jobs (
  generation INTEGER PRIMARY KEY, state TEXT NOT NULL, keys TEXT NOT NULL, created_us INTEGER NOT NULL, updated_us INTEGER NOT NULL
);
CREATE TABLE outbox (
  event_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, entity TEXT NOT NULL, attr TEXT NOT NULL,
  old_version INTEGER, new_version INTEGER NOT NULL, lsn INTEGER NOT NULL, payload TEXT NOT NULL,
  created_us INTEGER NOT NULL, delivered_us INTEGER
);
PRAGMA user_version = 1;
"""


def add(b, report, idem):  # type: ignore[no-untyped-def]
    return b.append(report, idempotency_key=idem, admitter=FakeAdmitter(), reviser=ChainReviser())


def shape(path: Path) -> tuple[dict[str, list[tuple[object, ...]]], dict[str, str], set[str]]:
    con = sqlite3.connect(path)
    tables = {n: [tuple(c) for c in con.execute(f"PRAGMA table_info({n})")] for (n,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    triggers = {n: " ".join(s.split()) for n, s in con.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger'")}
    indexes = {n for (n,) in con.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")}
    con.close()
    return tables, triggers, indexes


def make_v1(path: Path) -> None:
    """A populated store, downgraded to what format 1 looked like."""
    b = SQLiteBackend(path)
    b.put_schema(chain_schema())
    add(b, make_report(E, "employer", "acme", source="s1"), "k1")
    add(b, make_report(E, "employer", "globex", source="s2"), "k2")
    b.close()
    con = sqlite3.connect(path, isolation_level=None)
    con.executescript(DOWNGRADE_TO_V1)
    con.close()


def test_a_format_1_file_is_migrated_to_exactly_the_fresh_format_2_shape(tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert STORE_FORMAT_VERSION == 2 and 1 in MIGRATIONS
    old = tmp_path / "old.db"
    make_v1(old)
    con = sqlite3.connect(old)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 1
    assert "required_generation" not in [c[1] for c in con.execute("PRAGMA table_info(current_belief)")]
    con.close()

    fresh = tmp_path / "fresh.db"
    SQLiteBackend(fresh).close()
    b = SQLiteBackend(old)  # opening migrates
    try:
        con = sqlite3.connect(old)
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2
        con.close()
        assert shape(old) == shape(fresh)  # same tables, columns, triggers and indexes as a database created today

        # the data survived and is readable under the barrier; old versions count as 'append'
        a = b.read_belief(A)
        assert isinstance(a, Belief) and a.version == 2 and b.storage.required_generation(A) == 0
        assert b.storage.belief_row(A, 1).origin == "append"  # type: ignore[union-attr]
        assert b.verify_log().ok and b.recover().ok and b.verify_beliefs(ChainReviser()).ok

        # and the new machinery works on the migrated file
        b.subscribe("plan", [A])
        add(b, make_report(E, "employer", "initech", source="s3"), "k3")
        assert b.storage.required_generation(A) == 3 and len(b.pending_events()) == 1
        assert isinstance(b.read_belief(A), Belief) and b.recover().ok
    finally:
        b.close()
    SQLiteBackend(old).close()  # a second open changes nothing
    assert shape(old) == shape(fresh)


def test_a_refused_migration_leaves_the_file_untouched(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "old.db"
    make_v1(path)
    con = sqlite3.connect(path, isolation_level=None)
    con.execute("INSERT INTO outbox VALUES ('e1','p','alice','employer',NULL,1,1,'{}',1,NULL)")  # format 1 had no outbox logic
    con.close()
    before = shape(path)
    with pytest.raises(StoreError, match="outbox"):
        SQLiteBackend(path)
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 1  # rolled back: still format 1
    con.close()
    assert shape(path) == before


def test_a_missing_migration_step_is_an_error(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "old.db"
    make_v1(path)
    monkeypatch.delitem(MIGRATIONS, 1)
    with pytest.raises(StoreError, match="no migration from store format 1"):
        SQLiteBackend(path)
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 1
    con.close()


def test_a_newer_format_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "f.db"
    SQLiteBackend(path).close()
    con = sqlite3.connect(path, isolation_level=None)
    con.execute("PRAGMA user_version = 3")
    con.close()
    with pytest.raises(StoreError, match="not supported"):
        SQLiteBackend(path)


def test_the_export_header_carries_the_store_format(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import json

    b = SQLiteBackend(tmp_path / "x.db")
    header = json.loads(next(iter(b.export_jsonl())))
    assert header["store_format"] == STORE_FORMAT_VERSION and header["format"] == "palimem-log"
    b.close()
