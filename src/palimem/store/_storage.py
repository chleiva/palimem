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
    idem_key: str  # client key; after an erasure it is replaced by an HMAC reference (S-13)
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
    origin: str = "append"  # append | completion | repair: why this version exists (T-C4, T-C8)


@dataclass(frozen=True, kw_only=True)
class InputRow:
    kind: str
    version: int
    effective_lsn: int  # applies to appends with lsn >= effective_lsn
    recorded_us: int
    payload: str  # canonical JSON


@dataclass(frozen=True, kw_only=True)
class MarkRow:
    """A generation named a key (T-C4): the append-only history behind ``required_generation``."""

    key: Key
    generation: int
    lsn: int


@dataclass(frozen=True, kw_only=True)
class JobRow:
    """A durable completion job, keyed by generation (T-C4)."""

    generation: int
    state: str  # pending | done
    payload: str  # canonical JSON: {kind, lsn, seeds, keys}; keys are cleared when the job is done
    created_us: int
    updated_us: int


@dataclass(frozen=True, kw_only=True)
class DirtyRow:
    """A dirty marker (T-C4): reads of the listed attributes answer ``store_dirty`` between ``set_lsn``
    and ``cleared_lsn``. ``attrs`` is canonical JSON: a list of attribute names, or ``["*"]`` (store-wide)."""

    generation: int
    attrs: str
    set_lsn: int
    cleared_lsn: int | None = None


@dataclass(frozen=True, kw_only=True)
class OutboxRow:
    event_id: str
    plan_id: str
    key: Key
    old_version: int | None
    new_version: int
    lsn: int
    payload: str | None  # canonical JSON {old, new} belief views; None once redacted by an erasure
    created_us: int
    delivered_us: int | None = None


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
    def redact_belief(self, key: Key, version: int, redacted: str) -> None:
        """Replace a version's JSON by a redacted record and flag it not reconstructable (erasure only)."""
        ...

    def max_belief_lsn(self) -> int: ...
    def belief_rows_for_key(self, key: Key, max_lsn: int | None) -> list[BeliefRow]:
        """Versions of a key with ``lsn <= max_lsn``, newest first."""
        ...

    # generation barrier (T-C4)
    def required_generation(self, key: Key) -> int:
        """Newest generation that names the key (0 if none): a column on the current-version index."""
        ...

    def set_required(self, key: Key, generation: int) -> None:
        """Raise the key's required generation (creates a version-0 placeholder if the key has no belief yet)."""
        ...

    def put_mark(self, row: MarkRow) -> None: ...
    def required_at(self, key: Key, lsn: int) -> int:
        """Greatest generation that named the key at a log position ``<= lsn`` (0 if none)."""
        ...

    def marked_keys(self) -> list[Key]: ...
    def put_job(self, row: JobRow) -> None: ...
    def replace_job(self, row: JobRow) -> None: ...
    def jobs(self, state: str | None = None) -> list[JobRow]: ...
    def put_dirty(self, row: DirtyRow) -> None: ...
    def dirty_rows(self) -> list[DirtyRow]: ...
    def clear_dirty(self, generation: int, cleared_lsn: int) -> None: ...

    # subscriptions and outbox (T-C5)
    def put_subscription(self, plan_id: str, key: Key) -> None: ...
    def delete_subscriptions(self, plan_id: str) -> None: ...
    def subscriptions_for_plan(self, plan_id: str) -> list[Key]: ...
    def plans_for_key(self, key: Key) -> list[str]: ...
    def put_outbox(self, row: OutboxRow) -> None: ...
    def outbox_pending(self, limit: int) -> list[OutboxRow]: ...
    def outbox_all(self) -> list[OutboxRow]: ...
    def ack_outbox(self, event_id: str, plan_id: str, delivered_us: int) -> None: ...
    def redact_outbox(self, key: Key, versions: tuple[int, ...]) -> None:
        """Drop the embedded views of events that mention these (key, version) pairs (erasure)."""
        ...

    # versioned inputs
    def put_input(self, row: InputRow) -> None: ...
    def input_row(self, kind: str, version: int) -> InputRow | None: ...
    def input_row_at(self, kind: str, lsn: int) -> InputRow | None: ...
    def latest_input_version(self, kind: str) -> int | None: ...
    def inputs_all(self) -> list[InputRow]: ...
    def put_attr_dependents(self, schema_version: int, mapping: dict[str, tuple[str, ...]]) -> None: ...
    def attr_dependents(self, schema_version: int, attr: str) -> tuple[str, ...]: ...
