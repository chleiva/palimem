"""Subset matcher and reference resolution of the conformance format (docs/CONFORMANCE.md).

A matcher is a JSON value compared with an actual JSON value. Objects match as subsets (keys absent
from the matcher are not checked); lists match element by element and by length unless wrapped in
``{"$unordered": [...]}``. Reserved strings: ``$any``, ``$absent``, ``$not:X``, ``$eq:REF``,
``$gt:REF``, ``$after:TS``. Reserved single-key objects: ``$unordered``, ``$contains``, ``$none``,
``$len``, ``$oneof``, ``$lt``, ``$lte``, ``$gt``, ``$gte`` (and ``$is`` / ``$isnot``, which the resolver
produces from ``$eq:REF`` / ``$not:REF``).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

RESERVED = {"any", "absent"}
_REF = re.compile(r"^\$([A-Za-z0-9_]+)((?:\.[A-Za-z0-9_]+)*)$")


class _Missing:
    def __repr__(self) -> str:
        return "<missing>"


MISSING = _Missing()


class State:
    """References (``$r1`` = id of the report appended with ``ref: r1``) and named op results."""

    def __init__(self) -> None:
        self.refs: dict[str, dict[str, Any]] = {}
        self.named: dict[str, Any] = {}

    def lookup(self, s: str) -> Any:
        """Resolve a ``$name.path`` string; returns MISSING if ``name`` is not bound."""
        m = _REF.match(s)
        if not m or m.group(1) in RESERVED:
            return MISSING
        name, path = m.group(1), [p for p in m.group(2).split(".") if p]
        if name in self.refs:
            cur: Any = self.refs[name]
            if not path:
                return cur.get("id", MISSING)
        elif name in self.named:
            cur = self.named[name]
        else:
            return MISSING
        for seg in path:
            if isinstance(cur, dict):
                cur = cur.get(seg, MISSING)
            elif isinstance(cur, list) and seg.isdigit() and int(seg) < len(cur):
                cur = cur[int(seg)]
            else:
                return MISSING
            if cur is MISSING:
                return MISSING
        return cur


def resolve_deep(v: Any, st: State) -> Any:
    """Replace bound ``$ref`` strings anywhere in ``v`` (operator strings are left alone)."""
    if isinstance(v, str):
        if v.startswith("$"):
            for prefix, opname in (("$eq:", "$is"), ("$gt:", "$gt"), ("$not:", "$isnot")):
                if v.startswith(prefix):
                    r = st.lookup(v[len(prefix):])
                    return v if r is MISSING else {opname: r}
            r = st.lookup(v)
            return v if r is MISSING else r
        return v
    if isinstance(v, list):
        return [resolve_deep(x, st) for x in v]
    if isinstance(v, dict):
        return {k: resolve_deep(x, st) for k, x in v.items()}
    return v


def _eq(a: Any, b: Any) -> bool:
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    return bool(a == b)


def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def match(exp: Any, act: Any, path: str = "$") -> list[str]:
    """Return a list of mismatch descriptions (empty = match)."""
    if isinstance(exp, str) and exp.startswith("$"):
        if exp == "$any":
            return [] if act is not MISSING else [f"{path}: expected a value, found nothing"]
        if exp == "$absent":
            return [] if act is MISSING or act is None else [f"{path}: expected absent, got {_short(act)}"]
        if exp.startswith("$not:"):  # literal form; the reference form is resolved to {"$isnot": value}
            return [f"{path}: expected anything but {exp[5:]!r}"] if act is MISSING or _eq(act, exp[5:]) else []
        if exp.startswith("$eq:"):
            return [] if act is not MISSING and _eq(act, exp[4:]) else [f"{path}: expected {exp[4:]!r}, got {_short(act)}"]
        if exp.startswith("$gt:"):
            return [f"{path}: unresolved reference {exp}"]
        if exp.startswith("$after:"):
            return [] if _ts(act) and _ts(exp[7:]) and _ts(act) > _ts(exp[7:]) else [f"{path}: expected after {exp[7:]}, got {_short(act)}"]
        # an unresolved $ref: the fixture is wrong or the referenced op never ran
        return [f"{path}: unresolved reference {exp}"]
    if isinstance(exp, dict):
        if len(exp) == 1 and next(iter(exp)).startswith("$"):
            return _operator(next(iter(exp)), next(iter(exp.values())), act, path)
        if not isinstance(act, dict):
            return [f"{path}: expected an object, got {_short(act)}"]
        errs: list[str] = []
        for k, sub in exp.items():
            errs += match(sub, act.get(k, MISSING), f"{path}.{k}")
        return errs
    if isinstance(exp, list):
        if not isinstance(act, list):
            return [f"{path}: expected a list, got {_short(act)}"]
        if len(exp) != len(act):
            return [f"{path}: expected {len(exp)} elements, got {len(act)}"]
        errs = []
        for i, (e, a) in enumerate(zip(exp, act, strict=True)):
            errs += match(e, a, f"{path}[{i}]")
        return errs
    return [] if act is not MISSING and _eq(exp, act) else [f"{path}: expected {_short(exp)}, got {_short(act)}"]


def _operator(op: str, arg: Any, act: Any, path: str) -> list[str]:
    if op == "$oneof":
        alts = [match(a, act, path) for a in arg]
        return [] if any(not e for e in alts) else [f"{path}: none of {_short(arg)} matched {_short(act)}"]
    if op == "$is":
        return [] if act is not MISSING and _eq(act, arg) else [f"{path}: expected {_short(arg)}, got {_short(act)}"]
    if op == "$isnot":
        return [f"{path}: expected anything but {_short(arg)}"] if act is MISSING or _eq(act, arg) else []
    if op in ("$lt", "$lte", "$gt", "$gte"):
        ok = _num(act) and _num(arg) and {"$lt": act < arg, "$lte": act <= arg, "$gt": act > arg, "$gte": act >= arg}[op]
        return [] if ok else [f"{path}: expected {op[1:]} {arg}, got {_short(act)}"]
    if not isinstance(act, (list, dict)):
        return [f"{path}: {op} needs a list, got {_short(act)}"]
    items = list(act) if isinstance(act, list) else list(act.values())
    if op == "$len":
        return [] if len(items) == arg else [f"{path}: expected length {arg}, got {len(items)}"]
    if op == "$contains":
        return [] if any(not match(arg, x, path) for x in items) else [f"{path}: no element matches {_short(arg)} in {_short(act)}"]
    if op == "$none":
        hit = next((x for x in items if not match(arg, x, path)), MISSING)
        return [] if hit is MISSING else [f"{path}: an element matched {_short(arg)} but none should: {_short(hit)}"]
    if op == "$unordered":
        if len(arg) != len(items):
            return [f"{path}: expected {len(arg)} elements (any order), got {len(items)}: {_short(act)}"]
        return [] if _assign(arg, items, 0, set()) else [f"{path}: elements do not match {_short(arg)} in any order: {_short(act)}"]
    return [f"{path}: unknown operator {op}"]


def _assign(exps: list[Any], items: list[Any], i: int, used: set[int]) -> bool:
    if i == len(exps):
        return True
    for j, it in enumerate(items):
        if j not in used and not match(exps[i], it) and _assign(exps, items, i + 1, used | {j}):
            return True
    return False


def _ts(x: Any) -> datetime | None:
    if not isinstance(x, str):
        return None
    try:
        return datetime.fromisoformat(x)
    except ValueError:
        return None


def _short(x: Any, n: int = 160) -> str:
    s = "<missing>" if x is MISSING else repr(x)
    return s if len(s) <= n else s[: n - 3] + "..."


# --- virtual fields on query results ------------------------------------------------------------------


def add_virtuals(result: Any) -> Any:
    """``_candidates`` (union by id of every candidate an Answer carries), ``_environments`` (sorted
    environments of ``provenance``) and ``_support_max_len`` (longest support list in the justified view)."""
    if not isinstance(result, dict) or "justified" not in result:
        return result
    seg = (result.get("justified") or {}).get("segment") or {}
    cands: list[dict[str, Any]] = []
    seen: set[str] = set()
    pool = [seg.get("established"), result.get("assertion"), *(seg.get("alternatives") or []), *(result.get("alternatives") or [])]
    for c in pool:
        if isinstance(c, dict):
            key = str(c.get("id") or repr(sorted(c.items(), key=str)))
            if key not in seen:
                seen.add(key)
                cands.append(c)
    out = dict(result)
    out["_candidates"] = cands
    out["_environments"] = sorted(sorted(s.get("environment", [])) for s in result.get("provenance") or [])
    support = seg.get("support") or {}
    out["_support_max_len"] = max((len(v) for v in support.values()), default=0)
    return out
