"""A cached, read-only log view over a :class:`~palimem.store.StoreView` for admission and revision.

Admission needs the whole log prefix at every append, which would re-decode every stored row each time. The
committed prefix of the log is immutable (except for erasure, which the host reports through
:meth:`ViewLog.invalidate`), so the decoded entries are kept and only the new tail is fetched.

A rolled-back append leaves a cached entry whose row no longer exists: before each use the cache checks that its
last entry is still the committed row at that LSN and truncates otherwise.
"""

from __future__ import annotations

from collections.abc import Sequence

from palimem.store import StoreView
from palimem.types import LogEntry


class ViewLog:
    """Implements :class:`palimem.admission.LogView` over a store view."""

    def __init__(self, view: StoreView) -> None:
        self._view = view
        self._entries: list[LogEntry] = []
        self._by_id: dict[str, LogEntry] = {}

    def invalidate(self) -> None:
        """Forget everything (an erasure replaced rows by tombstones)."""
        self._entries.clear()
        self._by_id.clear()

    def rebind(self, view: StoreView) -> None:
        self._view = view

    def _revalidate(self) -> None:
        while self._entries:
            last = self._entries[-1]
            rid = last.report.id
            assert rid is not None
            row = self._view.get_entry(rid)
            if isinstance(row, LogEntry) and row.lsn == last.lsn:
                return
            self._entries.pop()
            self._by_id.pop(rid, None)

    def _fill(self, upto: int) -> None:
        self._revalidate()
        have = self._entries[-1].lsn if self._entries else 0
        if upto <= have:
            return
        for row in self._view.scan(have + 1, upto):
            if isinstance(row, LogEntry):
                self._entries.append(row)
                rid = row.report.id
                assert rid is not None
                self._by_id[rid] = row

    def get(self, report_id: str) -> LogEntry | None:
        self._fill(self._view.head().lsn)
        return self._by_id.get(report_id)

    def head_lsn(self) -> int:
        """The committed head of the log (what a rolled-back append never reached)."""
        return self._view.head().lsn

    def entries(self, *, upto_lsn: int | None = None) -> Sequence[LogEntry]:
        head = self._view.head().lsn
        upto = head if upto_lsn is None else min(upto_lsn, head)
        self._fill(upto)
        if upto >= (self._entries[-1].lsn if self._entries else 0):
            return tuple(self._entries)
        return tuple(e for e in self._entries if e.lsn <= upto)
