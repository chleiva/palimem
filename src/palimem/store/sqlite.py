"""SQLiteBackend: the default backend (T-C2). ``sqlite3`` ships with Python, so ``pip install`` gives a
working memory with no server.

* WAL journal, ``synchronous = FULL``, ``foreign_keys = ON``.
* One write transaction per append, opened with ``BEGIN IMMEDIATE`` (takes the database's single writer
  lock up front). A second connection (another ``SQLiteBackend`` on the same file, another thread or
  process) waits up to ``busy_timeout`` seconds, then raises ``StoreBusy``; readers never block.
* The append-only tables are protected by triggers (no ``DELETE``; ``UPDATE`` only for the controlled
  erasure and the ``reconstructable`` flag). They are defence in depth against bugs, not against an
  attacker with file access, who can drop them: tamper-*evidence* is the hash chain (see ``chain.py``).
* Schema/format version in ``PRAGMA user_version`` (``STORE_FORMAT_VERSION``).

Format version 2 (T-C4/T-C5/T-C9): ``marks`` (history of required generations), ``dirty_components``,
``completion_jobs`` and ``outbox`` carry logic; ``current_belief`` gains a ``required_generation`` column.
Migrations run in one transaction at open (``MIGRATIONS``). See docs/STORAGE.md.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from palimem.store._storage import (
    AdmRow,
    BeliefRow,
    DirtyRow,
    InputRow,
    JobRow,
    LogRow,
    MarkRow,
    OutboxRow,
)
from palimem.store.backend import FaultHook, StoreBusy, StoreError
from palimem.store.engine import DEFAULT_TRAVERSAL_BUDGET, Engine
from palimem.store.ids import UlidFactory
from palimem.types import Key

STORE_FORMAT_VERSION = 2

_DDL_TABLES = """
CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS log (
  lsn INTEGER PRIMARY KEY,
  report_id TEXT NOT NULL UNIQUE,
  recorded_us INTEGER NOT NULL,
  generation INTEGER NOT NULL,
  key_entity TEXT, key_attr TEXT,
  content TEXT, salt BLOB, commitment TEXT, prev_hash TEXT, entry_hash TEXT,
  idem_key TEXT NOT NULL UNIQUE,
  tomb TEXT
);
CREATE INDEX IF NOT EXISTS log_key ON log (key_entity, key_attr, lsn);
CREATE INDEX IF NOT EXISTS log_time ON log (recorded_us, lsn);

CREATE TABLE IF NOT EXISTS admissions (
  seq INTEGER PRIMARY KEY,
  lsn INTEGER NOT NULL,
  report_id TEXT NOT NULL,
  record TEXT NOT NULL,
  report_entry_hash TEXT, prev_hash TEXT, entry_hash TEXT
);
CREATE INDEX IF NOT EXISTS admissions_report ON admissions (report_id);
CREATE INDEX IF NOT EXISTS admissions_lsn ON admissions (lsn);

CREATE TABLE IF NOT EXISTS beliefs (
  entity TEXT NOT NULL, attr TEXT NOT NULL, version INTEGER NOT NULL,
  lsn INTEGER NOT NULL, recorded_us INTEGER NOT NULL,
  required_generation INTEGER NOT NULL, completed_generation INTEGER NOT NULL,
  belief TEXT NOT NULL,
  reconstructable INTEGER NOT NULL DEFAULT 1,
  origin TEXT NOT NULL DEFAULT 'append',   -- append | completion | repair
  PRIMARY KEY (entity, attr, version)
);
CREATE INDEX IF NOT EXISTS beliefs_asof ON beliefs (entity, attr, lsn);
CREATE INDEX IF NOT EXISTS beliefs_lsn ON beliefs (lsn);

CREATE TABLE IF NOT EXISTS belief_pins (
  entity TEXT NOT NULL, attr TEXT NOT NULL, version INTEGER NOT NULL, report_id TEXT NOT NULL,
  PRIMARY KEY (entity, attr, version, report_id)
);
CREATE INDEX IF NOT EXISTS belief_pins_report ON belief_pins (report_id);

CREATE TABLE IF NOT EXISTS belief_deps (
  entity TEXT NOT NULL, attr TEXT NOT NULL, version INTEGER NOT NULL,
  dep_entity TEXT NOT NULL, dep_attr TEXT NOT NULL, dep_version INTEGER NOT NULL,
  PRIMARY KEY (entity, attr, version, dep_entity, dep_attr)
);
CREATE INDEX IF NOT EXISTS belief_deps_dep ON belief_deps (dep_entity, dep_attr);

CREATE TABLE IF NOT EXISTS current_belief (
  entity TEXT NOT NULL, attr TEXT NOT NULL, version INTEGER NOT NULL,   -- version 0 = placeholder (marked, no belief yet)
  required_generation INTEGER NOT NULL DEFAULT 0,                       -- newest generation that names this key (T-C4)
  PRIMARY KEY (entity, attr)
);

CREATE TABLE IF NOT EXISTS inputs (
  kind TEXT NOT NULL, version INTEGER NOT NULL, effective_lsn INTEGER NOT NULL,
  recorded_us INTEGER NOT NULL, payload TEXT NOT NULL,
  PRIMARY KEY (kind, version)
);

CREATE TABLE IF NOT EXISTS attr_dependents (
  schema_version INTEGER NOT NULL, attr TEXT NOT NULL, dependent TEXT NOT NULL,
  PRIMARY KEY (schema_version, attr, dependent)
);

-- T-C4 generation barrier: the append-only history behind current_belief.required_generation,
-- dirty markers (scoped to the attribute components they name), and durable completion jobs.
CREATE TABLE IF NOT EXISTS marks (
  entity TEXT NOT NULL, attr TEXT NOT NULL, generation INTEGER NOT NULL, lsn INTEGER NOT NULL,
  PRIMARY KEY (entity, attr, generation)
);
CREATE INDEX IF NOT EXISTS marks_lsn ON marks (entity, attr, lsn);
CREATE TABLE IF NOT EXISTS dirty_components (
  generation INTEGER NOT NULL, attrs TEXT NOT NULL, set_lsn INTEGER NOT NULL, cleared_lsn INTEGER,
  PRIMARY KEY (generation, attrs)
);
CREATE TABLE IF NOT EXISTS completion_jobs (
  generation INTEGER PRIMARY KEY,
  state TEXT NOT NULL,            -- pending | done
  payload TEXT NOT NULL,          -- canonical JSON {kind, lsn, seeds, keys}; keys cleared when done
  created_us INTEGER NOT NULL, updated_us INTEGER NOT NULL
);

-- T-C5 subscriptions and the transactional outbox (rows are written inside the revision transaction).
CREATE TABLE IF NOT EXISTS subscriptions (
  plan_id TEXT NOT NULL, entity TEXT NOT NULL, attr TEXT NOT NULL,
  PRIMARY KEY (plan_id, entity, attr)
);
CREATE TABLE IF NOT EXISTS outbox (
  seq INTEGER PRIMARY KEY,
  event_id TEXT NOT NULL,         -- hash(key, old version, new version): stable across redelivery
  plan_id TEXT NOT NULL, entity TEXT NOT NULL, attr TEXT NOT NULL,
  old_version INTEGER, new_version INTEGER NOT NULL,
  lsn INTEGER NOT NULL, payload TEXT,
  created_us INTEGER NOT NULL, delivered_us INTEGER,
  UNIQUE (event_id, plan_id)
);
"""


_TRIGGERS = """
-- append-only protection (defence in depth; see module docstring)
CREATE TRIGGER IF NOT EXISTS log_no_delete BEFORE DELETE ON log
BEGIN SELECT RAISE(ABORT, 'palimem: the evidence log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS log_erase_only BEFORE UPDATE ON log
WHEN NEW.lsn IS NOT OLD.lsn OR NEW.report_id IS NOT OLD.report_id OR NEW.recorded_us IS NOT OLD.recorded_us
  OR NEW.generation IS NOT OLD.generation OR NEW.commitment IS NOT OLD.commitment OR NEW.prev_hash IS NOT OLD.prev_hash
  OR NEW.entry_hash IS NOT OLD.entry_hash OR OLD.tomb IS NOT NULL
  OR NEW.tomb IS NULL OR NEW.content IS NOT NULL OR NEW.salt IS NOT NULL OR NEW.key_entity IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'palimem: log rows may only be erased into a tombstone'); END;
CREATE TRIGGER IF NOT EXISTS admissions_no_delete BEFORE DELETE ON admissions
BEGIN SELECT RAISE(ABORT, 'palimem: the admission log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS admissions_no_update BEFORE UPDATE ON admissions
BEGIN SELECT RAISE(ABORT, 'palimem: the admission log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS beliefs_no_delete BEFORE DELETE ON beliefs
BEGIN SELECT RAISE(ABORT, 'palimem: belief versions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS beliefs_flag_only BEFORE UPDATE ON beliefs
WHEN NEW.entity IS NOT OLD.entity OR NEW.attr IS NOT OLD.attr OR NEW.version IS NOT OLD.version OR NEW.lsn IS NOT OLD.lsn
  OR (NEW.belief IS NOT OLD.belief AND NEW.reconstructable != 0) OR NEW.reconstructable > OLD.reconstructable
  OR NEW.required_generation IS NOT OLD.required_generation OR NEW.origin IS NOT OLD.origin
  OR NEW.completed_generation IS NOT OLD.completed_generation OR NEW.recorded_us IS NOT OLD.recorded_us
BEGIN SELECT RAISE(ABORT, 'palimem: belief versions are append-only'); END;
"""


def _key(r: Any) -> Key:
    return Key(entity=r[0], attr=r[1])


Migration = Callable[[sqlite3.Connection], None]
"""A migration upgrades an open database from format ``n`` to ``n + 1``. It runs inside one transaction
(``BEGIN IMMEDIATE`` .. ``COMMIT``) together with the ``user_version`` bump, so it is applied entirely or not
at all. Raise ``StoreError`` to refuse (the transaction is rolled back and the file is left at ``n``)."""


def _run_script(db: sqlite3.Connection, script: str) -> None:
    """Execute a multi-statement script inside the current transaction (``executescript`` would COMMIT)."""
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if any(ln.strip() and not ln.strip().startswith("--") for ln in buf.splitlines()):
                db.execute(buf)
            buf = ""


def _migrate_1_to_2(db: sqlite3.Connection) -> None:
    """Format 1 -> 2 (T-C4, T-C5): the barrier columns and tables, a real outbox, relaxed erasure triggers.

    Format 1 carried the ``completion_jobs`` and ``outbox`` tables without any logic, so they must be empty."""
    for table in ("completion_jobs", "outbox"):
        if db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
            raise StoreError(f"cannot migrate: format-1 table '{table}' is not empty")
    db.execute("ALTER TABLE current_belief ADD COLUMN required_generation INTEGER NOT NULL DEFAULT 0")
    db.execute("ALTER TABLE beliefs ADD COLUMN origin TEXT NOT NULL DEFAULT 'append'")
    db.execute("DROP TABLE completion_jobs")
    db.execute("DROP TABLE outbox")
    for name in ("log_erase_only", "beliefs_flag_only"):
        db.execute(f"DROP TRIGGER IF EXISTS {name}")
    _run_script(db, _DDL_TABLES)  # IF NOT EXISTS: adds marks, dirty_components, and the v2 completion_jobs / outbox
    _run_script(db, _TRIGGERS)


MIGRATIONS: dict[int, Migration] = {1: _migrate_1_to_2}


class SqliteStorage:
    def __init__(self, path: str | Path, *, busy_timeout: float = 5.0) -> None:
        self.path = str(path)
        self._busy_timeout = busy_timeout
        self._mutex = threading.RLock()
        self._in_txn = False
        self._db = sqlite3.connect(self.path, timeout=busy_timeout, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute("PRAGMA synchronous = FULL")
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute(f"PRAGMA busy_timeout = {int(busy_timeout * 1000)}")
        found = self._db.execute("PRAGMA user_version").fetchone()[0]
        if found > STORE_FORMAT_VERSION or found < 0:
            self._db.close()
            raise StoreError(f"store format version {found} is not supported (this build writes {STORE_FORMAT_VERSION})")
        if found == 0:
            self._db.executescript("BEGIN IMMEDIATE;" + _DDL_TABLES + _TRIGGERS + f"PRAGMA user_version = {STORE_FORMAT_VERSION};COMMIT;")
        elif found < STORE_FORMAT_VERSION:
            self._migrate(found)

    def _migrate(self, found: int) -> None:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            v = found
            while v < STORE_FORMAT_VERSION:
                step = MIGRATIONS.get(v)
                if step is None:
                    raise StoreError(f"no migration from store format {v} to {v + 1}")
                step(self._db)
                v += 1
            self._db.execute(f"PRAGMA user_version = {v}")
            self._db.execute("COMMIT")
        except BaseException:
            try:
                self._db.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            self._db.close()
            raise

    # -- transactions
    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._mutex:
            if self._in_txn:
                raise RuntimeError("nested transaction")
            try:
                self._db.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as e:
                if "locked" in str(e) or "busy" in str(e):
                    raise StoreBusy(f"another writer holds the lock (waited {self._busy_timeout}s)") from e
                raise
            self._in_txn = True
            try:
                yield
                self._db.execute("COMMIT")
            except BaseException:
                try:
                    self._db.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            finally:
                self._in_txn = False

    def close(self) -> None:
        with self._mutex:
            self._db.close()

    def _q(self, sql: str, args: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        with self._mutex:
            return self._db.execute(sql, args).fetchall()

    def _x(self, sql: str, args: tuple[Any, ...] = ()) -> None:
        with self._mutex:
            self._db.execute(sql, args)

    # -- meta
    def get_meta(self, name: str) -> str | None:
        r = self._q("SELECT value FROM meta WHERE name = ?", (name,))
        return None if not r else str(r[0][0])

    def set_meta(self, name: str, value: str) -> None:
        self._x("INSERT INTO meta(name, value) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET value = excluded.value", (name, value))

    # -- evidence log
    _LOG_COLS = "lsn, report_id, recorded_us, generation, key_entity, key_attr, content, salt, commitment, prev_hash, entry_hash, idem_key, tomb"

    @staticmethod
    def _log(r: tuple[Any, ...]) -> LogRow:
        return LogRow(
            lsn=r[0], report_id=r[1], recorded_us=r[2], generation=r[3],
            key=None if r[4] is None else Key(entity=r[4], attr=r[5]),
            content=r[6], salt=None if r[7] is None else bytes(r[7]), commitment=r[8], prev_hash=r[9], entry_hash=r[10],
            idem_key=r[11], tomb=r[12],
        )

    def log_head(self) -> LogRow | None:
        r = self._q(f"SELECT {self._LOG_COLS} FROM log ORDER BY lsn DESC LIMIT 1")
        return self._log(r[0]) if r else None

    def put_log(self, row: LogRow) -> None:
        self._x(
            f"INSERT INTO log({self._LOG_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row.lsn, row.report_id, row.recorded_us, row.generation,
             None if row.key is None else row.key.entity, None if row.key is None else row.key.attr,
             row.content, row.salt, row.commitment, row.prev_hash, row.entry_hash, row.idem_key, row.tomb),
        )

    def replace_log(self, row: LogRow) -> None:
        self._x(
            "UPDATE log SET key_entity = ?, key_attr = ?, content = ?, salt = ?, tomb = ? WHERE lsn = ?",
            (None if row.key is None else row.key.entity, None if row.key is None else row.key.attr, row.content, row.salt, row.tomb, row.lsn),
        )

    def _one(self, where: str, arg: Any) -> LogRow | None:
        r = self._q(f"SELECT {self._LOG_COLS} FROM log WHERE {where} = ?", (arg,))
        return self._log(r[0]) if r else None

    def log_by_lsn(self, lsn: int) -> LogRow | None:
        return self._one("lsn", lsn)

    def log_by_report(self, report_id: str) -> LogRow | None:
        return self._one("report_id", report_id)

    def log_by_idem(self, idem_key: str) -> LogRow | None:
        return self._one("idem_key", idem_key)

    def log_range(self, from_lsn: int, to_lsn: int | None) -> Iterator[LogRow]:
        hi = 2**62 if to_lsn is None else to_lsn
        for r in self._q(f"SELECT {self._LOG_COLS} FROM log WHERE lsn >= ? AND lsn <= ? ORDER BY lsn", (from_lsn, hi)):
            yield self._log(r)

    def log_for_key(self, key: Key, to_lsn: int | None) -> list[LogRow]:
        hi = 2**62 if to_lsn is None else to_lsn
        rows = self._q(
            f"SELECT {self._LOG_COLS} FROM log WHERE key_entity = ? AND key_attr = ? AND lsn <= ? ORDER BY lsn",
            (key.entity, key.attr, hi),
        )
        return [self._log(r) for r in rows]

    def lsn_at_us(self, us: int) -> int:
        r = self._q("SELECT MAX(lsn) FROM log WHERE recorded_us <= ?", (us,))
        return 0 if not r or r[0][0] is None else int(r[0][0])

    # -- admissions
    _ADM_COLS = "seq, lsn, report_id, record, report_entry_hash, prev_hash, entry_hash"

    @staticmethod
    def _adm(r: tuple[Any, ...]) -> AdmRow:
        return AdmRow(seq=r[0], lsn=r[1], report_id=r[2], record=r[3], report_entry_hash=r[4], prev_hash=r[5], entry_hash=r[6])

    def adm_head(self) -> AdmRow | None:
        r = self._q(f"SELECT {self._ADM_COLS} FROM admissions ORDER BY seq DESC LIMIT 1")
        return self._adm(r[0]) if r else None

    def adm_by_seq(self, seq: int) -> AdmRow | None:
        r = self._q(f"SELECT {self._ADM_COLS} FROM admissions WHERE seq = ?", (seq,))
        return self._adm(r[0]) if r else None

    def put_adm(self, row: AdmRow) -> None:
        self._x(
            f"INSERT INTO admissions({self._ADM_COLS}) VALUES (?,?,?,?,?,?,?)",
            (row.seq, row.lsn, row.report_id, row.record, row.report_entry_hash, row.prev_hash, row.entry_hash),
        )

    def adm_for_report(self, report_id: str) -> list[AdmRow]:
        return [self._adm(r) for r in self._q(f"SELECT {self._ADM_COLS} FROM admissions WHERE report_id = ? ORDER BY seq", (report_id,))]

    def adm_for_lsn(self, lsn: int) -> list[AdmRow]:
        return [self._adm(r) for r in self._q(f"SELECT {self._ADM_COLS} FROM admissions WHERE lsn = ? ORDER BY seq", (lsn,))]

    def adm_range(self) -> Iterator[AdmRow]:
        for r in self._q(f"SELECT {self._ADM_COLS} FROM admissions ORDER BY seq"):
            yield self._adm(r)

    # -- beliefs
    def current_version(self, key: Key) -> int | None:
        r = self._q("SELECT version FROM current_belief WHERE entity = ? AND attr = ? AND version > 0", (key.entity, key.attr))
        return None if not r else int(r[0][0])

    def put_belief(self, row: BeliefRow) -> None:
        k = row.key
        self._x(
            "INSERT INTO beliefs(entity, attr, version, lsn, recorded_us, required_generation, completed_generation, belief, reconstructable, origin) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (k.entity, k.attr, row.version, row.lsn, row.recorded_us, row.required_generation, row.completed_generation, row.belief, 1 if row.reconstructable else 0, row.origin),
        )
        for rid in row.pins:
            self._x("INSERT INTO belief_pins(entity, attr, version, report_id) VALUES (?,?,?,?)", (k.entity, k.attr, row.version, rid))
        for dk, dv in row.deps:
            self._x(
                "INSERT INTO belief_deps(entity, attr, version, dep_entity, dep_attr, dep_version) VALUES (?,?,?,?,?,?)",
                (k.entity, k.attr, row.version, dk.entity, dk.attr, dv),
            )

    def set_current(self, key: Key, version: int) -> None:
        self._x(
            "INSERT INTO current_belief(entity, attr, version) VALUES (?,?,?) ON CONFLICT(entity, attr) DO UPDATE SET version = excluded.version",
            (key.entity, key.attr, version),
        )

    _BELIEF_COLS = "entity, attr, version, lsn, recorded_us, required_generation, completed_generation, belief, reconstructable, origin"

    def _belief(self, r: tuple[Any, ...]) -> BeliefRow:
        key = _key(r)
        pins = tuple(x[0] for x in self._q("SELECT report_id FROM belief_pins WHERE entity=? AND attr=? AND version=? ORDER BY report_id", (r[0], r[1], r[2])))
        deps = tuple(
            (Key(entity=x[0], attr=x[1]), int(x[2]))
            for x in self._q("SELECT dep_entity, dep_attr, dep_version FROM belief_deps WHERE entity=? AND attr=? AND version=? ORDER BY dep_entity, dep_attr", (r[0], r[1], r[2]))
        )
        return BeliefRow(
            key=key, version=r[2], lsn=r[3], recorded_us=r[4], required_generation=r[5], completed_generation=r[6],
            belief=r[7], pins=pins, deps=deps, reconstructable=bool(r[8]), origin=r[9],
        )

    def belief_row(self, key: Key, version: int) -> BeliefRow | None:
        r = self._q(f"SELECT {self._BELIEF_COLS} FROM beliefs WHERE entity=? AND attr=? AND version=?", (key.entity, key.attr, version))
        return self._belief(r[0]) if r else None

    def belief_row_as_of(self, key: Key, lsn: int) -> BeliefRow | None:
        r = self._q(
            f"SELECT {self._BELIEF_COLS} FROM beliefs WHERE entity=? AND attr=? AND lsn <= ? ORDER BY version DESC LIMIT 1",
            (key.entity, key.attr, lsn),
        )
        return self._belief(r[0]) if r else None

    def belief_rows_at_lsn(self, lsn: int) -> list[BeliefRow]:
        return [self._belief(r) for r in self._q(f"SELECT {self._BELIEF_COLS} FROM beliefs WHERE lsn = ? ORDER BY entity, attr", (lsn,))]

    def current_keys(self) -> list[Key]:
        return [_key(r) for r in self._q("SELECT entity, attr FROM current_belief WHERE version > 0 ORDER BY entity, attr")]

    def key_dependents(self, key: Key) -> list[Key]:
        rows = self._q(
            "SELECT DISTINCT d.entity, d.attr FROM belief_deps d JOIN current_belief c "
            "ON c.entity = d.entity AND c.attr = d.attr AND c.version = d.version "
            "WHERE d.dep_entity = ? AND d.dep_attr = ?",
            (key.entity, key.attr),
        )
        return [_key(r) for r in rows]

    def versions_pinning(self, report_id: str) -> list[tuple[Key, int]]:
        rows = self._q("SELECT entity, attr, version FROM belief_pins WHERE report_id = ? ORDER BY entity, attr, version", (report_id,))
        return [(_key(r), int(r[2])) for r in rows]

    def mark_unreconstructable(self, key: Key, version: int) -> None:
        self._x("UPDATE beliefs SET reconstructable = 0 WHERE entity=? AND attr=? AND version=?", (key.entity, key.attr, version))

    def max_belief_lsn(self) -> int:
        r = self._q("SELECT MAX(lsn) FROM beliefs")
        return 0 if not r or r[0][0] is None else int(r[0][0])

    def redact_belief(self, key: Key, version: int, redacted: str) -> None:
        self._x(
            "UPDATE beliefs SET belief = ?, reconstructable = 0 WHERE entity=? AND attr=? AND version=?",
            (redacted, key.entity, key.attr, version),
        )

    def belief_rows_for_key(self, key: Key, max_lsn: int | None) -> list[BeliefRow]:
        hi = 2**62 if max_lsn is None else max_lsn
        rows = self._q(f"SELECT {self._BELIEF_COLS} FROM beliefs WHERE entity=? AND attr=? AND lsn <= ? ORDER BY version DESC", (key.entity, key.attr, hi))
        return [self._belief(r) for r in rows]

    # -- generation barrier (T-C4)
    def required_generation(self, key: Key) -> int:
        r = self._q("SELECT required_generation FROM current_belief WHERE entity = ? AND attr = ?", (key.entity, key.attr))
        return 0 if not r else int(r[0][0])

    def set_required(self, key: Key, generation: int) -> None:
        self._x(
            "INSERT INTO current_belief(entity, attr, version, required_generation) VALUES (?,?,0,?) "
            "ON CONFLICT(entity, attr) DO UPDATE SET required_generation = MAX(required_generation, excluded.required_generation)",
            (key.entity, key.attr, generation),
        )

    def put_mark(self, row: MarkRow) -> None:
        self._x("INSERT OR IGNORE INTO marks(entity, attr, generation, lsn) VALUES (?,?,?,?)", (row.key.entity, row.key.attr, row.generation, row.lsn))

    def required_at(self, key: Key, lsn: int) -> int:
        r = self._q("SELECT MAX(generation) FROM marks WHERE entity = ? AND attr = ? AND lsn <= ?", (key.entity, key.attr, lsn))
        return 0 if not r or r[0][0] is None else int(r[0][0])

    def marked_keys(self) -> list[Key]:
        return [_key(r) for r in self._q("SELECT entity, attr FROM current_belief WHERE required_generation > 0 ORDER BY entity, attr")]

    @staticmethod
    def _job(r: tuple[Any, ...]) -> JobRow:
        return JobRow(generation=r[0], state=r[1], payload=r[2], created_us=r[3], updated_us=r[4])

    def put_job(self, row: JobRow) -> None:
        self._x(
            "INSERT INTO completion_jobs(generation, state, payload, created_us, updated_us) VALUES (?,?,?,?,?)",
            (row.generation, row.state, row.payload, row.created_us, row.updated_us),
        )

    def replace_job(self, row: JobRow) -> None:
        self._x("UPDATE completion_jobs SET state = ?, payload = ?, updated_us = ? WHERE generation = ?", (row.state, row.payload, row.updated_us, row.generation))

    def jobs(self, state: str | None = None) -> list[JobRow]:
        if state is None:
            rows = self._q("SELECT generation, state, payload, created_us, updated_us FROM completion_jobs ORDER BY generation")
        else:
            rows = self._q("SELECT generation, state, payload, created_us, updated_us FROM completion_jobs WHERE state = ? ORDER BY generation", (state,))
        return [self._job(r) for r in rows]

    def put_dirty(self, row: DirtyRow) -> None:
        self._x("INSERT OR IGNORE INTO dirty_components(generation, attrs, set_lsn, cleared_lsn) VALUES (?,?,?,?)", (row.generation, row.attrs, row.set_lsn, row.cleared_lsn))

    def dirty_rows(self) -> list[DirtyRow]:
        rows = self._q("SELECT generation, attrs, set_lsn, cleared_lsn FROM dirty_components ORDER BY generation, attrs")
        return [DirtyRow(generation=r[0], attrs=r[1], set_lsn=r[2], cleared_lsn=r[3]) for r in rows]

    def clear_dirty(self, generation: int, cleared_lsn: int) -> None:
        self._x("UPDATE dirty_components SET cleared_lsn = ? WHERE generation = ? AND cleared_lsn IS NULL", (cleared_lsn, generation))

    # -- subscriptions and outbox (T-C5)
    def put_subscription(self, plan_id: str, key: Key) -> None:
        self._x("INSERT OR IGNORE INTO subscriptions(plan_id, entity, attr) VALUES (?,?,?)", (plan_id, key.entity, key.attr))

    def delete_subscriptions(self, plan_id: str) -> None:
        self._x("DELETE FROM subscriptions WHERE plan_id = ?", (plan_id,))

    def subscriptions_for_plan(self, plan_id: str) -> list[Key]:
        return [_key(r) for r in self._q("SELECT entity, attr FROM subscriptions WHERE plan_id = ? ORDER BY entity, attr", (plan_id,))]

    def plans_for_key(self, key: Key) -> list[str]:
        return [r[0] for r in self._q("SELECT plan_id FROM subscriptions WHERE entity = ? AND attr = ? ORDER BY plan_id", (key.entity, key.attr))]

    _OUTBOX_COLS = "event_id, plan_id, entity, attr, old_version, new_version, lsn, payload, created_us, delivered_us"

    @staticmethod
    def _outbox(r: tuple[Any, ...]) -> OutboxRow:
        return OutboxRow(
            event_id=r[0], plan_id=r[1], key=Key(entity=r[2], attr=r[3]), old_version=r[4], new_version=r[5], lsn=r[6],
            payload=r[7], created_us=r[8], delivered_us=r[9],
        )

    def put_outbox(self, row: OutboxRow) -> None:
        self._x(
            f"INSERT OR IGNORE INTO outbox({self._OUTBOX_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (row.event_id, row.plan_id, row.key.entity, row.key.attr, row.old_version, row.new_version, row.lsn, row.payload, row.created_us, row.delivered_us),
        )

    def outbox_pending(self, limit: int) -> list[OutboxRow]:
        rows = self._q(f"SELECT {self._OUTBOX_COLS} FROM outbox WHERE delivered_us IS NULL ORDER BY seq LIMIT ?", (limit,))
        return [self._outbox(r) for r in rows]

    def outbox_all(self) -> list[OutboxRow]:
        return [self._outbox(r) for r in self._q(f"SELECT {self._OUTBOX_COLS} FROM outbox ORDER BY seq")]

    def ack_outbox(self, event_id: str, plan_id: str, delivered_us: int) -> None:
        self._x("UPDATE outbox SET delivered_us = ? WHERE event_id = ? AND plan_id = ?", (delivered_us, event_id, plan_id))

    def redact_outbox(self, key: Key, versions: tuple[int, ...]) -> None:
        if not versions:
            return
        marks = ",".join("?" for _ in versions)
        self._x(
            f"UPDATE outbox SET payload = NULL WHERE entity = ? AND attr = ? AND (new_version IN ({marks}) OR old_version IN ({marks}))",
            (key.entity, key.attr, *versions, *versions),
        )

    # -- inputs
    def put_input(self, row: InputRow) -> None:
        self._x(
            "INSERT INTO inputs(kind, version, effective_lsn, recorded_us, payload) VALUES (?,?,?,?,?)",
            (row.kind, row.version, row.effective_lsn, row.recorded_us, row.payload),
        )

    @staticmethod
    def _input(r: tuple[Any, ...]) -> InputRow:
        return InputRow(kind=r[0], version=r[1], effective_lsn=r[2], recorded_us=r[3], payload=r[4])

    def input_row(self, kind: str, version: int) -> InputRow | None:
        r = self._q("SELECT kind, version, effective_lsn, recorded_us, payload FROM inputs WHERE kind=? AND version=?", (kind, version))
        return self._input(r[0]) if r else None

    def input_row_at(self, kind: str, lsn: int) -> InputRow | None:
        r = self._q(
            "SELECT kind, version, effective_lsn, recorded_us, payload FROM inputs WHERE kind=? AND effective_lsn <= ? ORDER BY version DESC LIMIT 1",
            (kind, lsn),
        )
        return self._input(r[0]) if r else None

    def inputs_all(self) -> list[InputRow]:
        rows = self._q("SELECT kind, version, effective_lsn, recorded_us, payload FROM inputs ORDER BY kind, version")
        return [self._input(r) for r in rows]

    def latest_input_version(self, kind: str) -> int | None:
        r = self._q("SELECT MAX(version) FROM inputs WHERE kind = ?", (kind,))
        return None if not r or r[0][0] is None else int(r[0][0])

    def put_attr_dependents(self, schema_version: int, mapping: dict[str, tuple[str, ...]]) -> None:
        for attr, deps in mapping.items():
            for d in deps:
                self._x("INSERT OR IGNORE INTO attr_dependents(schema_version, attr, dependent) VALUES (?,?,?)", (schema_version, attr, d))

    def attr_dependents(self, schema_version: int, attr: str) -> tuple[str, ...]:
        rows = self._q("SELECT dependent FROM attr_dependents WHERE schema_version = ? AND attr = ? ORDER BY dependent", (schema_version, attr))
        return tuple(r[0] for r in rows)


class SQLiteBackend(Engine):
    """Default backend. ``store_secret`` (host-supplied, never written to the database) enables ``erase``."""

    def __init__(
        self,
        path: str | Path,
        *,
        chain: bool = True,
        store_secret: bytes | None = None,
        busy_timeout: float = 5.0,
        clock: Callable[[], datetime] | None = None,
        fault: FaultHook | None = None,
        ids: UlidFactory | None = None,
        traversal_budget: int = DEFAULT_TRAVERSAL_BUDGET,
    ) -> None:
        self.storage = SqliteStorage(path, busy_timeout=busy_timeout)
        super().__init__(
            self.storage, chain_enabled=chain, store_secret=store_secret, clock=clock, fault=fault, ids=ids,
            traversal_budget=traversal_budget,
        )
