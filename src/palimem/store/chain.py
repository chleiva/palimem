"""Salted hash chain over the evidence log and the admission log (storage layer, S-13 amendment).

The chain is a property of the log *row*, never of ``Report`` (``prev_hash`` / ``entry_hash`` are
not Report fields; a backend without the chain is still contract-conformant).

Evidence log::

    commitment_i = SHA-256(salt_i || content_i)        content_i = canonical Report JSON without `id`
    entry_hash_i = SHA-256("palimem.log.v1\\n" prev_hash_i "\\n" canonical({lsn, recorded_us, report_id}) "\\n" commitment_i)

The chained metadata carries no key, actor or value, so a tombstone can keep ``entry_hash`` without
leaking anything. Erasing a row deletes ``content_i`` *and* ``salt_i``; ``commitment_i`` and
``entry_hash_i`` stay, so the next row's ``prev_hash`` still links (S-13), and the commitment is
unlinkable to a guessable value because the salt is gone.

Admission log: ``entry_hash = SHA-256("palimem.adm.v1\\n" prev "\\n" seq "\\n" canonical(record) "\\n" report_entry_hash)``.

This is tamper-*evidence* against anyone who cannot rewrite the whole chain; without an externally
anchored head (``export_head``) an attacker who recomputes every hash is not detected (T-24).
"""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import UTC, datetime, timedelta

from palimem.types import Key, Report, canonical_json

GENESIS = "0" * 64
SALT_BYTES = 16
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def new_salt() -> bytes:
    return os.urandom(SALT_BYTES)


def content_bytes(report: Report) -> bytes:
    """Canonical bytes of a report as committed: the id is log-assigned metadata, not content."""
    d = report.to_dict()
    d.pop("id", None)
    return canonical_json(d).encode("utf-8")


def commitment(salt: bytes, content: bytes) -> str:
    return hashlib.sha256(salt + content).hexdigest()


def log_entry_hash(prev_hash: str, lsn: int, report_id: str, recorded_us: int, commit: str) -> str:
    meta = canonical_json({"lsn": lsn, "recorded_us": recorded_us, "report_id": report_id})
    return hashlib.sha256(f"palimem.log.v1\n{prev_hash}\n{meta}\n{commit}".encode()).hexdigest()


def admission_entry_hash(prev_hash: str, seq: int, record_json: str, report_entry_hash: str) -> str:
    return hashlib.sha256(f"palimem.adm.v1\n{prev_hash}\n{seq}\n{record_json}\n{report_entry_hash}".encode()).hexdigest()


def key_ref(secret: bytes, key: Key) -> str:
    """Pseudonymous reference to a key for tombstones (S-13): HMAC under a host-held secret."""
    return hmac.new(secret, b"palimem.key\n" + canonical_json(key.to_dict()).encode(), hashlib.sha256).hexdigest()


def actor_ref(secret: bytes, actor: str) -> str:
    return hmac.new(secret, b"palimem.actor\n" + actor.encode(), hashlib.sha256).hexdigest()


def to_us(t: datetime) -> int:
    return (t.astimezone(UTC) - _EPOCH) // timedelta(microseconds=1)


def from_us(us: int) -> datetime:
    return _EPOCH + timedelta(microseconds=us)


def idem_ref(secret: bytes, idem_key: str) -> str:
    """What replaces a client idempotency key on an erased row (S-13): the key is client-chosen and may embed
    anything, so it is not kept; an HMAC keeps retries of the erased append recognisable."""
    return "erased:" + hmac.new(secret, b"palimem.idem\n" + idem_key.encode(), hashlib.sha256).hexdigest()
