"""Deterministic ULID-shaped ids for derived records (admission decisions).

An admission decision is a pure function of the log prefix and the admission inputs, so its id is
derived from its content: replaying the same log under the same admission version yields the same
record ids. (Report ids are assigned by the log, not here.)
"""

from __future__ import annotations

import hashlib

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def derive_ulid(*parts: str) -> str:
    """26 Crockford-base32 characters (a valid ULID shape) derived from ``parts``."""
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).digest()
    n = int.from_bytes(digest[:16], "big") >> 2  # 126 bits; the leading character stays <= '7'
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[n & 31])
        n >>= 5
    return "".join(reversed(chars))
