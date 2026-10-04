"""palimem storage (T-C1/T-C2/T-C3/T-C10): the backend interface, the in-memory reference backend,
and the SQLite default with a salted hash chain over the evidence and admission logs.

See docs/STORAGE.md.
"""

from palimem.store.backend import (
    APPEND_STEPS,
    CAP_ERASE,
    CAP_EXPORT_HEAD,
    CAP_VERIFY_LOG,
    AdmissionContext,
    Admitter,
    AppendResult,
    Backend,
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
    StoreBusy,
    StoreError,
    StoreView,
    Tombstone,
    VerifyProblem,
    VerifyResult,
)
from palimem.store.ids import UlidFactory
from palimem.store.memory import InMemoryBackend
from palimem.store.sqlite import STORE_FORMAT_VERSION, SQLiteBackend

__all__ = [
    "APPEND_STEPS",
    "CAP_ERASE",
    "CAP_EXPORT_HEAD",
    "CAP_VERIFY_LOG",
    "STORE_FORMAT_VERSION",
    "AdmissionContext",
    "Admitter",
    "AppendResult",
    "Backend",
    "CapabilityError",
    "ErasureReason",
    "FaultHook",
    "Head",
    "IdempotencyConflict",
    "InMemoryBackend",
    "InputKind",
    "InvalidRevision",
    "RecoveryReport",
    "Reviser",
    "RevisionContext",
    "RowStatus",
    "SQLiteBackend",
    "StoreBusy",
    "StoreError",
    "StoreView",
    "Tombstone",
    "UlidFactory",
    "VerifyProblem",
    "VerifyResult",
]
