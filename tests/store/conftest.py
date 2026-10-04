"""Backend fixtures: the same contract tests run against every backend."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from palimem.store import Backend, InMemoryBackend, SQLiteBackend
from palimem.store.memory import MemoryStorage
from palimem.store.sqlite import SqliteStorage

SECRET = b"test-store-secret-never-in-the-db"


class Clock:
    """Deterministic, manually advanced clock (microsecond steps)."""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 5, 9, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def tick(self, seconds: float = 1.0) -> datetime:
        self.now += timedelta(seconds=seconds)
        return self.now


class Harness:
    """A backend plus what the tests need around it (the way to tamper with storage directly)."""

    def __init__(self, kind: str, backend: Backend, clock: Clock, path: Path | None, make: Callable[..., Backend]) -> None:
        self.kind = kind
        self.backend = backend
        self.clock = clock
        self.path = path
        self.make = make  # reopen / build a sibling backend on the same storage (sqlite only)

    # -- direct tampering, bypassing the engine (what an attacker with the file can do)
    def tamper(self, sql_or_fn: Any, *args: Any) -> None:
        if self.kind == "sqlite":
            assert self.path is not None
            self.backend.close()
            db = sqlite3.connect(self.path, isolation_level=None)
            for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
                db.execute(f'DROP TRIGGER IF EXISTS "{name}"')  # an attacker with file access can drop the guards
            if args:
                db.execute(sql_or_fn, args)
            else:
                db.executescript(sql_or_fn)  # several statements
            db.close()
            self.backend = self.make(path=self.path)
        else:
            sql_or_fn(self.backend.storage)  # type: ignore[attr-defined]


@pytest.fixture(params=["memory", "sqlite"])
def h(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Harness]:
    clock = Clock()
    kind = request.param
    if kind == "memory":
        def make_mem(**kw: Any) -> Backend:
            return InMemoryBackend(store_secret=SECRET, clock=clock, **kw)

        hh = Harness("memory", make_mem(), clock, None, make_mem)
    else:
        db = tmp_path / "palimem.db"

        def make_sql(**kw: Any) -> Backend:
            kw.setdefault("path", db)
            return SQLiteBackend(store_secret=SECRET, clock=clock, **kw)

        hh = Harness("sqlite", make_sql(), clock, db, make_sql)
    yield hh
    hh.backend.close()


@pytest.fixture
def storage_types() -> tuple[type, type]:
    return MemoryStorage, SqliteStorage
