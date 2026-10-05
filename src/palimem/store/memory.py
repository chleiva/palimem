"""InMemoryBackend: the reference implementation of the backend interface (T-C1).

Same engine as the SQLite backend over plain containers. A transaction snapshots the containers
(rows are immutable, so a shallow copy is enough) and restores them if anything raises, so a fault at
any step of an append leaves no trace, exactly as in SQLite.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from typing import Any

from palimem.store._storage import (
    AdmRow,
    BeliefRow,
    DirtyRow,
    EntityRewriter,
    InputRow,
    JobRow,
    LogRow,
    MarkRow,
    OutboxRow,
)
from palimem.store.backend import FaultHook
from palimem.store.engine import DEFAULT_TRAVERSAL_BUDGET, Engine
from palimem.store.ids import UlidFactory
from palimem.types import Key


class MemoryStorage:
    def __init__(self) -> None:
        self.meta: dict[str, str] = {}
        self.log: dict[int, LogRow] = {}
        self.adm: dict[int, AdmRow] = {}
        self.beliefs: dict[tuple[str, str, int], BeliefRow] = {}
        self.current: dict[Key, int] = {}
        self.inputs: dict[tuple[str, int], InputRow] = {}
        self.attr_deps: dict[tuple[int, str], tuple[str, ...]] = {}
        self.required: dict[Key, int] = {}
        self.marks: list[MarkRow] = []
        self.job_rows: dict[int, JobRow] = {}
        self.dirty: list[DirtyRow] = []
        self.subs: set[tuple[str, Key]] = set()
        self.outbox: dict[tuple[str, str], OutboxRow] = {}
        self._in_txn = False
        self._write_lock = threading.RLock()

    # -- transactions
    def _snapshot(self) -> tuple[Any, ...]:
        return (
            dict(self.meta), dict(self.log), dict(self.adm), dict(self.beliefs), dict(self.current), dict(self.inputs),
            dict(self.attr_deps), dict(self.required), list(self.marks), dict(self.job_rows), list(self.dirty),
            set(self.subs), dict(self.outbox),
        )

    def _restore(self, snap: tuple[Any, ...]) -> None:
        (
            self.meta, self.log, self.adm, self.beliefs, self.current, self.inputs, self.attr_deps, self.required,
            self.marks, self.job_rows, self.dirty, self.subs, self.outbox,
        ) = snap

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._write_lock:
            if self._in_txn:
                raise RuntimeError("nested transaction")
            snap = self._snapshot()
            self._in_txn = True
            try:
                yield
            except BaseException:
                self._restore(snap)
                raise
            finally:
                self._in_txn = False

    def close(self) -> None:
        return None

    # -- meta
    def get_meta(self, name: str) -> str | None:
        return self.meta.get(name)

    def set_meta(self, name: str, value: str) -> None:
        self.meta[name] = value

    # -- evidence log
    def log_head(self) -> LogRow | None:
        return self.log[max(self.log)] if self.log else None

    def put_log(self, row: LogRow) -> None:
        if row.lsn in self.log or any(r.report_id == row.report_id or r.idem_key == row.idem_key for r in self.log.values()):
            raise ValueError("duplicate log row")
        self.log[row.lsn] = row

    def replace_log(self, row: LogRow) -> None:
        self.log[row.lsn] = row

    def log_by_lsn(self, lsn: int) -> LogRow | None:
        return self.log.get(lsn)

    def log_by_report(self, report_id: str) -> LogRow | None:
        return next((r for r in self.log.values() if r.report_id == report_id), None)

    def log_by_idem(self, idem_key: str) -> LogRow | None:
        return next((r for r in self.log.values() if r.idem_key == idem_key), None)

    def log_range(self, from_lsn: int, to_lsn: int | None) -> Iterator[LogRow]:
        for lsn in sorted(self.log):
            if lsn >= from_lsn and (to_lsn is None or lsn <= to_lsn):
                yield self.log[lsn]

    def log_for_key(self, key: Key, to_lsn: int | None) -> list[LogRow]:
        return [r for r in self.log_range(1, to_lsn) if r.key == key]

    def lsn_at_us(self, us: int) -> int:
        eligible = [r.lsn for r in self.log.values() if r.recorded_us <= us]
        return max(eligible) if eligible else 0

    # -- admissions
    def adm_head(self) -> AdmRow | None:
        return self.adm[max(self.adm)] if self.adm else None

    def adm_by_seq(self, seq: int) -> AdmRow | None:
        return self.adm.get(seq)

    def put_adm(self, row: AdmRow) -> None:
        self.adm[row.seq] = row

    def adm_for_report(self, report_id: str) -> list[AdmRow]:
        return [r for _, r in sorted(self.adm.items()) if r.report_id == report_id]

    def adm_for_lsn(self, lsn: int) -> list[AdmRow]:
        return [r for _, r in sorted(self.adm.items()) if r.lsn == lsn]

    def adm_range(self) -> Iterator[AdmRow]:
        for seq in sorted(self.adm):
            yield self.adm[seq]

    # -- beliefs
    def current_version(self, key: Key) -> int | None:
        v = self.current.get(key)
        return v if v else None  # version 0 is a placeholder created by set_required

    def put_belief(self, row: BeliefRow) -> None:
        k = (row.key.entity, row.key.attr, row.version)
        if k in self.beliefs:
            raise ValueError("belief versions are append-only")
        self.beliefs[k] = row

    def set_current(self, key: Key, version: int) -> None:
        self.current[key] = version

    def belief_row(self, key: Key, version: int) -> BeliefRow | None:
        return self.beliefs.get((key.entity, key.attr, version))

    def belief_row_as_of(self, key: Key, lsn: int) -> BeliefRow | None:
        rows = [r for r in self.beliefs.values() if r.key == key and r.lsn <= lsn]
        return max(rows, key=lambda r: r.version) if rows else None

    def belief_rows_at_lsn(self, lsn: int) -> list[BeliefRow]:
        return sorted((r for r in self.beliefs.values() if r.lsn == lsn), key=lambda r: (r.key.entity, r.key.attr))

    def current_keys(self) -> list[Key]:
        return sorted((k for k, v in self.current.items() if v), key=lambda k: (k.entity, k.attr))

    def key_dependents(self, key: Key) -> list[Key]:
        out = []
        for k, v in self.current.items():
            if not v:
                continue
            row = self.beliefs.get((k.entity, k.attr, v))
            if row is not None and any(dep_key == key for dep_key, _ in row.deps):
                out.append(k)
        return out

    def versions_pinning(self, report_id: str) -> list[tuple[Key, int]]:
        return sorted(((r.key, r.version) for r in self.beliefs.values() if report_id in r.pins), key=lambda kv: (kv[0].entity, kv[0].attr, kv[1]))

    def mark_unreconstructable(self, key: Key, version: int) -> None:
        k = (key.entity, key.attr, version)
        if k in self.beliefs:
            self.beliefs[k] = replace(self.beliefs[k], reconstructable=False)

    def redact_belief(self, key: Key, version: int, redacted: str) -> None:
        k = (key.entity, key.attr, version)
        if k in self.beliefs:
            self.beliefs[k] = replace(self.beliefs[k], belief=redacted, reconstructable=False)

    def max_belief_lsn(self) -> int:
        return max((r.lsn for r in self.beliefs.values()), default=0)

    def belief_rows_for_key(self, key: Key, max_lsn: int | None) -> list[BeliefRow]:
        rows = [r for r in self.beliefs.values() if r.key == key and (max_lsn is None or r.lsn <= max_lsn)]
        return sorted(rows, key=lambda r: -r.version)

    # -- generation barrier (T-C4)
    def required_generation(self, key: Key) -> int:
        return self.required.get(key, 0)

    def set_required(self, key: Key, generation: int) -> None:
        self.required[key] = max(self.required.get(key, 0), generation)
        self.current.setdefault(key, 0)

    def put_mark(self, row: MarkRow) -> None:
        self.marks.append(row)

    def required_at(self, key: Key, lsn: int) -> int:
        return max((m.generation for m in self.marks if m.key == key and m.lsn <= lsn), default=0)

    def marked_keys(self) -> list[Key]:
        return sorted(self.required, key=lambda k: (k.entity, k.attr))

    def put_job(self, row: JobRow) -> None:
        if row.generation in self.job_rows:
            raise ValueError("duplicate completion job")
        self.job_rows[row.generation] = row

    def replace_job(self, row: JobRow) -> None:
        self.job_rows[row.generation] = row

    def jobs(self, state: str | None = None) -> list[JobRow]:
        return [r for _, r in sorted(self.job_rows.items()) if state is None or r.state == state]

    def put_dirty(self, row: DirtyRow) -> None:
        self.dirty.append(row)

    def dirty_rows(self) -> list[DirtyRow]:
        return list(self.dirty)

    def clear_dirty(self, generation: int, cleared_lsn: int) -> None:
        self.dirty = [replace(d, cleared_lsn=cleared_lsn) if d.generation == generation and d.cleared_lsn is None else d for d in self.dirty]

    # -- subscriptions and outbox (T-C5)
    def put_subscription(self, plan_id: str, key: Key) -> None:
        self.subs.add((plan_id, key))

    def delete_subscriptions(self, plan_id: str) -> None:
        self.subs = {s for s in self.subs if s[0] != plan_id}

    def subscriptions_for_plan(self, plan_id: str) -> list[Key]:
        return sorted((k for p, k in self.subs if p == plan_id), key=lambda k: (k.entity, k.attr))

    def plans_for_key(self, key: Key) -> list[str]:
        return sorted(p for p, k in self.subs if k == key)

    def put_outbox(self, row: OutboxRow) -> None:
        self.outbox.setdefault((row.event_id, row.plan_id), row)

    def outbox_pending(self, limit: int) -> list[OutboxRow]:
        return [r for r in self.outbox.values() if r.delivered_us is None][:limit]

    def outbox_all(self) -> list[OutboxRow]:
        return list(self.outbox.values())

    def ack_outbox(self, event_id: str, plan_id: str, delivered_us: int) -> None:
        k = (event_id, plan_id)
        if k in self.outbox:
            self.outbox[k] = replace(self.outbox[k], delivered_us=delivered_us)

    def redact_outbox(self, key: Key, versions: tuple[int, ...]) -> None:
        for k, r in list(self.outbox.items()):
            if r.key == key and (r.new_version in versions or (r.old_version is not None and r.old_version in versions)):
                self.outbox[k] = replace(r, payload=None)

    # -- entity pseudonymisation
    def entity_has_log_rows(self, entity: str) -> bool:
        return any(r.key is not None and r.key.entity == entity for r in self.log.values())

    def rename_entity(self, rw: EntityRewriter) -> None:
        key = rw.key
        self.beliefs = {
            (key(r.key).entity, r.key.attr, r.version): replace(
                r, key=key(r.key), deps=tuple((key(k), v) for k, v in r.deps), belief=rw.belief(r.belief)
            )
            for r in self.beliefs.values()
        }
        self.current = {key(k): v for k, v in self.current.items()}
        self.required = {key(k): v for k, v in self.required.items()}
        self.marks = [replace(m, key=key(m.key)) for m in self.marks]
        self.job_rows = {g: replace(j, payload=rw.job(j.payload)) for g, j in self.job_rows.items()}
        self.subs = {(p, key(k)) for p, k in self.subs}
        self.outbox = {
            ids: replace(o, key=key(o.key), payload=rw.outbox(o.payload)) for ids, o in self.outbox.items()
        }

    # -- inputs
    def put_input(self, row: InputRow) -> None:
        self.inputs[(row.kind, row.version)] = row

    def input_row(self, kind: str, version: int) -> InputRow | None:
        return self.inputs.get((kind, version))

    def input_row_at(self, kind: str, lsn: int) -> InputRow | None:
        rows = [r for r in self.inputs.values() if r.kind == kind and r.effective_lsn <= lsn]
        return max(rows, key=lambda r: r.version) if rows else None

    def inputs_all(self) -> list[InputRow]:
        return [self.inputs[k] for k in sorted(self.inputs)]

    def latest_input_version(self, kind: str) -> int | None:
        vs = [r.version for r in self.inputs.values() if r.kind == kind]
        return max(vs) if vs else None

    def put_attr_dependents(self, schema_version: int, mapping: dict[str, tuple[str, ...]]) -> None:
        for attr, deps in mapping.items():
            self.attr_deps[(schema_version, attr)] = deps

    def attr_dependents(self, schema_version: int, attr: str) -> tuple[str, ...]:
        return self.attr_deps.get((schema_version, attr), ())


class InMemoryBackend(Engine):
    """Reference backend. ``chain=False`` builds a chainless backend (no ``verify_log``/``export_head``),
    which must remain contract-conformant (S-13)."""

    def __init__(
        self,
        *,
        chain: bool = True,
        store_secret: bytes | None = None,
        clock: Callable[[], datetime] | None = None,
        fault: FaultHook | None = None,
        ids: UlidFactory | None = None,
        traversal_budget: int = DEFAULT_TRAVERSAL_BUDGET,
    ) -> None:
        self.storage = MemoryStorage()
        super().__init__(
            self.storage, chain_enabled=chain, store_secret=store_secret, clock=clock, fault=fault, ids=ids,
            traversal_budget=traversal_budget,
        )

