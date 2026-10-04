"""The backend semantics, implemented once on top of :class:`~palimem.store._storage.Storage`.

``Engine`` is what ``InMemoryBackend`` and ``SQLiteBackend`` both are. It implements, in one
transaction per append: log insert (+ salted chain), admission records (+ chain), the reviser's belief
versions, the current-version index and the generation stamp, becoming visible together or not at all.
It never decides admission and never computes beliefs.
"""

from __future__ import annotations

import hmac
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from palimem.store import chain
from palimem.store._storage import AdmRow, BeliefRow, InputRow, LogRow, Storage
from palimem.store.backend import (
    CAP_ERASE,
    CAP_EXPORT_HEAD,
    CAP_VERIFY_LOG,
    AdmissionContext,
    Admitter,
    AppendResult,
    CapabilityError,
    ErasureReason,
    FaultHook,
    Head,
    IdempotencyConflict,
    InputKind,
    InvalidRevision,
    RecoveryReport,
    Reviser,
    RevisionContext,
    RowStatus,
    StoreError,
    Tombstone,
    VerifyProblem,
    VerifyResult,
)
from palimem.store.ids import UlidFactory
from palimem.types import (
    AdmissionRecord,
    Belief,
    BeliefAsOf,
    Key,
    LogEntry,
    Report,
    Schema,
    canonical_json,
    parse_json,
)

_GENERATION = "generation"


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
    ) -> None:
        self._s = storage
        self._chain = chain_enabled
        self._secret = store_secret
        self._clock = clock or _utcnow
        self._fault_hook = fault
        self._ids = ids or UlidFactory()
        self._lock = threading.RLock()

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
        with self._lock:
            v = self._s.current_version(key)
            if v is None:
                return None
            row = self._s.belief_row(key, v)
            return None if row is None else self._belief(row)

    def belief_version(self, key: Key, version: int) -> Belief | None:
        with self._lock:
            row = self._s.belief_row(key, version)
            return None if row is None else self._belief(row)

    def belief_at(self, key: Key, as_of: BeliefAsOf) -> Belief | None:
        with self._lock:
            row = self._s.belief_row_as_of(key, self._lsn(as_of))
            return None if row is None else self._belief(row)

    def lsn_at(self, when: datetime) -> int:
        with self._lock:
            return self._s.lsn_at_us(chain.to_us(when))

    def key_dependents(self, key: Key) -> tuple[Key, ...]:
        with self._lock:
            return tuple(sorted(self._s.key_dependents(key), key=lambda k: (k.entity, k.attr)))

    def _input_lsn(self, as_of: BeliefAsOf | None) -> int:
        if as_of is None:
            lh = self._s.log_head()
            return (lh.lsn if lh else 0) + 1
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

    # ------------------------------------------------------------------ versioned inputs

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

    # ------------------------------------------------------------------ append

    def append(self, report: Report, *, idempotency_key: str, admitter: Admitter, reviser: Reviser) -> AppendResult:
        if report.id is not None:
            raise ValueError("report.id is assigned by the log; append an unassigned report")
        if not idempotency_key:
            raise ValueError("every append carries a client idempotency key")
        with self._lock:
            existing = self._s.log_by_idem(idempotency_key)
            if existing is not None:
                return self._replay(existing, report)
            result: AppendResult
            with self._s.transaction():
                self._fault("begin")
                existing = self._s.log_by_idem(idempotency_key)  # re-check under the write lock
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
        beliefs = tuple(self._belief(r) for r in self._s.belief_rows_at_lsn(row.lsn))
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

        # -- revision (computed elsewhere; stored here)
        s.set_meta(_GENERATION, str(generation))
        beliefs = tuple(reviser.revise(RevisionContext(entry=entry, admissions=records, generation=generation, view=self)))
        self._fault("after_revision")
        seen: set[Key] = set()
        for b in beliefs:
            if b.key in seen:
                raise InvalidRevision(f"revision returned two versions for key {b.key}")
            seen.add(b.key)
            if b.lsn != lsn:
                raise InvalidRevision(f"belief for {b.key} carries lsn {b.lsn}, expected {lsn}")
            cur = s.current_version(b.key) or 0
            if b.version != cur + 1:
                raise InvalidRevision(f"belief for {b.key} has version {b.version}, expected {cur + 1}")
        for b in beliefs:
            s.put_belief(
                BeliefRow(
                    key=b.key, version=b.version, lsn=b.lsn, recorded_us=chain.to_us(b.recorded_at),
                    required_generation=b.required_generation, completed_generation=b.completed_generation,
                    belief=b.to_json(), pins=tuple(sorted({p.report_id for p in b.pinned})),
                    deps=tuple((d.key, d.version) for d in b.depends_on),
                )
            )
        self._fault("after_beliefs")
        for b in beliefs:
            s.set_current(b.key, b.version)
        self._fault("after_index")
        return AppendResult(entry=entry, admissions=records, beliefs=beliefs, generation=generation)

    # ------------------------------------------------------------------ erasure (S-13)

    def erase(self, report_id: str, reason: ErasureReason) -> Tombstone:
        """Erase a report's content, raw reference and salt, leaving a minimal tombstone that carries the
        ORIGINAL entry hash so the chain still verifies. Belief versions that pinned the report are
        flagged ``reconstructable = False``. Dependency *repair* (recomputing dependants without the
        report) needs the reviser and is Lane C's T-C8, not done here."""
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
            actor = Report.from_dict(parse_json(row.content)).actor
            affected = sorted((chain.key_ref(secret, k), v) for k, v in self._s.versions_pinning(report_id))
            tomb = Tombstone(
                report_id=report_id, lsn=row.lsn, entry_hash=row.entry_hash if self._chain else None,
                key_ref=chain.key_ref(secret, row.key), actor_ref=chain.actor_ref(secret, actor),
                reason_class=reason, erased_at=self._clock(), affected_versions=tuple(affected),
            )
            self._s.replace_log(replace(row, key=None, content=None, salt=None, tomb=canonical_json(tomb.to_dict())))
            for k, v in self._s.versions_pinning(report_id):
                self._s.mark_unreconstructable(k, v)
            self._s.set_meta(_GENERATION, str(self._generation() + 1))
            return tomb

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

    def verify_beliefs(self, reviser: Reviser, *, keys: Sequence[Key] | None = None) -> VerifyResult:
        """Recompute beliefs with ``reviser.recompute`` and compare with the stored current versions (SEC-25)."""
        with self._lock:
            targets = list(keys) if keys is not None else self._s.current_keys()
            problems: list[VerifyProblem] = []
            for key in targets:
                v = self._s.current_version(key)
                if v is not None and self._s.belief_row(key, v + 1) is not None:
                    problems.append(VerifyProblem(kind="index_mismatch", key=key, version=v, detail="a newer belief version exists than the current-version index names"))
                stored = self.current_belief(key)
                if stored is not None and (stored.version != v or stored.key != key):
                    problems.append(VerifyProblem(kind="belief_row_mismatch", key=key, version=v, detail="the stored belief names a different key or version than its row"))
                fresh = reviser.recompute(key, self)
                if stored is None and fresh is None:
                    continue
                if stored is None or fresh is None or _core(stored) != _core(fresh):
                    problems.append(VerifyProblem(kind="belief_mismatch", key=key, version=v, detail="stored belief differs from the one recomputed from the log"))
            return VerifyResult(ok=not problems, checked=len(targets), problems=tuple(problems))

    # ------------------------------------------------------------------ recovery

    def recover(self) -> RecoveryReport:
        """Check the invariants an interrupted append must not have broken. Appends are atomic, so a
        crash leaves either the whole append or nothing; this proves it rather than assuming it."""
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
            return RecoveryReport(ok=not problems, head_lsn=head_lsn, generation=gen, problems=tuple(problems))


def _core(b: Belief) -> str:
    d = b.to_dict()
    return canonical_json({k: d[k] for k in ("segments", "pinned", "depends_on", "invalidated_by")})
