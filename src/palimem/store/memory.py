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

from palimem.store._storage import AdmRow, BeliefRow, InputRow, LogRow
from palimem.store.backend import FaultHook
from palimem.store.engine import Engine
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
        self._in_txn = False
        self._write_lock = threading.RLock()

    # -- transactions
    def _snapshot(self) -> tuple[Any, ...]:
        return (dict(self.meta), dict(self.log), dict(self.adm), dict(self.beliefs), dict(self.current), dict(self.inputs), dict(self.attr_deps))

    def _restore(self, snap: tuple[Any, ...]) -> None:
        self.meta, self.log, self.adm, self.beliefs, self.current, self.inputs, self.attr_deps = snap

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
        return self.current.get(key)

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
        return sorted(self.current, key=lambda k: (k.entity, k.attr))

    def key_dependents(self, key: Key) -> list[Key]:
        out = []
        for k, v in self.current.items():
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

    def max_belief_lsn(self) -> int:
        return max((r.lsn for r in self.beliefs.values()), default=0)

    # -- inputs
    def put_input(self, row: InputRow) -> None:
        self.inputs[(row.kind, row.version)] = row

    def input_row(self, kind: str, version: int) -> InputRow | None:
        return self.inputs.get((kind, version))

    def input_row_at(self, kind: str, lsn: int) -> InputRow | None:
        rows = [r for r in self.inputs.values() if r.kind == kind and r.effective_lsn <= lsn]
        return max(rows, key=lambda r: r.version) if rows else None

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
    ) -> None:
        self.storage = MemoryStorage()
        super().__init__(self.storage, chain_enabled=chain, store_secret=store_secret, clock=clock, fault=fault, ids=ids)

