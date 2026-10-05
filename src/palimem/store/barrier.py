"""Generation-barrier helpers (T-C4): the static attribute graph, dirty-marker scope, job payloads.

Everything here is pure. The attribute dependency graph is known statically from the schema's rules
(``Attr.rule.reads``), which is what lets a traversal-budget overflow be scoped to the *connected component*
of the attributes it can reach instead of the whole store (THREAT_MODEL T-13, concern H4).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from palimem.types import Key, Schema, canonical_json, parse_json

DEFAULT_TRAVERSAL_BUDGET = 1000
"""Default number of keys one append's dependency traversal may visit (the design's 'traversal budget')."""

STORE_WIDE = "*"
"""Dirty-marker scope of last resort: every attribute (used only when no schema bounds the component)."""

STORE_FORMAT_VERSION = 2
EXPORT_FORMAT_VERSION = 1

_MAX_DEPTH = 10_000


def attr_components(schema: Schema) -> list[frozenset[str]]:
    """Connected components of the undirected attribute graph (edge: a derived attribute and each attribute
    its rule reads). Attributes with no rule edges are singleton components."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a in schema.attrs:
        find(a.name)
        if a.rule is not None:
            for r in a.rule.reads:
                parent[find(r)] = find(a.name)
    groups: dict[str, set[str]] = {}
    for name in list(parent):
        groups.setdefault(find(name), set()).add(name)
    return sorted((frozenset(g) for g in groups.values()), key=lambda g: sorted(g))


def component_of(schema: Schema | None, attr: str) -> frozenset[str] | None:
    """The attribute component containing ``attr``, or ``None`` if the schema does not bound it."""
    if schema is None:
        return None
    for comp in attr_components(schema):
        if attr in comp:
            return comp
    return None


def attr_depths(schema: Schema | None) -> dict[str, int]:
    """Derivation depth per attribute: 0 for a base attribute, ``1 + max(depth of what it reads)`` for a derived
    one. A dependency always goes from a derived attribute to attributes of strictly smaller depth, so sorting
    keys by depth is a valid topological order whatever entities they belong to."""
    if schema is None:
        return {}
    rules = {a.name: a.rule.reads for a in schema.attrs if a.rule is not None}
    memo: dict[str, int] = {}

    def depth(name: str, stack: tuple[str, ...]) -> int:
        if name in memo:
            return memo[name]
        reads = rules.get(name)
        if not reads or name in stack or len(stack) > _MAX_DEPTH:
            memo[name] = 0
            return 0
        memo[name] = 1 + max(depth(r, (*stack, name)) for r in reads)
        return memo[name]

    return {a.name: depth(a.name, ()) for a in schema.attrs}


def order_keys(keys: Iterable[Key], depths: dict[str, int]) -> list[Key]:
    return sorted(set(keys), key=lambda k: (depths.get(k.attr, 0), k.entity, k.attr))


def encode_attrs(attrs: Iterable[str]) -> str:
    return canonical_json(sorted(set(attrs)))


def decode_attrs(text: str) -> frozenset[str]:
    obj = parse_json(text)
    assert isinstance(obj, list)
    return frozenset(str(x) for x in obj)


JOB_REVISE = "revise"
JOB_REPAIR = "repair"


@dataclass(frozen=True, kw_only=True)
class JobPayload:
    """What a completion job remembers. ``keys`` is the list still to stamp, or ``None`` meaning 'recompute the
    dependency closure of ``seeds`` without a budget' (the traversal overflowed). Cleared when the job is done."""

    kind: str
    lsn: int
    seeds: tuple[Key, ...]
    keys: tuple[Key, ...] | None
    attempts: int = 0
    """Runs that ended without finishing the job; past ``MAX_JOB_ATTEMPTS`` the job is *blocked* (it is never retried
    in a loop: a key that cannot be completed, e.g. past the environment budget, is reported, not recomputed forever)."""
    blocked: str | None = None
    """Why the job stopped being retried (the first key left and its incompleteness reason), for the operator."""

    def to_json(self) -> str:
        d: dict[str, Any] = {
            "kind": self.kind,
            "lsn": self.lsn,
            "seeds": [k.to_dict() for k in self.seeds],
            "keys": None if self.keys is None else [k.to_dict() for k in self.keys],
        }
        if self.attempts:  # absent for a job that never failed: the payload stays byte-identical to the original form
            d["attempts"] = self.attempts
        if self.blocked is not None:
            d["blocked"] = self.blocked
        return canonical_json(d)

    @classmethod
    def from_json(cls, text: str) -> JobPayload:
        d: Any = parse_json(text)
        keys = d["keys"]
        return cls(
            kind=str(d["kind"]),
            lsn=int(d["lsn"]),
            seeds=tuple(Key.from_dict(k) for k in d["seeds"]),
            keys=None if keys is None else tuple(Key.from_dict(k) for k in keys),
            attempts=int(d.get("attempts", 0)),
            blocked=d.get("blocked"),
        )

    def after_failed_run(self, reason: str, *, limit: int) -> JobPayload:
        n = self.attempts + 1
        return JobPayload(
            kind=self.kind, lsn=self.lsn, seeds=self.seeds, keys=self.keys, attempts=n,
            blocked=reason if n >= limit else None,
        )

    def cleared(self) -> JobPayload:
        return JobPayload(kind=self.kind, lsn=self.lsn, seeds=(), keys=())


MAX_JOB_ATTEMPTS = 3
"""Completion attempts of one job that end with keys still unfinished before the job is marked ``blocked``."""

JOB_BLOCKED = "blocked"


def sorted_keys(keys: Sequence[Key]) -> list[Key]:
    return sorted(keys, key=lambda k: (k.entity, k.attr))
