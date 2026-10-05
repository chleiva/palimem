"""The backend interface (T-C1): what a storage backend must provide, and the two injected stages.

A backend owns the *evidence log*, the *admission log*, *belief versions*, *versioned inputs* and the
indexes. It does **not** decide admission and does **not** compute beliefs: both are injected for
one append, so Lane B's kernel and Lane D's admission plug in without the store knowing them.

Core operations (every conformant backend)::

    append(report, idempotency_key, admitter, reviser) -> AppendResult   one atomic revision transaction
    current_belief(key) / belief_at(key, as_of) / belief_version(key, v)  as_of = LSN or timestamp (S-05)
    scan(from_lsn, to_lsn)                                                replay
    key_dependents(key) / attr_dependents(attr)                           dependency lookup
    put_schema / put_input / schema / input_at                            versioned inputs

Optional capabilities (``capabilities``): ``verify_log`` and ``export_head`` (the salted hash chain,
a storage-layer property; S-13). A backend without them is still contract-conformant.

Wave 2 adds the generation barrier (``read_belief``, ``complete_pending``; T-C4), the notification outbox
(``subscribe``, ``pending_events``, ``deliver``; T-C5), ``historical_inputs`` (T-C6), erasure with dependency
repair (``erase(..., reviser=)``; T-C8) and JSONL ``export_jsonl`` / ``import_jsonl`` (T-C9).

``belief_as_of`` resolves to the version of a key with the greatest ``Belief.lsn`` at or before the
requested LSN; a timestamp maps to the last LSN whose ``recorded_at`` is at or before it. The log keeps
``recorded_at`` non-decreasing along LSNs (it is clamped to the previous row on clock skew), so that
mapping is well defined.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, Self, runtime_checkable

from palimem.types import (
    AdmissionRecord,
    Belief,
    BeliefAsOf,
    BeliefView,
    Key,
    LastComplete,
    LogEntry,
    Report,
    ResourceLimited,
    ResourceLimitedReason,
    Schema,
    ValidationError,
    canonical_json,
    parse_json,
)

# ----------------------------------------------------------------------------- errors


class StoreError(Exception):
    """Base class of store errors."""


class IdempotencyConflict(StoreError):
    """The idempotency key was already used for a *different* report."""


class CapabilityError(StoreError):
    """The backend does not implement an optional capability (e.g. the hash chain)."""


class StoreBusy(StoreError):
    """Another writer holds the write lock and the busy timeout elapsed."""


class InvalidRevision(StoreError):
    """The reviser or admitter returned something the store cannot commit (nothing is written)."""


# ----------------------------------------------------------------------------- fault injection

APPEND_STEPS: tuple[str, ...] = (
    "begin",
    "after_log_insert",
    "after_admission",
    "after_revision",
    "after_beliefs",
    "after_index",
    "after_barrier",
    "before_commit",
    "after_commit",
)
"""Names passed to the ``fault`` hook, in order. Raising (or ``os._exit``-ing) at any step before
``after_commit`` must leave no trace of the append; at ``after_commit`` the append is durable and a
retry with the same idempotency key replays it."""

COMPLETION_STEPS: tuple[str, ...] = ("completion_stamped", "completion_before_commit")
"""Fault-hook names inside ``complete_pending`` (once per stamped key, then before the job is marked done). A fault
rolls back the whole job: no partial versions, the job stays pending."""

ERASE_STEPS: tuple[str, ...] = ("erase_after_redact", "erase_after_repair")
"""Fault-hook names inside ``erase``. A fault rolls the whole erasure back: the report, its beliefs and its
idempotency key are exactly as before."""

FaultHook = Callable[[str], None]


# ----------------------------------------------------------------------------- records


class InputKind(str, Enum):
    """Versioned inputs without which a belief cannot be reproduced (design v0.3 §Storage layout)."""

    SCHEMA = "schema"
    SEMANTIC = "semantic"
    ADMISSION = "admission"  # includes the authority grant table (S-07): a grant change is an admission version
    POLICY = "policy"


class ErasureReason(str, Enum):
    """Coarse reason class of an erasure: no free text (S-13)."""

    ERASURE_REQUEST = "erasure_request"
    LEGAL_HOLD_RELEASE = "legal_hold_release"
    RETENTION_EXPIRY = "retention_expiry"
    OTHER = "other"


@dataclass(frozen=True, kw_only=True)
class Tombstone:
    """What remains of an erased report (S-13): no plain key, value or free text.

    ``entry_hash`` is the ORIGINAL chain hash of the row (``None`` on a chainless backend), so the
    next row's ``prev_hash`` still links across the tombstone.
    """

    report_id: str
    lsn: int
    entry_hash: str | None
    key_ref: str  # HMAC(store_secret, key)
    actor_ref: str  # HMAC(store_secret, actor): the AUTHOR of the erased report
    reason_class: ErasureReason
    erased_at: datetime
    affected_versions: tuple[tuple[str, int], ...] = ()  # (key_ref, belief version): no longer reconstructable
    requester_ref: str | None = None  # HMAC(store_secret, requester): who asked for the erasure (never the plain id)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "report_id": self.report_id,
            "lsn": self.lsn,
            "entry_hash": self.entry_hash,
            "key_ref": self.key_ref,
            "actor_ref": self.actor_ref,
            "reason_class": self.reason_class.value,
            "erased_at_us": _us(self.erased_at),
            "affected_versions": [[k, v] for k, v in self.affected_versions],
        }
        if self.requester_ref is not None:  # absent on tombstones written before the requester was recorded
            out["requester_ref"] = self.requester_ref
        return out

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Self:
        from palimem.store.chain import from_us

        return cls(
            report_id=str(d["report_id"]),
            lsn=int(d["lsn"]),
            entry_hash=d["entry_hash"],
            key_ref=str(d["key_ref"]),
            actor_ref=str(d["actor_ref"]),
            reason_class=ErasureReason(d["reason_class"]),
            erased_at=from_us(int(d["erased_at_us"])),
            affected_versions=tuple((str(k), int(v)) for k, v in d["affected_versions"]),
            requester_ref=None if d.get("requester_ref") is None else str(d["requester_ref"]),
        )


def _us(t: datetime) -> int:
    from palimem.store.chain import to_us

    return to_us(t)


@dataclass(frozen=True, kw_only=True)
class Head:
    """The chain heads at one moment: export it somewhere an attacker cannot rewrite (T-24)."""

    lsn: int
    entry_hash: str | None
    admission_seq: int
    admission_hash: str | None
    generation: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "lsn": self.lsn,
            "entry_hash": self.entry_hash,
            "admission_seq": self.admission_seq,
            "admission_hash": self.admission_hash,
            "generation": self.generation,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Self:
        return cls(
            lsn=int(d["lsn"]),
            entry_hash=d.get("entry_hash"),
            admission_seq=int(d["admission_seq"]),
            admission_hash=d.get("admission_hash"),
            generation=int(d["generation"]),
        )

    @classmethod
    def from_json(cls, text: str) -> Self:
        obj = parse_json(text)
        if not isinstance(obj, dict):
            raise ValidationError("head: expected a JSON object")
        return cls.from_dict(obj)


@dataclass(frozen=True, kw_only=True)
class VerifyProblem:
    kind: str  # see docs/STORAGE.md §Verification for the vocabulary
    detail: str
    lsn: int | None = None
    key: Key | None = None
    version: int | None = None


@dataclass(frozen=True, kw_only=True)
class RowStatus:
    """Per-row result of ``verify_log``. A tombstoned row is ``linked`` but not ``content_verified`` (S-13)."""

    lsn: int
    linked: bool
    content_verified: bool
    tombstoned: bool


@dataclass(frozen=True, kw_only=True)
class VerifyResult:
    ok: bool
    checked: int
    problems: tuple[VerifyProblem, ...] = ()
    rows: tuple[RowStatus, ...] = ()
    checkpoint_lsn: int | None = None  # incremental mode: the log head the next incremental run starts after


@dataclass(frozen=True, kw_only=True)
class RecoveryReport:
    ok: bool
    head_lsn: int
    generation: int
    problems: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class AppendResult:
    """Outcome of one append. On a replay (same idempotency key) ``replayed`` is True and the stored
    result is returned; if the report has since been erased, ``entry`` is None and ``tombstone`` is set."""

    entry: LogEntry | None
    admissions: tuple[AdmissionRecord, ...]
    beliefs: tuple[Belief, ...]
    generation: int
    replayed: bool = False
    tombstone: Tombstone | None = None


@dataclass(frozen=True, kw_only=True)
class LimitedRead:
    """A read that has no valid result for the requested snapshot (T-C4): the generation barrier.

    Carries no belief as current. ``last_complete`` is an OLDER version, complete when it was written, to be
    labelled with its own ``lsn`` (``belief_as_of``) and never presented as current."""

    reason: ResourceLimitedReason
    required_generation: int
    completed_generation: int
    reason_key: Key | None = None
    last_complete: Belief | None = None

    def to_answer(self, valid_at: datetime | None = None) -> ResourceLimited:
        """The contract ``ResourceLimited`` (no segment, no kernel_status), with ``last_complete`` labelled by
        the ``belief_as_of`` (LSN) of the older version and cut to the segment at ``valid_at``."""
        from palimem.store.views import belief_view

        last: LastComplete | None = None
        if self.last_complete is not None:
            view = belief_view(self.last_complete, valid_at)
            if view is not None:
                last = LastComplete(belief_as_of=self.last_complete.lsn, view=view)
        return ResourceLimited(
            reason=self.reason,
            required_generation=self.required_generation,
            completed_generation=self.completed_generation,
            reason_key=self.reason_key,
            last_complete=last,
        )


@dataclass(frozen=True, kw_only=True)
class NotReconstructable:
    """The version in force at this snapshot was redacted by an erasure (T-C8, S-13): the historical answer
    can no longer be reconstructed. The current belief of the key, if any, was repaired and is readable."""

    key: Key
    version: int
    lsn: int


BeliefRead = Belief | LimitedRead | NotReconstructable | None


@dataclass(frozen=True, kw_only=True)
class InputsAt:
    """The versioned inputs in force at a log position (T-C6). Historical queries are evaluated under these."""

    schema: Schema | None
    semantic: tuple[int, Mapping[str, Any]] | None
    admission: tuple[int, Mapping[str, Any]] | None
    policy: tuple[int, Mapping[str, Any]] | None
    lsn: int = 0

    def versions(self) -> dict[str, int]:
        out: dict[str, int] = {}
        if self.schema is not None:
            out[InputKind.SCHEMA.value] = self.schema.version
        for kind, v in ((InputKind.SEMANTIC, self.semantic), (InputKind.ADMISSION, self.admission), (InputKind.POLICY, self.policy)):
            if v is not None:
                out[kind.value] = v[0]
        return out


@dataclass(frozen=True, kw_only=True)
class OutboxEvent:
    """A durable notification (T-C5). ``event_id = hash(key, old version, new version)`` is stable across
    redelivery; delivery is at-least-once, so subscribers must process idempotently on it. The views are
    ``None`` when an erasure redacted the versions the event mentions."""

    event_id: str
    plan_id: str
    key: Key
    old_version: int | None
    new_version: int
    lsn: int
    old_view: BeliefView | None
    new_view: BeliefView | None
    created_at: datetime
    redacted: bool = False


@dataclass(frozen=True, kw_only=True)
class CompletionReport:
    """Outcome of ``complete_pending``: jobs finished, versions stamped, jobs still pending."""

    jobs_done: int
    keys_stamped: int
    jobs_pending: int
    skipped_newer: int = 0  # keys left alone because a newer generation had already completed them
    jobs_blocked: int = 0  # jobs given up after repeated unfinished runs (see ``Engine.retry_blocked``)
    # Both lists aggregate over the jobs of one run, so a key can be in both: stamped by an earlier job, then found
    # already complete by a later one (a newer generation completed it, so the later job left it alone).
    stamped: tuple[Key, ...] = ()  # the keys this run wrote a version for (``len == keys_stamped``)
    skipped: tuple[Key, ...] = ()  # the keys it left alone because a newer generation had completed them (``len == skipped_newer``)


@dataclass(frozen=True, kw_only=True)
class ImportReport:
    log_rows: int
    admissions: int
    inputs: int
    beliefs: int
    generation: int
    head: Head


# ----------------------------------------------------------------------------- read view


@runtime_checkable
class StoreView(Protocol):
    """Read-only access. Inside an append the view includes the not-yet-committed rows of that append."""

    def head(self) -> Head: ...
    def get_entry(self, report_id: str) -> LogEntry | Tombstone | None: ...
    def scan(self, from_lsn: int = 1, to_lsn: int | None = None) -> Iterator[LogEntry | Tombstone]: ...
    def entries_for_key(self, key: Key, to_lsn: int | None = None) -> tuple[LogEntry, ...]:
        """Live (non-erased) entries on a key, in LSN order."""
        ...

    def admissions_for_report(self, report_id: str) -> tuple[AdmissionRecord, ...]: ...
    def admissions_for_key(self, key: Key, to_lsn: int | None = None) -> tuple[AdmissionRecord, ...]:
        """Every admission record of the (live) reports on a key, in decision order."""
        ...

    def current_belief(self, key: Key) -> Belief | None: ...
    def belief_version(self, key: Key, version: int) -> Belief | None: ...
    def belief_at(self, key: Key, as_of: BeliefAsOf) -> Belief | None: ...
    def lsn_at(self, when: datetime) -> int:
        """Greatest LSN recorded at or before ``when`` (0 if none)."""
        ...

    def key_dependents(self, key: Key) -> tuple[Key, ...]:
        """Keys whose *current* belief depends on ``key``."""
        ...

    def attr_dependents(self, attr: str, as_of: BeliefAsOf | None = None) -> tuple[str, ...]:
        """Derived attributes that read ``attr`` under the schema current at ``as_of`` (default: now)."""
        ...

    def schema(self, as_of: BeliefAsOf | None = None) -> Schema | None: ...
    def input_at(self, kind: InputKind, as_of: BeliefAsOf | None = None) -> tuple[int, Mapping[str, Any]] | None:
        """(version, payload) of the input current at ``as_of`` (default: now)."""
        ...


# ----------------------------------------------------------------------------- injected stages


@dataclass(frozen=True, kw_only=True)
class AdmissionContext:
    entry: LogEntry
    view: StoreView
    new_id: Callable[[], str]  # mints ULIDs for AdmissionRecord.id


class Admitter(Protocol):
    """Decides admission (Lane D). Must return at least one record for ``ctx.entry.report.id``; it may
    return more (e.g. a later report confirming an earlier quarantined one). The store only stores them."""

    def admit(self, ctx: AdmissionContext) -> Sequence[AdmissionRecord]: ...


@dataclass(frozen=True, kw_only=True)
class RevisionContext:
    entry: LogEntry
    admissions: tuple[AdmissionRecord, ...]
    generation: int  # the store generation this append establishes
    view: StoreView
    inputs: Mapping[str, int] = field(default_factory=dict)  # versions of the schema/semantic/admission/policy inputs in force (T-C6)


class Reviser(Protocol):
    """Computes belief versions (Lane B). ``revise`` returns the new versions for the touched key and
    every derived key that reads it. Each returned Belief must carry ``lsn == ctx.entry.lsn`` and
    ``version == previous + 1`` (1 for a new key), and appear once per key.

    ``recompute`` rebuilds a key's belief from the log alone, as of the head, for ``verify_beliefs``;
    its ``segments``, ``pinned``, ``depends_on`` and ``invalidated_by`` must equal the stored current
    belief or the store reports ``belief_mismatch`` (SEC-25). It is also what the store calls to *finish*
    keys left incomplete (``complete_pending``, T-C4) and to *repair* beliefs after an erasure (T-C8): the
    store stamps ``version``, ``lsn`` and the generations itself, so only the content matters. It must work
    for a key that has no stored belief yet, and read the base keys' CURRENT beliefs from ``view``
    (dependencies are recomputed first).
    """

    def revise(self, ctx: RevisionContext) -> Sequence[Belief]: ...
    def recompute(self, key: Key, view: StoreView) -> Belief | None: ...


# ----------------------------------------------------------------------------- the backend


@runtime_checkable
class Backend(StoreView, Protocol):
    capabilities: frozenset[str]

    def append(self, report: Report, *, idempotency_key: str, admitter: Admitter, reviser: Reviser) -> AppendResult: ...
    def put_schema(self, schema: Schema) -> None: ...
    def put_input(self, kind: InputKind, version: int, payload: Mapping[str, Any]) -> None: ...
    def erase(
        self, report_id: str, reason: ErasureReason, *, reviser: Reviser | None = None, requester: str | None = None
    ) -> Tombstone: ...
    def pseudonym_of(self, principal: str) -> str: ...
    def recover(self) -> RecoveryReport: ...
    def close(self) -> None: ...

    # generation barrier (T-C4)
    def read_belief(self, key: Key, as_of: BeliefAsOf | None = None) -> BeliefRead: ...
    def complete_pending(self, reviser: Reviser, *, limit: int | None = None) -> CompletionReport: ...
    def retry_blocked(self) -> int: ...

    # notifications (T-C5)
    def subscribe(self, plan_id: str, keys: Sequence[Key]) -> None: ...
    def unsubscribe(self, plan_id: str) -> None: ...
    def subscriptions(self, plan_id: str) -> tuple[Key, ...]: ...
    def pending_events(self, limit: int = 100) -> tuple[OutboxEvent, ...]: ...
    def ack_event(self, event_id: str, plan_id: str) -> None: ...
    def deliver(self, handler: Callable[[OutboxEvent], None], *, limit: int = 100) -> int: ...

    # versioned inputs, export and import (T-C6, T-C9)
    def historical_inputs(self, as_of: BeliefAsOf | None = None) -> InputsAt: ...
    def export_jsonl(self) -> Iterator[str]: ...
    def import_jsonl(self, lines: Iterable[str], *, reviser: Reviser) -> ImportReport: ...

    # optional capabilities
    def verify_log(self, from_lsn: int = 1, to_lsn: int | None = None, *, anchor: Head | None = None) -> VerifyResult: ...
    def export_head(self) -> Head: ...
    def verify_beliefs(
        self, reviser: Reviser, *, keys: Sequence[Key] | None = None, since_lsn: int | None = None
    ) -> VerifyResult: ...
    def verify_beliefs_incremental(self, reviser: Reviser) -> VerifyResult: ...


CAP_VERIFY_LOG = "verify_log"
CAP_EXPORT_HEAD = "export_head"
CAP_ERASE = "erase"
