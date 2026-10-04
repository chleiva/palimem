"""Monotone ULID generation (stdlib only). Report and admission ids are assigned by the log."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_MAX_RANDOM = (1 << 80) - 1


def encode_ulid(timestamp_ms: int, randomness: int) -> str:
    if not 0 <= timestamp_ms < (1 << 48):
        raise ValueError("ULID timestamp out of range")
    if not 0 <= randomness <= _MAX_RANDOM:
        raise ValueError("ULID randomness out of range")
    n = (timestamp_ms << 80) | randomness
    chars = []
    for _ in range(26):
        chars.append(_ALPHABET[n & 31])
        n >>= 5
    return "".join(reversed(chars))


class UlidFactory:
    """Thread-safe, strictly increasing within one factory: in one millisecond the random part is
    incremented rather than redrawn, so ids sort in generation order.

    The LSN, not the ULID, is the canonical belief axis (S-05); ULIDs only need to be unique.
    """

    def __init__(self, now_ms: Callable[[], int] | None = None, rand: Callable[[int], bytes] = os.urandom) -> None:
        self._now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)
        self._rand = rand
        self._lock = threading.Lock()
        self._last_ms = -1
        self._last_rand = 0

    def new(self) -> str:
        with self._lock:
            ms = max(self._now_ms(), self._last_ms)
            if ms == self._last_ms:
                r = self._last_rand + 1
                if r > _MAX_RANDOM:  # 2^80 draws in one millisecond: roll the clock forward
                    ms += 1
                    r = int.from_bytes(self._rand(10), "big")
            else:
                r = int.from_bytes(self._rand(10), "big")
            self._last_ms, self._last_rand = ms, r
            return encode_ulid(ms, r)
