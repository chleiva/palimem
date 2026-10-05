"""The backend semantics, implemented once on top of :class:`~palimem.store._storage.Storage`.

``Engine`` is what ``InMemoryBackend`` and ``SQLiteBackend`` both are. It implements, in one
transaction per append: log insert (+ salted chain), admission records (+ chain), the dependency-closure
marking of the generation barrier, the reviser's belief versions, the current-version index, completion
jobs and notification events, becoming visible together or not at all. It never decides admission and
never computes beliefs.

Wave 2 (T-C4 .. T-C9) adds: the generation barrier and its durable completion jobs (``read_belief``,
``complete_pending``), the outbox (``subscribe``, ``deliver``), versioned inputs (``historical_inputs``),
erasure with dependency repair (``erase(..., reviser=)``) and JSONL export / import.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from palimem.store import barrier, chain, portable
from palimem.store._storage import (
    AdmRow,
    BeliefRow,
    DirtyRow,
    InputRow,
    JobRow,
    LogRow,
    MarkRow,
    OutboxRow,
    Storage,
)
from palimem.store.backend import (
    CAP_ERASE,
    CAP_EXPORT_HEAD,
    CAP_VERIFY_LOG,
    AdmissionContext,
    Admitter,
    AppendResult,
    BeliefRead,
    CapabilityError,
    CompletionReport,
    ErasureReason,
    FaultHook,
    Head,
    IdempotencyConflict,
    ImportReport,
    InputKind,
    InputsAt,
    InvalidRevision,
    LimitedRead,
    NotReconstructable,
    OutboxEvent,
    RecoveryReport,
    Reviser,
    RevisionContext,
    RowStatus,
    StoreError,
    Tombstone,
    VerifyProblem,
    VerifyResult,
)
from palimem.store.barrier import (
    DEFAULT_TRAVERSAL_BUDGET,
    JOB_REPAIR,
    JOB_REVISE,
    STORE_WIDE,
    JobPayload,
)
from palimem.store.ids import UlidFactory
from palimem.store.views import belief_view, parse_belief_ref
from palimem.types import (
    AdmissionRecord,
    Belief,
    BeliefAsOf,
    BeliefView,
    Inference,
    InvalidatedBy,
    KernelStatus,
    Key,
    LogEntry,
    Report,
    ResourceLimitedReason,
    Schema,
    Segment,
    Versions,
    canonical_json,
    parse_json,
)

__all__ = ["DEFAULT_TRAVERSAL_BUDGET", "Engine"]

_GENERATION = "generation"
_VERIFY_CHECKPOINT = "verify_beliefs_checkpoint"
_REDACTED = canonical_json({"redacted": True})
_FAR = 2**62


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Engine:
    def __init__(
        self,
        storage: Storage,
        *,
        chain_enabled: bool = True,
        store_secret: bytes | None = None,
        clock: Callable[[], datetime] | None = None,
        fault: FaultHook | None = None,
        ids: UlidFactory | None = None,
        traversal_budget: int = DEFAULT_TRAVERSAL_BUDGET,
    ) -> None:
        if traversal_budget < 1:
            raise ValueError("traversal_budget must be at least 1")
        self._s = storage
        self._chain = chain_enabled
        self._secret = store_secret
        self._clock = clock or _utcnow
        self._fault_hook = fault
        self._ids = ids or UlidFactory()
        self._lock = threading.RLock()
        self._traversal_budget = traversal_budget

    # ------------------------------------------------------------------ capabilities / plumbing

    @property
    def capabilities(self) -> frozenset[str]:
        caps: set[str] = set()
        if self._chain:
            caps |= {CAP_VERIFY_LOG, CAP_EXPORT_HEAD}
        if self._secret is not None:
            caps.add(CAP_ERASE)
        return frozenset(caps)

    def set_fault_hook(self, hook: FaultHook | None) -> None:
        self._fault_hook = hook

    def _fault(self, step: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(step)

    def close(self) -> None:
        with self._lock:
            self._s.close()

    def _generation(self) -> int:
        v = self._s.get_meta(_GENERATION)
        return int(v) if v is not None else 0

    def _head_lsn(self) -> int:
        lh = self._s.log_head()
        return lh.lsn if lh else 0

    def _now_us(self) -> int:
        return chain.to_us(self._clock())

    def _lsn(self, as_of: BeliefAsOf) -> int:
        if isinstance(as_of, bool):
            raise TypeError("belief_as_of must be an LSN (int) or a timestamp")
        if isinstance(as_of, int):
            return as_of
        return self.lsn_at(as_of)

    # ------------------------------------------------------------------ row <-> type

    @staticmethod
    def _entry(row: LogRow) -> LogEntry:
        assert row.content is not None
        report = replace(Report.from_dict(parse_json(row.content)), id=row.report_id)
        return LogEntry(
            lsn=row.lsn,
            recorded_at=chain.from_us(row.recorded_us),
            report=report,
            prev_hash=row.prev_hash,
            entry_hash=row.entry_hash,
        )

    @staticmethod
    def _tombstone(row: LogRow) -> Tombstone:
        assert row.tomb is not None
        d = parse_json(row.tomb)
        return Tombstone.from_dict(d)

    def _item(self, row: LogRow) -> LogEntry | Tombstone:
        return self._tombstone(row) if row.tomb is not None else self._entry(row)

    @staticmethod
    def _adm(row: AdmRow) -> AdmissionRecord:
        return AdmissionRecord.from_json(row.record)

    @staticmethod
    def _belief(row: BeliefRow) -> Belief:
        return Belief.from_json(row.belief)

    @staticmethod
    def _decode(row: BeliefRow) -> Belief | None:
        """The stored belief, or ``None`` if an erasure redacted this version (S-13)."""
        return Belief.from_json(row.belief) if row.reconstructable else None

    # ------------------------------------------------------------------ read view

    def head(self) -> Head:
        with self._lock:
            lh = self._s.log_head()
            ah = self._s.adm_head()
            return Head(
                lsn=lh.lsn if lh else 0,
                entry_hash=(lh.entry_hash if lh else chain.GENESIS) if self._chain else None,
                admission_seq=ah.seq if ah else 0,
                admission_hash=(ah.entry_hash if ah else chain.GENESIS) if self._chain else None,
                generation=self._generation(),
            )

    def get_entry(self, report_id: str) -> LogEntry | Tombstone | None:
        with self._lock:
            row = self._s.log_by_report(report_id)
            return None if row is None else self._item(row)

    def scan(self, from_lsn: int = 1, to_lsn: int | None = None) -> Iterator[LogEntry | Tombstone]:
        with self._lock:
            items = [self._item(r) for r in self._s.log_range(from_lsn, to_lsn)]
        return iter(items)

    def entries_for_key(self, key: Key, to_lsn: int | None = None) -> tuple[LogEntry, ...]:
        with self._lock:
            return tuple(self._entry(r) for r in self._s.log_for_key(key, to_lsn) if r.tomb is None and r.content is not None)

    def admissions_for_report(self, report_id: str) -> tuple[AdmissionRecord, ...]:
        with self._lock:
            return tuple(self._adm(r) for r in self._s.adm_for_report(report_id))

    def admissions_for_key(self, key: Key, to_lsn: int | None = None) -> tuple[AdmissionRecord, ...]:
        with self._lock:
            rows: list[AdmRow] = []
            for lr in self._s.log_for_key(key, to_lsn):
                if lr.tomb is None:
                    rows.extend(self._s.adm_for_report(lr.report_id))
            rows.sort(key=lambda r: r.seq)
            return tuple(self._adm(r) for r in rows)

    def current_belief(self, key: Key) -> Belief | None:
        """The newest stored version, raw (no barrier): what a Reviser needs. Use ``read_belief`` to serve a read."""
        with self._lock:
            v = self._s.current_version(key)
            if v is None:
                return None
            row = self._s.belief_row(key, v)
            return None if row is None else self._decode(row)

    def belief_version(self, key: Key, version: int) -> Belief | None:
        with self._lock:
            row = self._s.belief_row(key, version)
            return None if row is None else self._decode(row)

    def belief_at(self, key: Key, as_of: BeliefAsOf) -> Belief | None:
        with self._lock:
            row = self._s.belief_row_as_of(key, self._lsn(as_of))
            return None if row is None else self._decode(row)

    def get_belief_by_ref(self, ref: str) -> Belief | None:
        key, version = parse_belief_ref(ref)
        return self.belief_version(key, version)

    def lsn_at(self, when: datetime) -> int:
        with self._lock:
            return self._s.lsn_at_us(chain.to_us(when))

    def key_dependents(self, key: Key) -> tuple[Key, ...]:
        with self._lock:
            return tuple(sorted(self._s.key_dependents(key), key=lambda k: (k.entity, k.attr)))

    def _input_lsn(self, as_of: BeliefAsOf | None) -> int:
        if as_of is None:
            return self._head_lsn() + 1
        return self._lsn(as_of)

    def input_at(self, kind: InputKind, as_of: BeliefAsOf | None = None) -> tuple[int, Mapping[str, Any]] | None:
        with self._lock:
            row = self._s.input_row_at(kind.value, self._input_lsn(as_of))
            if row is None:
                return None
            payload = parse_json(row.payload)
            assert isinstance(payload, dict)
            return row.version, payload

    def schema(self, as_of: BeliefAsOf | None = None) -> Schema | None:
        found = self.input_at(InputKind.SCHEMA, as_of)
        return None if found is None else Schema.from_dict(found[1])

    def attr_dependents(self, attr: str, as_of: BeliefAsOf | None = None) -> tuple[str, ...]:
        with self._lock:
            row = self._s.input_row_at(InputKind.SCHEMA.value, self._input_lsn(as_of))
            return () if row is None else self._s.attr_dependents(row.version, attr)

    # ------------------------------------------------------------------ versioned inputs (T-C6)

    def put_schema(self, schema: Schema) -> None:
        mapping: dict[str, list[str]] = {}
        for a in schema.attrs:
            if a.rule is not None:
                for r in a.rule.reads:
                    mapping.setdefault(r, []).append(a.name)
        with self._lock, self._s.transaction():
            self._put_input_row(InputKind.SCHEMA, schema.version, schema.to_dict())
            self._s.put_attr_dependents(schema.version, {k: tuple(sorted(v)) for k, v in mapping.items()})

    def put_input(self, kind: InputKind, version: int, payload: Mapping[str, Any]) -> None:
        if kind is InputKind.SCHEMA:
            raise ValueError("use put_schema for schema versions (it maintains the attr -> dependents index)")
        with self._lock, self._s.transaction():
            self._put_input_row(kind, version, payload)

    def _put_input_row(self, kind: InputKind, version: int, payload: Mapping[str, Any]) -> None:
        latest = self._s.latest_input_version(kind.value)
        if latest is not None and version <= latest:
            raise StoreError(f"{kind.value} version {version} is not greater than the current version {latest}")
        lh = self._s.log_head()
        eff = (lh.lsn if lh else 0) + 1  # applies from the next append
        rec_us = max(chain.to_us(self._clock()), lh.recorded_us if lh else 0)
        self._s.put_input(InputRow(kind=kind.value, version=version, effective_lsn=eff, recorded_us=rec_us, payload=canonical_json(dict(payload))))

    def historical_inputs(self, as_of: BeliefAsOf | None = None) -> InputsAt:
        """The schema, semantic, admission and policy inputs in force at ``as_of`` (default: the next append).

        A ``belief_as_of`` query is evaluated under these (design v0.3: "historical queries are evaluated under
        the schema, semantic and admission versions current at belief_as_of")."""
        with self._lock:
            lsn = self._input_lsn(as_of)
            return InputsAt(
                schema=self.schema(as_of),
                semantic=self.input_at(InputKind.SEMANTIC, as_of),
                admission=self.input_at(InputKind.ADMISSION, as_of),
                policy=self.input_at(InputKind.POLICY, as_of),
                lsn=lsn,
            )

    def _inputs_in_force(self, lsn: int) -> dict[str, int]:
        out: dict[str, int] = {}
        for kind in InputKind:
            row = self._s.input_row_at(kind.value, lsn)
            if row is not None:
                out[kind.value] = row.version
        return out

    def _versions_in_force(self, lsn: int) -> Versions:
        v = self._inputs_in_force(lsn)
        return Versions(schema=v.get("schema") or 1, semantic=v.get("semantic") or 1, admission=v.get("admission") or 1)

    # ------------------------------------------------------------------ append

    def _find_idem(self, idem_key: str) -> LogRow | None:
        row = self._s.log_by_idem(idem_key)
        if row is None and self._secret is not None:
            row = self._s.log_by_idem(chain.idem_ref(self._secret, idem_key))  # an erased row keeps only an HMAC
        return row

    def append(self, report: Report, *, idempotency_key: str, admitter: Admitter, reviser: Reviser) -> AppendResult:
        if report.id is not None:
            raise ValueError("report.id is assigned by the log; append an unassigned report")
        if not idempotency_key:
            raise ValueError("every append carries a client idempotency key")
        with self._lock:
            existing = self._find_idem(idempotency_key)
            if existing is not None:
                return self._replay(existing, report)
            result: AppendResult
            with self._s.transaction():
                self._fault("begin")
                existing = self._find_idem(idempotency_key)  # re-check under the write lock
                if existing is not None:
                    return self._replay(existing, report)
                result = self._append_in_txn(report, idempotency_key, admitter, reviser)
                self._fault("before_commit")
            self._fault("after_commit")
            return result

    def _replay(self, row: LogRow, report: Report) -> AppendResult:
        if row.tomb is None:
            assert row.content is not None
            content = chain.content_bytes(report)
            if row.salt is not None and row.commitment is not None:
                same = hmac.compare_digest(chain.commitment(row.salt, content), row.commitment)
            else:
                same = row.content == content.decode("utf-8")
            if not same:
                raise IdempotencyConflict(f"idempotency key '{row.idem_key}' was used for a different report")
        adms = tuple(self._adm(r) for r in self._s.adm_for_lsn(row.lsn))
        beliefs = tuple(self._belief(r) for r in self._s.belief_rows_at_lsn(row.lsn) if r.origin == "append" and r.reconstructable)
        if row.tomb is not None:
            return AppendResult(entry=None, admissions=adms, beliefs=beliefs, generation=row.generation, replayed=True, tombstone=self._tombstone(row))
        return AppendResult(entry=self._entry(row), admissions=adms, beliefs=beliefs, generation=row.generation, replayed=True)

    def _append_in_txn(self, report: Report, idem_key: str, admitter: Admitter, reviser: Reviser) -> AppendResult:
        s = self._s
        head = s.log_head()
        lsn = (head.lsn if head else 0) + 1
        recorded_us = max(chain.to_us(self._clock()), head.recorded_us if head else 0)  # non-decreasing along LSNs
        report_id = self._ids.new()
        stored = replace(report, id=report_id)
        content = chain.content_bytes(stored)
        generation = self._generation() + 1

        salt: bytes | None = None
        commit: str | None = None
        prev: str | None = None
        entry_hash: str | None = None
        if self._chain:
            salt = chain.new_salt()
            commit = chain.commitment(salt, content)
            prev = head.entry_hash if head and head.entry_hash else chain.GENESIS
            entry_hash = chain.log_entry_hash(prev, lsn, report_id, recorded_us, commit)
        s.put_log(
            LogRow(
                lsn=lsn, report_id=report_id, recorded_us=recorded_us, generation=generation, key=stored.key,
                content=content.decode("utf-8"), salt=salt, commitment=commit, prev_hash=prev, entry_hash=entry_hash,
                idem_key=idem_key,
            )
        )
        self._fault("after_log_insert")
        entry = LogEntry(lsn=lsn, recorded_at=chain.from_us(recorded_us), report=stored, prev_hash=prev, entry_hash=entry_hash)

        # -- admission (decided elsewhere; stored here)
        records = tuple(admitter.admit(AdmissionContext(entry=entry, view=self, new_id=self._ids.new)))
        if not any(r.report_id == report_id for r in records):
            raise InvalidRevision("the admitter must return a record for the appended report")
        if len({r.id for r in records}) != len(records):
            raise InvalidRevision("duplicate admission record ids")
        for rec in records:
            target = s.log_by_report(rec.report_id)
            if target is None:
                raise InvalidRevision(f"admission record names unknown report {rec.report_id}")
            ah = s.adm_head()
            seq = (ah.seq if ah else 0) + 1
            rjson = rec.to_json()
            a_prev: str | None = None
            a_hash: str | None = None
            if self._chain:
                a_prev = ah.entry_hash if ah and ah.entry_hash else chain.GENESIS
                a_hash = chain.admission_entry_hash(a_prev, seq, rjson, target.entry_hash or "")
            s.put_adm(AdmRow(seq=seq, lsn=lsn, report_id=rec.report_id, record=rjson, report_entry_hash=target.entry_hash, prev_hash=a_prev, entry_hash=a_hash))
        self._fault("after_admission")

        s.set_meta(_GENERATION, str(generation))
        beliefs = self._revise_stage(entry, records, generation, reviser)
        return AppendResult(entry=entry, admissions=records, beliefs=beliefs, generation=generation)

    # ------------------------------------------------------------------ the revision stage and the barrier (T-C4)

    def _schema_version_row(self) -> int | None:
        row = self._s.input_row_at(InputKind.SCHEMA.value, self._head_lsn() + 1)
        return None if row is None else row.version

    def _neighbours(self, key: Key, schema_version: int | None) -> list[Key]:
        out = list(self._s.key_dependents(key))
        if schema_version is not None:
            out.extend(Key(entity=key.entity, attr=a) for a in self._s.attr_dependents(schema_version, key.attr))
        return out

    def _closure(self, seeds: Sequence[Key], budget: int | None) -> tuple[list[Key], bool]:
        """Dependency closure of ``seeds`` (breadth first): keys whose current belief depends on a reached key,
        plus the same entity's derived attributes (schema). ``exhausted`` is True when the traversal would visit
        more than ``budget`` keys; the caller must then NOT mark partially."""
        sv = self._schema_version_row()
        seen: dict[Key, None] = {}
        queue: deque[Key] = deque(seeds)
        while queue:
            k = queue.popleft()
            if k in seen:
                continue
            if budget is not None and len(seen) >= budget:
                return list(seen), True
            seen[k] = None
            queue.extend(self._neighbours(k, sv))
        return list(seen), False

    def _completed_of(self, key: Key) -> int:
        v = self._s.current_version(key)
        if v is None:
            return 0
        row = self._s.belief_row(key, v)
        return 0 if row is None else row.completed_generation

    def _revise_stage(self, entry: LogEntry, records: tuple[AdmissionRecord, ...], generation: int, reviser: Reviser) -> tuple[Belief, ...]:
        """Mark the dependency closure, revise, store the versions, move the index, write jobs and events.

        The marking is bounded by the traversal budget: past it nothing is marked partially; a dirty marker scoped
        to the attribute component and a durable completion job are written instead (the revision is deferred)."""
        s = self._s
        lsn = entry.lsn
        touched = entry.report.key
        closure, exhausted = self._closure([touched], self._traversal_budget)
        beliefs: tuple[Belief, ...] = ()
        if not exhausted:
            beliefs = tuple(
                reviser.revise(RevisionContext(entry=entry, admissions=records, generation=generation, view=self, inputs=self._inputs_in_force(lsn)))
            )
        self._fault("after_revision")
        seen: set[Key] = set()
        previous: dict[Key, int] = {}
        for b in beliefs:
            if b.key in seen:
                raise InvalidRevision(f"revision returned two versions for key {b.key}")
            seen.add(b.key)
            if b.lsn != lsn:
                raise InvalidRevision(f"belief for {b.key} carries lsn {b.lsn}, expected {lsn}")
            cur = s.current_version(b.key) or 0
            if b.version != cur + 1:
                raise InvalidRevision(f"belief for {b.key} has version {b.version}, expected {cur + 1}")
            previous[b.key] = cur
        for b in beliefs:
            self._put_belief(b, "append")
        self._fault("after_beliefs")
        for b in beliefs:
            s.set_current(b.key, b.version)
        self._fault("after_index")

        marked: list[Key] = []
        if not exhausted:
            marked = list(dict.fromkeys([*closure, *(b.key for b in beliefs)]))
            for k in marked:
                s.put_mark(MarkRow(key=k, generation=generation, lsn=lsn))
                s.set_required(k, generation)
        stale = [k for k in marked if self._completed_of(k) < generation]
        now_us = self._now_us()
        if exhausted:
            self._set_dirty(touched, generation, lsn)
            s.put_job(JobRow(generation=generation, state="pending", payload=JobPayload(kind=JOB_REVISE, lsn=lsn, seeds=(touched,), keys=None).to_json(), created_us=now_us, updated_us=now_us))
        elif stale:
            s.put_job(JobRow(generation=generation, state="pending", payload=JobPayload(kind=JOB_REVISE, lsn=lsn, seeds=(touched,), keys=tuple(barrier.sorted_keys(stale))).to_json(), created_us=now_us, updated_us=now_us))
        for b in beliefs:
            self._emit_events(b, previous[b.key] or None)
        self._fault("after_barrier")
        return beliefs

    def _put_belief(self, b: Belief, origin: str) -> None:
        self._s.put_belief(
            BeliefRow(
                key=b.key, version=b.version, lsn=b.lsn, recorded_us=chain.to_us(b.recorded_at),
                required_generation=b.required_generation, completed_generation=b.completed_generation,
                belief=b.to_json(), pins=tuple(sorted({p.report_id for p in b.pinned})),
                deps=tuple((d.key, d.version) for d in b.depends_on), origin=origin,
            )
        )

    def _set_dirty(self, touched: Key, generation: int, lsn: int) -> None:
        """Dirty marker scoped to the connected component of the attribute dependency graph (concern H4);
        store-wide only when no schema bounds the component (last resort)."""
        comp = barrier.component_of(self.schema(), touched.attr)
        attrs = comp if comp is not None else frozenset({STORE_WIDE})
        self._s.put_dirty(DirtyRow(generation=generation, attrs=barrier.encode_attrs(attrs), set_lsn=lsn))

    def _dirty_for(self, key: Key, at_lsn: int | None) -> DirtyRow | None:
        found: DirtyRow | None = None
        for d in self._s.dirty_rows():
            attrs = barrier.decode_attrs(d.attrs)
            if STORE_WIDE not in attrs and key.attr not in attrs:
                continue
            if at_lsn is None:
                active = d.cleared_lsn is None
            else:
                active = d.set_lsn <= at_lsn and (d.cleared_lsn is None or at_lsn < d.cleared_lsn)
            if active and (found is None or d.generation > found.generation):
                found = d
        return found

    def _unknown(self, key: Key, *, version: int, lsn: int, generation: int, now: datetime, invalidated_by: InvalidatedBy | None = None) -> Belief:
        """The belief of a key with no admissible evidence: one ``unknown`` segment (design: status ladder)."""
        return Belief(
            key=key, version=version, lsn=lsn, required_generation=generation, completed_generation=generation,
            segments=(Segment(valid_from=None, valid_to=None, kernel_status=KernelStatus.UNKNOWN),), pinned=(), depends_on=(),
            invalidated_by=invalidated_by, versions=self._versions_in_force(lsn), inference=Inference(complete=True), recorded_at=now,
        )

    def _stamped(self, key: Key, fresh: Belief | None, *, lsn: int, required: int, now: datetime) -> Belief:
        """A reviser's recomputed content as the key's next version. The store owns version, lsn and generations."""
        cur = self._s.current_version(key) or 0
        if fresh is None:
            return self._unknown(key, version=cur + 1, lsn=lsn, generation=required, now=now)
        if fresh.inference.complete:
            return replace(
                fresh, key=key, version=cur + 1, lsn=lsn, required_generation=required, completed_generation=required,
                inference=Inference(complete=True), recorded_at=now,
            )
        completed = max(0, min(fresh.completed_generation, required - 1))
        return replace(
            fresh, key=key, version=cur + 1, lsn=lsn, required_generation=required, completed_generation=completed,
            inference=Inference(complete=False, reason=fresh.inference.reason), recorded_at=now,
        )

    # -- reads under the barrier

    def read_belief(self, key: Key, as_of: BeliefAsOf | None = None) -> BeliefRead:
        """Serve a read: the belief, or why no valid result exists for this snapshot (T-C4).

        Never returns an older version as current: a key (or a dependency, or its whole attribute component) that is
        stale answers ``LimitedRead`` with the older complete version, labelled by its own ``lsn``. A historical read
        applies the barrier of ITS snapshot: the version in force at ``as_of`` must cover the generations that named
        the key by then, whatever completed later."""
        with self._lock:
            current = as_of is None
            lsn = self._head_lsn() if current else self._lsn(as_of)  # type: ignore[arg-type]
            rows = self._s.belief_rows_for_key(key, None if current else lsn)
            row = rows[0] if rows else None
            required = self._s.required_generation(key) if current else self._s.required_at(key, lsn)
            dirty = self._dirty_for(key, None if current else lsn)

            def last_complete() -> Belief | None:
                for r in rows:
                    b = self._decode(r)
                    if b is not None and b.inference.complete:
                        return b
                return None

            completed = 0 if row is None else row.completed_generation
            if dirty is not None:
                return LimitedRead(
                    reason=ResourceLimitedReason.STORE_DIRTY, required_generation=dirty.generation,
                    completed_generation=min(completed, dirty.generation), last_complete=last_complete(),
                )
            if row is None:
                if required > 0:
                    return LimitedRead(reason=ResourceLimitedReason.STALE_DEPENDENCY, required_generation=required, completed_generation=0, reason_key=key)
                return None
            if not row.reconstructable:
                return NotReconstructable(key=key, version=row.version, lsn=row.lsn)
            belief = Belief.from_json(row.belief)
            if not belief.inference.complete:
                return LimitedRead(
                    reason=ResourceLimitedReason.INFERENCE_INCOMPLETE,
                    required_generation=max(belief.required_generation, required),
                    completed_generation=belief.completed_generation, last_complete=last_complete(),
                )
            if required > belief.completed_generation:
                return LimitedRead(
                    reason=ResourceLimitedReason.STALE_DEPENDENCY, required_generation=required,
                    completed_generation=belief.completed_generation, reason_key=key, last_complete=belief,
                )
            if current:
                stale = self._stale_dependency(row)
                if stale is not None:
                    dep, req, comp = stale
                    return LimitedRead(
                        reason=ResourceLimitedReason.STALE_DEPENDENCY, required_generation=req, completed_generation=comp,
                        reason_key=dep, last_complete=belief,
                    )
            return belief

    def _stale_dependency(self, row: BeliefRow) -> tuple[Key, int, int] | None:
        """The first stale key in the ``depends_on`` closure of a stored version (reads of anything depending on a
        stale key are stale, even if the marking did not reach it)."""
        seen: set[Key] = set()
        stack = [k for k, _ in row.deps]
        while stack:
            k = stack.pop()
            if k in seen:
                continue
            seen.add(k)
            req = self._s.required_generation(k)
            v = self._s.current_version(k)
            drow = self._s.belief_row(k, v) if v is not None else None
            comp = 0 if drow is None else drow.completed_generation
            if req > comp:
                return k, req, comp
            if drow is not None:
                stack.extend(dk for dk, _ in drow.deps)
        return None

    # -- completion jobs

    def complete_pending(self, reviser: Reviser, *, limit: int | None = None) -> CompletionReport:
        """Run the durable completion jobs in generation order (resumable: a job lives in the database until it is
        done). A job for generation ``g`` writes a version for a key only if the stored ``completed_generation < g``
        and ``required_generation >= g``, so it finishes an incomplete generation and can never overwrite work a
        newer generation already completed. Versions are recomputed with ``Reviser.recompute`` at the log head and
        carry that head as their ``lsn``; keys that stay incomplete leave the job pending."""
        done = 0
        stamped: list[Key] = []
        skipped: list[Key] = []
        with self._lock:
            pending = self._s.jobs("pending")
        if limit is not None:
            pending = pending[:limit]
        for job in pending:
            with self._lock, self._s.transaction():
                stamped_keys, skipped_keys, finished = self._run_job(job, reviser)
            stamped.extend(stamped_keys)
            skipped.extend(skipped_keys)
            done += 1 if finished else 0
        with self._lock:
            return CompletionReport(
                jobs_done=done, keys_stamped=len(stamped), jobs_pending=len(self._s.jobs("pending")),
                skipped_newer=len(skipped), jobs_blocked=len(self._s.jobs(barrier.JOB_BLOCKED)),
                stamped=tuple(stamped), skipped=tuple(skipped),
            )

    def _run_job(self, job: JobRow, reviser: Reviser) -> tuple[list[Key], list[Key], bool]:
        s = self._s
        g = job.generation
        p = JobPayload.from_json(job.payload)
        head = self._head_lsn()
        schema = self.schema()
        depths = barrier.attr_depths(schema)
        now = self._clock()
        if p.keys is None:
            closure, _ = self._closure(p.seeds, None)
            if p.kind == JOB_REPAIR:
                closure = [k for k in closure if s.current_version(k) is not None]
            for k in closure:  # the deferred marking, at the generation and log position of the original write
                s.put_mark(MarkRow(key=k, generation=g, lsn=p.lsn))
                s.set_required(k, g)
        else:
            closure = list(p.keys)
        needed: list[Key] = []
        skipped: list[Key] = []
        for k in closure:
            if s.required_generation(k) >= g:
                if self._completed_of(k) < g:
                    needed.append(k)
                else:
                    skipped.append(k)  # a newer generation already completed it: never overwritten
        stamped: list[Key] = []
        for k in barrier.order_keys(needed, depths):
            required = s.required_generation(k)
            fresh = reviser.recompute(k, self)
            if fresh is not None and not fresh.inference.complete:
                cur = s.current_version(k)
                crow = None if cur is None else s.belief_row(k, cur)
                if crow is not None and crow.completed_generation < crow.required_generation:
                    continue  # already incomplete and still unfinishable: do not pile up versions
            b = self._stamped(k, fresh, lsn=head, required=required, now=now)
            prev = s.current_version(k)
            self._put_belief(b, "completion")
            s.set_current(k, b.version)
            self._emit_events(b, prev)
            stamped.append(k)
            self._fault("completion_stamped")
        self._fault("completion_before_commit")
        remaining = [k for k in closure if s.required_generation(k) >= g and self._completed_of(k) < g]
        finished = not remaining
        if finished:
            s.replace_job(JobRow(generation=g, state="done", payload=p.cleared().to_json(), created_us=job.created_us, updated_us=self._now_us()))
            s.clear_dirty(g, head)
        else:
            # never loop on a job that cannot finish: count the attempt, and give up after a few with the reason recorded
            q = p.after_failed_run(self._unfinished_reason(remaining[0]), limit=barrier.MAX_JOB_ATTEMPTS)
            state = barrier.JOB_BLOCKED if q.blocked is not None else "pending"
            s.replace_job(JobRow(generation=g, state=state, payload=q.to_json(), created_us=job.created_us, updated_us=self._now_us()))
        return stamped, skipped, finished

    def _unfinished_reason(self, key: Key) -> str:
        v = self._s.current_version(key)
        b = None if v is None else self.belief_version(key, v)
        why = b.inference.reason if b is not None and b.inference.reason else "still marked, recomputation did not complete it"
        return f"{key.entity}/{key.attr}: {why}"

    def retry_blocked(self) -> int:
        """Put the blocked completion jobs back to pending with their attempt count reset (after the cause was fixed,
        e.g. the environment budget was raised). Returns how many jobs were reset."""
        n = 0
        with self._lock, self._s.transaction():
            for job in self._s.jobs(barrier.JOB_BLOCKED):
                p = JobPayload.from_json(job.payload)
                fresh = JobPayload(kind=p.kind, lsn=p.lsn, seeds=p.seeds, keys=p.keys)
                self._s.replace_job(JobRow(generation=job.generation, state="pending", payload=fresh.to_json(), created_us=job.created_us, updated_us=self._now_us()))
                n += 1
        return n

    # ------------------------------------------------------------------ subscriptions and the outbox (T-C5)

    def subscribe(self, plan_id: str, keys: Sequence[Key]) -> None:
        if not plan_id:
            raise ValueError("plan_id must be non-empty")
        with self._lock, self._s.transaction():
            for k in keys:
                self._s.put_subscription(plan_id, k)

    def unsubscribe(self, plan_id: str) -> None:
        with self._lock, self._s.transaction():
            self._s.delete_subscriptions(plan_id)

    def subscriptions(self, plan_id: str) -> tuple[Key, ...]:
        with self._lock:
            return tuple(self._s.subscriptions_for_plan(plan_id))

    @staticmethod
    def event_id(key: Key, old_version: int | None, new_version: int) -> str:
        """Stable across redelivery: ``hash(key, old version, new version)``."""
        return hashlib.sha256(canonical_json({"key": key.to_dict(), "old": old_version, "new": new_version}).encode("utf-8")).hexdigest()

    def _emit_events(self, new: Belief, old_version: int | None) -> None:
        """Write an outbox row, inside the revision transaction, for every plan subscribed to the key."""
        plans = self._s.plans_for_key(new.key)
        if not plans:
            return
        old_view: BeliefView | None = None
        if old_version:
            orow = self._s.belief_row(new.key, old_version)
            ob = None if orow is None else self._decode(orow)
            old_view = None if ob is None else belief_view(ob)
        new_view = belief_view(new)
        payload = canonical_json({"old": None if old_view is None else old_view.to_dict(), "new": None if new_view is None else new_view.to_dict()})
        eid = self.event_id(new.key, old_version, new.version)
        for plan in plans:
            self._s.put_outbox(
                OutboxRow(event_id=eid, plan_id=plan, key=new.key, old_version=old_version, new_version=new.version, lsn=new.lsn, payload=payload, created_us=self._now_us())
            )

    @staticmethod
    def _event(row: OutboxRow) -> OutboxEvent:
        old_view: BeliefView | None = None
        new_view: BeliefView | None = None
        if row.payload is not None:
            d: Any = parse_json(row.payload)
            old_view = None if d["old"] is None else BeliefView.from_dict(d["old"])
            new_view = None if d["new"] is None else BeliefView.from_dict(d["new"])
        return OutboxEvent(
            event_id=row.event_id, plan_id=row.plan_id, key=row.key, old_version=row.old_version, new_version=row.new_version,
            lsn=row.lsn, old_view=old_view, new_view=new_view, created_at=chain.from_us(row.created_us), redacted=row.payload is None,
        )

    def pending_events(self, limit: int = 100) -> tuple[OutboxEvent, ...]:
        with self._lock:
            return tuple(self._event(r) for r in self._s.outbox_pending(limit))

    def ack_event(self, event_id: str, plan_id: str) -> None:
        with self._lock, self._s.transaction():
            self._s.ack_outbox(event_id, plan_id, self._now_us())

    def deliver(self, handler: Callable[[OutboxEvent], None], *, limit: int = 100) -> int:
        """Hand every pending event to ``handler`` and record the acknowledgement. At-least-once: a crash after the
        handler and before the acknowledgement redelivers the same ``event_id``; handlers must be idempotent on it."""
        n = 0
        for ev in self.pending_events(limit):
            handler(ev)
            self._fault("after_handler")
            self.ack_event(ev.event_id, ev.plan_id)
            n += 1
        return n

    # ------------------------------------------------------------------ erasure with dependency repair (S-13, T-C8)

    def pseudonym_of(self, principal: str) -> str:
        """The pseudonym (HMAC under the host-supplied store secret) the tombstones use for a principal: lets the host
        ask 'was this erasure requested by X?' without any plain actor text ever being stored (S-13)."""
        if self._secret is None:
            raise CapabilityError("pseudonym_of needs a host-supplied store_secret")
        return chain.actor_ref(self._secret, principal)

    def erase(
        self, report_id: str, reason: ErasureReason, *, reviser: Reviser | None = None, requester: str | None = None
    ) -> Tombstone:
        """Erase a report: its content, raw reference, salt, plain key and idempotency key are removed, leaving a
        minimal tombstone that carries the ORIGINAL entry hash so the chain still verifies. Every belief version that
        pinned the report is redacted (and flagged not reconstructable: those historical answers can no longer be
        reconstructed), then the dependants are repaired exactly as after a withdrawal, by recomputing the affected
        keys without the report (``reviser`` is required when any version pinned it; a failure rolls the erasure back).
        Values that rested on the report disappear from the stored beliefs; versions that did not pin it are kept."""
        if self._secret is None:
            raise CapabilityError("erase needs a host-supplied store_secret (it is never stored in the data tables)")
        secret = self._secret
        with self._lock, self._s.transaction():
            row = self._s.log_by_report(report_id)
            if row is None:
                raise StoreError(f"unknown report {report_id}")
            if row.tomb is not None:
                return self._tombstone(row)
            assert row.content is not None and row.key is not None
            pinned = self._s.versions_pinning(report_id)
            if pinned and reviser is None:
                raise StoreError("beliefs rest on this report: pass the reviser so dependants can be repaired (erase would otherwise leave derived values behind)")
            actor = Report.from_dict(parse_json(row.content)).actor
            affected = sorted((chain.key_ref(secret, k), v) for k, v in pinned)
            tomb = Tombstone(
                report_id=report_id, lsn=row.lsn, entry_hash=row.entry_hash if self._chain else None,
                key_ref=chain.key_ref(secret, row.key), actor_ref=chain.actor_ref(secret, actor),
                reason_class=reason, erased_at=self._clock(), affected_versions=tuple(affected),
                requester_ref=None if requester is None else chain.actor_ref(secret, requester),
            )
            self._s.replace_log(replace(row, key=None, content=None, salt=None, idem_key=chain.idem_ref(secret, row.idem_key), tomb=canonical_json(tomb.to_dict())))
            by_key: dict[Key, list[int]] = {}
            for k, v in pinned:
                self._s.redact_belief(k, v, _REDACTED)
                by_key.setdefault(k, []).append(v)
            for k, vs in by_key.items():
                self._s.redact_outbox(k, tuple(vs))
            self._fault("erase_after_redact")
            generation = self._generation() + 1
            self._s.set_meta(_GENERATION, str(generation))
            if pinned:
                assert reviser is not None
                seeds = list(dict.fromkeys([*by_key, row.key]))
                head = self._head_lsn()
                closure, exhausted = self._closure(seeds, self._traversal_budget)
                if exhausted:
                    self._set_dirty(row.key, generation, head)
                    now_us = self._now_us()
                    self._s.put_job(
                        JobRow(generation=generation, state="pending", payload=JobPayload(kind=JOB_REPAIR, lsn=head, seeds=tuple(seeds), keys=None).to_json(), created_us=now_us, updated_us=now_us)
                    )
                else:
                    self._repair(closure, reviser, generation, head)
            self._fault("erase_after_repair")
            return tomb

    def _repair(self, keys: Sequence[Key], reviser: Reviser, generation: int, head: int) -> None:
        """Recompute ``keys`` (dependencies first) without the erased evidence and store the repaired versions."""
        s = self._s
        depths = barrier.attr_depths(self.schema())
        now = self._clock()
        for k in barrier.order_keys(keys, depths):
            prev = s.current_version(k)
            fresh = reviser.recompute(k, self)
            if fresh is None and prev is None:
                continue  # never had a belief: nothing to repair, and nothing to leave stale
            s.put_mark(MarkRow(key=k, generation=generation, lsn=head))
            s.set_required(k, generation)
            b = self._stamped(k, fresh, lsn=head, required=generation, now=now)
            self._put_belief(b, "repair")
            s.set_current(k, b.version)
            self._emit_events(b, prev)

    # ------------------------------------------------------------------ verification (T-C10)

    def export_head(self) -> Head:
        if not self._chain:
            raise CapabilityError("this backend has no hash chain")
        return self.head()

    def verify_log(self, from_lsn: int = 1, to_lsn: int | None = None, *, anchor: Head | None = None) -> VerifyResult:
        if not self._chain:
            raise CapabilityError("this backend has no hash chain")
        with self._lock:
            problems: list[VerifyProblem] = []
            rows_out: list[RowStatus] = []
            lh = self._s.log_head()
            head_lsn = lh.lsn if lh else 0
            lo = max(1, from_lsn)
            hi = head_lsn if to_lsn is None else to_lsn
            if lo > 1:
                before = self._s.log_by_lsn(lo - 1)
                prev_hash: str | None = before.entry_hash if before else None
            else:
                prev_hash = chain.GENESIS
            expected = lo
            checked = 0
            for row in self._s.log_range(lo, hi):
                checked += 1
                if row.lsn != expected:
                    problems.append(VerifyProblem(kind="missing_lsn", lsn=expected, detail=f"expected lsn {expected}, found {row.lsn}"))
                linked = prev_hash is None or row.prev_hash == prev_hash
                if not linked:
                    problems.append(VerifyProblem(kind="chain_break", lsn=row.lsn, detail="prev_hash does not match the previous row's entry_hash (edit, deletion or reordering)"))
                if row.commitment is None or row.entry_hash is None or row.prev_hash is None:
                    problems.append(VerifyProblem(kind="malformed", lsn=row.lsn, detail="chain columns are missing"))
                    linked = False
                else:
                    want = chain.log_entry_hash(row.prev_hash, row.lsn, row.report_id, row.recorded_us, row.commitment)
                    if want != row.entry_hash:
                        problems.append(VerifyProblem(kind="entry_hash_mismatch", lsn=row.lsn, detail="entry_hash does not match the row's metadata and commitment"))
                        linked = False
                verified = False
                if row.tomb is not None:
                    if row.content is not None or row.salt is not None or row.key is not None:
                        problems.append(VerifyProblem(kind="erasure_incomplete", lsn=row.lsn, detail="tombstoned row still holds content, salt or key"))
                    elif self._tombstone(row).entry_hash != row.entry_hash:
                        problems.append(VerifyProblem(kind="tombstone_mismatch", lsn=row.lsn, detail="tombstone does not carry the original entry_hash"))
                elif row.content is None or row.salt is None:
                    problems.append(VerifyProblem(kind="content_missing", lsn=row.lsn, detail="content or salt removed without a tombstone"))
                else:
                    if row.commitment is not None and not hmac.compare_digest(chain.commitment(row.salt, row.content.encode("utf-8")), row.commitment):
                        problems.append(VerifyProblem(kind="commitment_mismatch", lsn=row.lsn, detail="content does not match its salted commitment (edited)"))
                    else:
                        verified = True
                        try:
                            decoded = Report.from_dict(parse_json(row.content))
                        except ValueError as e:
                            problems.append(VerifyProblem(kind="malformed", lsn=row.lsn, detail=f"content does not decode: {e}"))
                            verified = False
                        else:
                            if decoded.key != row.key:
                                problems.append(VerifyProblem(kind="index_mismatch", lsn=row.lsn, detail="indexed key differs from the committed content"))
                rows_out.append(RowStatus(lsn=row.lsn, linked=linked, content_verified=verified, tombstoned=row.tomb is not None))
                prev_hash = row.entry_hash
                expected = row.lsn + 1
            if expected <= hi:
                problems.append(VerifyProblem(kind="missing_lsn", lsn=expected, detail=f"rows {expected}..{hi} are missing"))
            problems.extend(self._verify_admissions(lo, hi))
            if anchor is not None:
                problems.extend(self._verify_anchor(anchor, head_lsn))
            return VerifyResult(ok=not problems, checked=checked, problems=tuple(problems), rows=tuple(rows_out))

    def _verify_admissions(self, lo: int, hi: int) -> list[VerifyProblem]:
        out: list[VerifyProblem] = []
        prev = chain.GENESIS
        expected = 1
        for row in self._s.adm_range():
            in_range = lo <= row.lsn <= hi
            if row.seq != expected and in_range:
                out.append(VerifyProblem(kind="admission_missing_seq", lsn=row.lsn, detail=f"expected admission seq {expected}, found {row.seq}"))
            if row.prev_hash != prev and in_range:
                out.append(VerifyProblem(kind="admission_chain_break", lsn=row.lsn, detail=f"admission {row.seq}: prev_hash does not link"))
            if row.prev_hash is not None and row.entry_hash is not None:
                want = chain.admission_entry_hash(row.prev_hash, row.seq, row.record, row.report_entry_hash or "")
                if want != row.entry_hash and in_range:
                    out.append(VerifyProblem(kind="admission_hash_mismatch", lsn=row.lsn, detail=f"admission {row.seq}: entry_hash does not match its record"))
            target = self._s.log_by_report(row.report_id)
            if in_range and (target is None or target.entry_hash != row.report_entry_hash):
                out.append(VerifyProblem(kind="admission_report_mismatch", lsn=row.lsn, detail=f"admission {row.seq}: its report is missing or differs from the one it was decided on"))
            prev = row.entry_hash or prev
            expected = row.seq + 1
        return out

    def _verify_anchor(self, anchor: Head, head_lsn: int) -> list[VerifyProblem]:
        out: list[VerifyProblem] = []
        if anchor.lsn > head_lsn:
            out.append(VerifyProblem(kind="anchor_ahead_of_head", lsn=anchor.lsn, detail=f"anchor is at lsn {anchor.lsn} but the log ends at {head_lsn} (restored older copy or truncation)"))
        elif anchor.lsn > 0:
            row = self._s.log_by_lsn(anchor.lsn)
            if row is None or row.entry_hash != anchor.entry_hash:
                out.append(VerifyProblem(kind="anchor_mismatch", lsn=anchor.lsn, detail="the row at the anchored lsn differs from the exported head (history rewritten)"))
        if anchor.admission_seq > 0:
            arow = self._s.adm_by_seq(anchor.admission_seq)
            if arow is None:
                out.append(VerifyProblem(kind="anchor_ahead_of_head", detail=f"admission seq {anchor.admission_seq} is missing"))
            elif arow.entry_hash != anchor.admission_hash:
                out.append(VerifyProblem(kind="anchor_mismatch", detail=f"admission seq {anchor.admission_seq} differs from the exported head"))
        return out

    def verify_beliefs(
        self, reviser: Reviser, *, keys: Sequence[Key] | None = None, since_lsn: int | None = None
    ) -> VerifyResult:
        """Recompute beliefs with ``reviser.recompute`` and compare with the stored current versions (SEC-25).

        ``keys`` verifies those keys on demand; ``since_lsn`` restricts the run to the keys whose current version was
        written after that log position (the incremental mode: what changed since the last checkpoint). With neither,
        every current key is verified (the full mode, the only one that notices an old row edited in place).

        Stale keys (marked by the barrier and not yet completed) are skipped: their stored version is outdated
        by design and is never served as current. Beliefs fabricated as ``unknown`` for a key left without
        evidence match a ``None`` recomputation."""
        with self._lock:
            targets = list(keys) if keys is not None else self._s.current_keys()
            problems: list[VerifyProblem] = []
            checked = 0
            for key in targets:
                v = self._s.current_version(key)
                if since_lsn is not None and keys is None:
                    row = None if v is None else self._s.belief_row(key, v)
                    if row is not None and row.lsn <= since_lsn:
                        continue
                if v is not None and self._s.belief_row(key, v + 1) is not None:
                    problems.append(VerifyProblem(kind="index_mismatch", key=key, version=v, detail="a newer belief version exists than the current-version index names"))
                if self._s.required_generation(key) > self._completed_of(key):
                    continue
                checked += 1
                stored = self.current_belief(key)
                if stored is not None and (stored.version != v or stored.key != key):
                    problems.append(VerifyProblem(kind="belief_row_mismatch", key=key, version=v, detail="the stored belief names a different key or version than its row"))
                fresh = reviser.recompute(key, self)
                if stored is None and fresh is None:
                    continue
                if fresh is None and stored is not None and _is_empty_unknown(stored):
                    continue
                if stored is None or fresh is None or _core(stored) != _core(fresh):
                    problems.append(VerifyProblem(kind="belief_mismatch", key=key, version=v, detail="stored belief differs from the one recomputed from the log"))
            return VerifyResult(ok=not problems, checked=checked, problems=tuple(problems))

    def verify_beliefs_incremental(self, reviser: Reviser) -> VerifyResult:
        """Verify only what changed since the last successful incremental run, then move the checkpoint.

        The checkpoint (a log position) lives in the store's meta table. A run with problems leaves it where it was,
        so the same keys are checked again next time. The first run (no checkpoint) is a full verification."""
        with self._lock:
            raw = self._s.get_meta(_VERIFY_CHECKPOINT)
            since = int(raw) if raw is not None else None
            head = self._head_lsn()
            res = self.verify_beliefs(reviser, since_lsn=since)
            if res.ok:
                self._s.set_meta(_VERIFY_CHECKPOINT, str(head))
            return VerifyResult(
                ok=res.ok, checked=res.checked, problems=res.problems, rows=res.rows,
                checkpoint_lsn=head if res.ok else since,
            )

    # ------------------------------------------------------------------ recovery

    def recover(self) -> RecoveryReport:
        """Check the invariants an interrupted append must not have broken. Appends are atomic, so a crash leaves
        either the whole append or nothing; this proves it rather than assuming it."""
        with self._lock:
            problems: list[str] = []
            lh = self._s.log_head()
            head_lsn = lh.lsn if lh else 0
            expected = 1
            for row in self._s.log_range(1, None):
                if row.lsn != expected:
                    problems.append(f"log gap at lsn {expected}")
                    expected = row.lsn
                if not self._s.adm_for_report(row.report_id):
                    problems.append(f"report {row.report_id} (lsn {row.lsn}) has no admission record")
                expected = row.lsn + 1
            if self._s.max_belief_lsn() > head_lsn:
                problems.append("a belief version refers to an lsn beyond the log head")
            gen = self._generation()
            if gen < head_lsn:
                problems.append(f"generation {gen} is behind the log head {head_lsn}")
            for key in self._s.current_keys():
                v = self._s.current_version(key)
                if v is None or self._s.belief_row(key, v) is None:
                    problems.append(f"current-version index for {key.entity}/{key.attr} names a missing version")
            for key in self._s.marked_keys():
                if self._s.required_generation(key) != self._s.required_at(key, _FAR):
                    problems.append(f"required generation of {key.entity}/{key.attr} disagrees with its marking history")
            for job in self._s.jobs("pending"):
                if job.generation > gen:
                    problems.append(f"completion job for generation {job.generation} is ahead of the store generation {gen}")
            return RecoveryReport(ok=not problems, head_lsn=head_lsn, generation=gen, problems=tuple(problems))

    # ------------------------------------------------------------------ JSONL export and import (T-C9)

    def export_jsonl(self) -> Iterator[str]:
        """The evidence log, admissions and versioned inputs as JSON lines (see ``palimem.store.portable``).

        Beliefs are not exported: ``import_jsonl`` replays the revision stage with the same Reviser."""
        with self._lock:
            head = self.head()
            inputs = self._s.inputs_all()
            logs = list(self._s.log_range(1, None))
            adms = list(self._s.adm_range())
        lines = [
            portable.dumps(
                {"type": "header", "format": portable.FORMAT_NAME, "version": barrier.EXPORT_FORMAT_VERSION,
                 "store_format": barrier.STORE_FORMAT_VERSION, "chain": self._chain, "head": head.to_dict()}
            )
        ]
        lines.extend(portable.input_line(r) for r in inputs)
        lines.extend(portable.log_line(r) for r in logs)
        lines.extend(portable.adm_line(r) for r in adms)
        lines.append(portable.dumps({"type": "footer", "head": head.to_dict(), "log_rows": len(logs), "admissions": len(adms), "inputs": len(inputs)}))
        return iter(lines)

    def import_jsonl(self, lines: Iterable[str], *, reviser: Reviser) -> ImportReport:
        """Load an export into this EMPTY backend, verifying the hash chains while reading, and replay the revision
        stage with ``reviser`` so beliefs are rebuilt (identical to the exporting store's when it had no erasures
        or deferred completions). Everything happens in one transaction: a failed import leaves the store empty."""
        with self._lock:
            if self._s.log_head() is not None or self._s.inputs_all():
                raise StoreError("import needs an empty store")
            objs: list[dict[str, Any]] = []
            for text in lines:
                if text.strip():
                    try:
                        d = parse_json(text)
                    except ValueError as e:
                        raise StoreError(f"import: a line is not valid JSON: {e}") from e
                    if not isinstance(d, dict) or "type" not in d:
                        raise StoreError("import: every line must be a JSON object with a 'type'")
                    objs.append(d)
            if not objs or objs[0]["type"] != "header" or objs[-1]["type"] != "footer":
                raise StoreError("import: missing header or footer (truncated export)")
            header, footer = objs[0], objs[-1]
            if header.get("format") != portable.FORMAT_NAME or header.get("version") != barrier.EXPORT_FORMAT_VERSION:
                raise StoreError(f"import: unsupported export format {header.get('format')!r} v{header.get('version')!r}")
            if bool(header.get("chain")) != self._chain:
                raise StoreError("import: the export and this backend disagree on the hash chain")
            input_rows = [portable.to_input_row(d) for d in objs if d["type"] == "input"]
            log_rows = sorted((portable.to_log_row(d) for d in objs if d["type"] == "log"), key=lambda r: r.lsn)
            adm_rows = sorted((portable.to_adm_row(d) for d in objs if d["type"] == "admission"), key=lambda r: r.seq)
            if (footer["log_rows"], footer["admissions"], footer["inputs"]) != (len(log_rows), len(adm_rows), len(input_rows)):
                raise StoreError("import: the footer counts do not match the lines (truncated or edited export)")
            adm_by_lsn: dict[int, list[AdmRow]] = {}
            for a in adm_rows:
                adm_by_lsn.setdefault(a.lsn, []).append(a)
            beliefs = 0
            with self._s.transaction():
                for ir in input_rows:
                    self._import_input(ir)
                prev_hash = chain.GENESIS
                adm_prev = chain.GENESIS
                adm_expected = 1
                for i, row in enumerate(log_rows, start=1):
                    if row.lsn != i:
                        raise StoreError(f"import: log is not contiguous at lsn {i}")
                    if self._chain:
                        self._check_imported_row(row, prev_hash)
                    prev_hash = row.entry_hash or prev_hash
                    self._s.put_log(row)
                    these = adm_by_lsn.get(row.lsn, [])
                    for a in these:
                        if a.seq != adm_expected:
                            raise StoreError(f"import: admission sequence is not contiguous at {adm_expected}")
                        target = self._s.log_by_report(a.report_id)
                        if target is None or (self._chain and target.entry_hash != a.report_entry_hash):
                            raise StoreError(f"import: admission {a.seq} does not match its report")
                        if self._chain:
                            want = chain.admission_entry_hash(a.prev_hash or "", a.seq, a.record, a.report_entry_hash or "")
                            if a.prev_hash != adm_prev or want != a.entry_hash:
                                raise StoreError(f"import: admission {a.seq} breaks the admission chain")
                            adm_prev = a.entry_hash or adm_prev
                        self._s.put_adm(a)
                        adm_expected += 1
                    self._s.set_meta(_GENERATION, str(row.generation))
                    if row.tomb is None and these:
                        entry = self._entry(row)
                        records = tuple(self._adm(a) for a in these)
                        beliefs += len(self._revise_stage(entry, records, row.generation, reviser))
                if adm_expected != len(adm_rows) + 1:
                    raise StoreError("import: admissions refer to log positions that do not exist")
                gen = max(int(footer["head"]["generation"]), max((r.generation for r in log_rows), default=0))
                self._s.set_meta(_GENERATION, str(gen))
                head = self.head()
                if head.lsn != footer["head"]["lsn"] or (self._chain and head.entry_hash != footer["head"]["entry_hash"]):
                    raise StoreError("import: the replayed head differs from the footer (the export was altered)")
            return ImportReport(log_rows=len(log_rows), admissions=len(adm_rows), inputs=len(input_rows), beliefs=beliefs, generation=gen, head=head)

    def _check_imported_row(self, row: LogRow, prev_hash: str) -> None:
        if row.prev_hash != prev_hash or row.commitment is None or row.entry_hash is None:
            raise StoreError(f"import: log row {row.lsn} does not link to the previous row")
        if chain.log_entry_hash(row.prev_hash, row.lsn, row.report_id, row.recorded_us, row.commitment) != row.entry_hash:
            raise StoreError(f"import: log row {row.lsn} has an entry_hash that does not match its metadata")
        if row.tomb is not None:
            if self._tombstone(row).entry_hash != row.entry_hash:
                raise StoreError(f"import: tombstone {row.lsn} does not carry the original entry_hash")
        elif row.content is None or row.salt is None or not hmac.compare_digest(chain.commitment(row.salt, row.content.encode("utf-8")), row.commitment):
            raise StoreError(f"import: log row {row.lsn} does not match its salted commitment")

    def _import_input(self, row: InputRow) -> None:
        self._s.put_input(row)
        if row.kind == InputKind.SCHEMA.value:
            schema = Schema.from_dict(parse_json(row.payload))
            mapping: dict[str, list[str]] = {}
            for a in schema.attrs:
                if a.rule is not None:
                    for r in a.rule.reads:
                        mapping.setdefault(r, []).append(a.name)
            self._s.put_attr_dependents(schema.version, {k: tuple(sorted(v)) for k, v in mapping.items()})


def _core(b: Belief) -> str:
    d = b.to_dict()
    return canonical_json({k: d[k] for k in ("segments", "pinned", "depends_on", "invalidated_by")})


def _is_empty_unknown(b: Belief) -> bool:
    return not b.pinned and not b.depends_on and all(s.kernel_status is KernelStatus.UNKNOWN for s in b.segments)
