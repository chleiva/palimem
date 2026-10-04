"""Shared codec helpers for palimem types: canonical JSON, timestamps, strict decoding.

Canonical form (the one ``to_json`` emits and the conformance fixtures assume):
sorted keys, no whitespace, UTF-8 (no ASCII escaping), no NaN/Infinity, timestamps as
UTC ``YYYY-MM-DDTHH:MM:SS[.ffffff]Z``, tuples as arrays, absent optionals as explicit ``null``.
Decoding is strict: unknown keys are rejected; nullable keys may be omitted (read as null).

Standard library only.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Self, TypeVar

E = TypeVar("E", bound=Enum)
T = TypeVar("T")

# A JSON scalar usable as a proposition value. bool is accepted and kept distinct from int
# in canonical JSON (``true`` vs ``1``), so candidate ids differ.
Value = str | int | float | bool


class ValidationError(ValueError):
    """A record violates the contract (bad shape, bad value, or inconsistent fields)."""


# ---------------------------------------------------------------- canonical JSON

def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _reject_constant(name: str) -> Any:
    raise ValidationError(f"non-finite number {name} is not allowed in JSON")


def parse_json(text: str) -> Any:
    try:
        return json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as e:
        raise ValidationError(f"invalid JSON: {e}") from e


class Codec:
    """Mixin: ``to_json`` (canonical) and ``from_json`` on top of ``to_dict``/``from_dict``."""

    def to_dict(self) -> dict[str, Any]:  # pragma: no cover - overridden
        raise NotImplementedError

    @classmethod
    def from_dict(cls, d: Any) -> Self:  # pragma: no cover - overridden
        raise NotImplementedError

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_json(cls, text: str) -> Self:
        return cls.from_dict(parse_json(text))


# ---------------------------------------------------------------- timestamps

_TS_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})$"
)


def ts_to_str(t: datetime) -> str:
    if t.tzinfo is None or t.tzinfo.utcoffset(t) is None:
        raise ValidationError("timestamps must be timezone-aware")
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z")


def ts_from_str(s: Any, ctx: str = "timestamp") -> datetime:
    if not isinstance(s, str):
        raise ValidationError(f"{ctx}: expected an ISO-8601 timestamp string, got {type(s).__name__}")
    m = _TS_RE.match(s)
    if not m:
        raise ValidationError(f"{ctx}: not an ISO-8601 timestamp with a zone ('{s}')")
    y, mo, d, h, mi, sec, frac, tz = m.groups()
    micro = int((frac or "0").ljust(6, "0"))
    if tz == "Z":
        zone = UTC
    else:
        sign = 1 if tz[0] == "+" else -1
        zone = timezone(sign * timedelta(hours=int(tz[1:3]), minutes=int(tz[4:6])))
    try:
        return datetime(int(y), int(mo), int(d), int(h), int(mi), int(sec), micro, tzinfo=zone).astimezone(UTC)
    except ValueError as e:
        raise ValidationError(f"{ctx}: {e}") from e


def norm_ts(v: datetime | None, ctx: str) -> datetime | None:
    """Validate (aware) and normalise to UTC; used from ``__post_init__``."""
    if v is None:
        return None
    if not isinstance(v, datetime):
        raise ValidationError(f"{ctx}: expected a datetime, got {type(v).__name__}")
    if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
        raise ValidationError(f"{ctx}: datetime must be timezone-aware")
    return v.astimezone(UTC)


def req_ts(v: datetime, ctx: str) -> datetime:
    out = norm_ts(v, ctx)
    if out is None:
        raise ValidationError(f"{ctx}: a timestamp is required")
    return out


def opt_ts_str(t: datetime | None) -> str | None:
    return None if t is None else ts_to_str(t)


def set_field(obj: object, name: str, value: Any) -> None:
    """Assign on a frozen dataclass from ``__post_init__`` (normalisation only)."""
    object.__setattr__(obj, name, value)


# ---------------------------------------------------------------- strict decoding

def as_obj(x: Any, ctx: str, required: Iterable[str], optional: Iterable[str] = ()) -> dict[str, Any]:
    if not isinstance(x, dict):
        raise ValidationError(f"{ctx}: expected an object, got {type(x).__name__}")
    req, opt = set(required), set(optional)
    unknown = set(x) - req - opt
    if unknown:
        raise ValidationError(f"{ctx}: unknown field(s) {sorted(unknown)}")
    missing = req - set(x)
    if missing:
        raise ValidationError(f"{ctx}: missing field(s) {sorted(missing)}")
    return x


def as_str(x: Any, ctx: str) -> str:
    if not isinstance(x, str):
        raise ValidationError(f"{ctx}: expected a string, got {type(x).__name__}")
    return x


def as_int(x: Any, ctx: str) -> int:
    if isinstance(x, bool) or not isinstance(x, int):
        raise ValidationError(f"{ctx}: expected an integer, got {type(x).__name__}")
    return x


def as_bool(x: Any, ctx: str) -> bool:
    if not isinstance(x, bool):
        raise ValidationError(f"{ctx}: expected a boolean, got {type(x).__name__}")
    return x


def as_float(x: Any, ctx: str) -> float:
    if isinstance(x, bool) or not isinstance(x, int | float):
        raise ValidationError(f"{ctx}: expected a number, got {type(x).__name__}")
    return float(x)


def as_list(x: Any, ctx: str) -> list[Any]:
    if not isinstance(x, list):
        raise ValidationError(f"{ctx}: expected an array, got {type(x).__name__}")
    return x


def as_enum(cls: type[E], x: Any, ctx: str) -> E:
    try:
        return cls(x)
    except ValueError:
        allowed = [m.value for m in cls]
        raise ValidationError(f"{ctx}: {x!r} is not one of {allowed}") from None


def opt(x: Any, fn: Callable[[Any], T]) -> T | None:
    return None if x is None else fn(x)


def tuple_of(x: Any, ctx: str, fn: Callable[[Any, str], T]) -> tuple[T, ...]:
    return tuple(fn(v, f"{ctx}[{i}]") for i, v in enumerate(as_list(x, ctx)))


# ---------------------------------------------------------------- field validators

def check_value(v: Any, ctx: str) -> Value:
    if isinstance(v, bool | int | str):
        return v
    if isinstance(v, float):
        if not math.isfinite(v):
            raise ValidationError(f"{ctx}: non-finite float")
        return v
    raise ValidationError(f"{ctx}: values must be str, int, float or bool, got {type(v).__name__}")


def check_nonempty(s: Any, ctx: str) -> str:
    if not isinstance(s, str) or not s.strip():
        raise ValidationError(f"{ctx}: expected a non-empty string")
    return s


def check_nat(n: Any, ctx: str, minimum: int = 0) -> int:
    if isinstance(n, bool) or not isinstance(n, int):
        raise ValidationError(f"{ctx}: expected an integer, got {type(n).__name__}")
    if n < minimum:
        raise ValidationError(f"{ctx}: must be >= {minimum}")
    return n


ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


def check_ulid(s: Any, ctx: str) -> str:
    if not isinstance(s, str) or not ULID_RE.match(s):
        raise ValidationError(f"{ctx}: not a ULID (26 chars, Crockford base32)")
    return s


def check_hex64(s: Any, ctx: str) -> str:
    if not isinstance(s, str) or not HEX64_RE.match(s):
        raise ValidationError(f"{ctx}: expected 64 lowercase hex characters")
    return s


def check_order(lo: datetime | None, hi: datetime | None, ctx: str) -> None:
    if lo is not None and hi is not None and lo > hi:
        raise ValidationError(f"{ctx}: start {ts_to_str(lo)} is after end {ts_to_str(hi)}")
