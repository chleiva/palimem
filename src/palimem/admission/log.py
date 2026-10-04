"""The read-only view of the evidence log that admission needs.

The store lane implements :class:`LogView` over its backend; :class:`ListLog` is a small in-memory
implementation for tests and for hand-built streams. Admission never writes to the log and never
reads anything but entries at or below the requested LSN (the belief axis, S-05).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol

from palimem.types import LogEntry


class LogView(Protocol):
    def get(self, report_id: str) -> LogEntry | None: ...

    def entries(self, *, upto_lsn: int | None = None) -> Sequence[LogEntry]:
        """Entries in ascending LSN order, restricted to ``lsn <= upto_lsn`` when given."""
        ...


class ListLog:
    """In-memory log over a list of already-sequenced :class:`LogEntry` (LSNs strictly increasing)."""

    def __init__(self, entries: Iterable[LogEntry] = ()) -> None:
        self._entries: list[LogEntry] = []
        self._by_id: dict[str, LogEntry] = {}
        for e in entries:
            self.add(e)

    def add(self, entry: LogEntry) -> None:
        if self._entries and entry.lsn <= self._entries[-1].lsn:
            raise ValueError("ListLog: LSNs must be strictly increasing")
        rid = entry.report.id
        assert rid is not None
        if rid in self._by_id:
            raise ValueError(f"ListLog: duplicate report id {rid}")
        self._entries.append(entry)
        self._by_id[rid] = entry

    def get(self, report_id: str) -> LogEntry | None:
        return self._by_id.get(report_id)

    def entries(self, *, upto_lsn: int | None = None) -> Sequence[LogEntry]:
        if upto_lsn is None:
            return tuple(self._entries)
        return tuple(e for e in self._entries if e.lsn <= upto_lsn)
