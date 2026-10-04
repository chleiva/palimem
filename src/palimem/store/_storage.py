"""Low-level storage primitives shared by the engine (internal; not part of the public contract).

Two implementations exist (``MemoryStorage``, ``SqliteStorage``). The :class:`~palimem.store.engine.Engine`
implements the whole backend semantics once, on top of these primitives, so both backends are
exercised by the same contract tests and cannot drift.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from palimem.types import Key


@dataclass(frozen=True, kw_only=True)
class LogRow:
    lsn: int
    report_id: str
    recorded_us: int
    generation: int
    key: Key | None  # plain key index; set to None on erasure (a key can itself be sensitive, T-29)
    content: str | None  # canonical Report JSON without id; None after erasure
    salt: bytes | None  # per-row salt; erased with the content (S-13). None on a chainless backend
    commitment: str | None
    prev_hash: str | None
    entry_hash: str | None
    idem_key: str
    tomb: str | None = None  # canonical Tombstone JSON once erased


@dataclass(frozen=True, kw_only=True)
class AdmRow:
    seq: int
    lsn: int  # log head when the decision was made
    report_id: str
    record: str  # canonical AdmissionRecord JSON
    report_entry_hash: str | None
    prev_hash: str | None
    entry_hash: str | None


@dataclass(frozen=True, kw_only=True)
class BeliefRow:
    key: Key
    version: int
    lsn: int
    recorded_us: int
    required_generation: int
    completed_generation: int
    belief: str  # canonical Belief JSON
    pins: tuple[str, ...]  # report ids pinned by this version
    deps: tuple[tuple[Key, int], ...]  # (key, version) it depends on
    reconstructable: bool = True


@dataclass(frozen=True, kw_only=True)
class InputRow:
    kind: str
    version: int
    effective_lsn: int  # applies to appends with lsn >= effective_lsn
    recorded_us: int
    payload: str  # canonical JSON


class Storage(Protocol):
    def transaction(self) -> AbstractContextManager[None]:
        """Atomic write transaction holding the single-writer lock. Not re-entrant."""
        ...

    def close(self) -> None: ...

    # meta
    def get_meta(self, name: str) -> str | None: ...
    def set_meta(self, name: str, value: str) -> None: ...

    # evidence log
    def log_head(self) -> LogRow | None: ...
    def put_log(self, row: LogRow) -> None: ...
    def replace_log(self, row: LogRow) -> None:
        """Replace a row in place (erasure only)."""
        ...

    def log_by_lsn(self, lsn: int) -> LogRow | None: ...
    def log_by_report(self, report_id: str) -> LogRow | None: ...
    def log_by_idem(self, idem_key: str) -> LogRow | None: ...
    def log_range(self, from_lsn: int, to_lsn: int | None) -> Iterator[LogRow]: ...
    def log_for_key(self, key: Key, to_lsn: int | None) -> list[LogRow]: ...
    def lsn_at_us(self, us: int) -> int: ...

    # admission log
    def adm_head(self) -> AdmRow | None: ...
    def adm_by_seq(self, seq: int) -> AdmRow | None: ...
    def put_adm(self, row: AdmRow) -> None: ...
    def adm_for_report(self, report_id: str) -> list[AdmRow]: ...
    def adm_for_lsn(self, lsn: int) -> list[AdmRow]: ...
    def adm_range(self) -> Iterator[AdmRow]: ...

    # beliefs and indexes
    def current_version(self, key: Key) -> int | None: ...
    def put_belief(self, row: BeliefRow) -> None: ...
    def set_current(self, key: Key, version: int) -> None: ...
    def belief_row(self, key: Key, version: int) -> BeliefRow | None: ...
    def belief_row_as_of(self, key: Key, lsn: int) -> BeliefRow | None: ...
    def belief_rows_at_lsn(self, lsn: int) -> list[BeliefRow]: ...
    def current_keys(self) -> list[Key]: ...
    def key_dependents(self, key: Key) -> list[Key]: ...
    def versions_pinning(self, report_id: str) -> list[tuple[Key, int]]: ...
    def mark_unreconstructable(self, key: Key, version: int) -> None: ...
    def max_belief_lsn(self) -> int: ...

    # versioned inputs
    def put_input(self, row: InputRow) -> None: ...
    def input_row(self, kind: str, version: int) -> InputRow | None: ...
    def input_row_at(self, kind: str, lsn: int) -> InputRow | None: ...
    def latest_input_version(self, kind: str) -> int | None: ...
    def put_attr_dependents(self, schema_version: int, mapping: dict[str, tuple[str, ...]]) -> None: ...
    def attr_dependents(self, schema_version: int, attr: str) -> tuple[str, ...]: ...
