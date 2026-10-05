"""Pseudonymising the key text of an *orphaned* entity after an erasure (S-13; author ruling 2026-10-05, item 8).

An erasure removes a report's content, plain key, salt and idempotency key from the log and redacts the beliefs that
pinned it. If the erased report was the **only** evidence about its entity (no live log row names the entity any more),
the entity's name would still sit in the belief index columns, the dependency, mark, subscription and outbox tables and
inside stored belief JSON. This module removes that last plain trace: the entity text becomes a keyed pseudonym
``erased:<hmac>`` everywhere in the store, and every storage access translates a plain key to its stored form, so
the key stays addressable by anyone who knows the name and can no longer be read off the database by anyone who does not.

* Only the **entity** is pseudonymised. The attribute name is schema vocabulary, already stored in the clear with the schema.
* The pseudonym is ``HMAC(store_secret, "palimem.entity\\n" + entity)``: the same name always maps to the same pseudonym,
  and the plain name is recoverable only by guessing it. The set of pseudonyms (never the names) is kept in the store's meta.
* If a new report about the entity arrives later, the entity is *re-identified* first (the plain name is legitimately in the
  log again): its rows are renamed back, so a live entity never has pseudonymised index rows.
* Beliefs of **other** entities that depended on the erased entity keep working: their ``depends_on`` is rewritten to the
  stored form, and every comparison against a recomputed belief normalises both sides the same way.

Known residual: free-text diagnostics (``Inference.reason``, a blocked job's reason) are rewritten at the moment of the rename
but not when written *afterwards*, since the plain name of an already pseudonymised entity is not stored and cannot be
searched for in free text.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

from palimem.store._storage import BeliefRow, JobRow, MarkRow, OutboxRow, Storage
from palimem.store.barrier import JobPayload
from palimem.store.views import belief_ref, parse_belief_ref
from palimem.types import Belief, BeliefView, Candidate, Key, Segment
from palimem.types._codec import canonical_json, parse_json

PREFIX = "erased:"
META_NAME = "entity_pseudonyms"

KeyMap = Callable[[Key], Key]
TextMap = Callable[[str], str]


def ref_of(secret: bytes, entity: str) -> str:
    return PREFIX + hmac.new(secret, b"palimem.entity\n" + entity.encode("utf-8"), hashlib.sha256).hexdigest()[:40]


class EntityPseudonyms:
    """The pseudonymised entities of one store (their pseudonyms only) and the plain-to-stored key translation."""

    def __init__(self, secret: bytes | None, refs: Iterable[str] = ()) -> None:
        self._secret = secret
        self.refs: set[str] = set(refs)

    # -- persistence (in the store's meta table: pseudonyms only, never a plain name)
    @classmethod
    def load(cls, secret: bytes | None, text: str | None) -> EntityPseudonyms:
        refs: list[str] = [] if not text else list(parse_json(text))
        return cls(secret, refs)

    def dump(self) -> str:
        return canonical_json(sorted(self.refs))

    # -- state
    @property
    def active(self) -> bool:
        return bool(self.refs) and self._secret is not None

    def is_ref(self, entity: str) -> bool:
        return entity in self.refs

    def ref_of(self, entity: str) -> str:
        if self._secret is None:
            raise ValueError("pseudonymising an entity needs the store secret")
        return ref_of(self._secret, entity)

    def entity(self, entity: str) -> str:
        """The stored form of an entity name: itself, unless that entity has been pseudonymised."""
        if not self.active or entity in self.refs:
            return entity
        r = self.ref_of(entity)
        return r if r in self.refs else entity

    def key(self, key: Key) -> Key:
        e = self.entity(key.entity)
        return key if e == key.entity else Key(entity=e, attr=key.attr)

    def add(self, ref: str) -> None:
        self.refs.add(ref)

    def discard(self, ref: str) -> None:
        self.refs.discard(ref)

    @contextmanager
    def guard(self) -> Iterator[None]:
        """Roll the in-memory set back if the surrounding storage transaction fails."""
        snap = set(self.refs)
        try:
            yield
        except BaseException:
            self.refs = snap
            raise


# ---------------------------------------------------------------------------- rekeying stored JSON


def _rekey_candidate(c: Candidate, fwd: KeyMap) -> Candidate:
    return Candidate(key=fwd(c.key), form=c.form)


def rekey_segment(seg: Segment, fwd: KeyMap) -> Segment:
    """The segment with every candidate re-keyed (a candidate id is a hash of its key, so ids and the support mapping move)."""
    ids: dict[str, str] = {}
    est = seg.established
    new_est = None
    if est is not None:
        new_est = _rekey_candidate(est, fwd)
        ids[est.id] = new_est.id
    alts = []
    for a in seg.alternatives:
        na = _rekey_candidate(a, fwd)
        ids[a.id] = na.id
        alts.append(na)
    if all(old == new for old, new in ids.items()):
        return seg
    support: Mapping[str, Any] = {ids.get(cid, cid): sups for cid, sups in seg.support.items()}
    return replace(seg, established=new_est, alternatives=tuple(alts), support=support)


def rekey_belief(b: Belief, fwd: KeyMap, text: TextMap | None = None) -> Belief:
    segs = tuple(rekey_segment(s, fwd) for s in b.segments)
    deps = tuple(replace(d, key=fwd(d.key)) for d in b.depends_on)
    inf = b.inference
    if text is not None and inf.reason:
        inf = replace(inf, reason=text(inf.reason))
    return replace(b, key=fwd(b.key), segments=segs, depends_on=deps, inference=inf)


def rekey_view(v: BeliefView, fwd: KeyMap, text: TextMap | None = None) -> BeliefView:
    ref = v.ref
    try:
        k, ver = parse_belief_ref(ref)
        ref = belief_ref(fwd(k), ver)
    except ValueError:  # a virtual handle: never stored
        pass
    inf = v.inference
    if text is not None and inf.reason:
        inf = replace(inf, reason=text(inf.reason))
    return replace(v, key=fwd(v.key), segment=rekey_segment(v.segment, fwd), ref=ref, inference=inf)


def belief_json(text: str, fwd: KeyMap, tx: TextMap | None = None) -> str:
    """Stored belief JSON with every key in stored form. A redacted version carries no key text and is returned as is."""
    d = parse_json(text)
    if not isinstance(d, dict) or d.get("redacted") is True:
        return text
    b = Belief.from_dict(d)
    nb = rekey_belief(b, fwd, tx)
    return text if nb == b else nb.to_json()


def outbox_json(text: str | None, fwd: KeyMap, tx: TextMap | None = None) -> str | None:
    if text is None:
        return None
    d = parse_json(text)
    out: dict[str, Any] = {}
    changed = False
    for side in ("old", "new"):
        raw = d.get(side)
        if raw is None:
            out[side] = None
            continue
        v = BeliefView.from_dict(raw)
        nv = rekey_view(v, fwd, tx)
        changed = changed or nv != v
        out[side] = nv.to_dict()
    return canonical_json(out) if changed else text


def job_json(text: str, fwd: KeyMap, tx: TextMap | None = None) -> str:
    p = JobPayload.from_json(text)
    seeds = tuple(fwd(k) for k in p.seeds)
    keys = None if p.keys is None else tuple(fwd(k) for k in p.keys)
    blocked = p.blocked if (tx is None or p.blocked is None) else tx(p.blocked)
    np_ = replace(p, seeds=seeds, keys=keys, blocked=blocked)
    return text if np_ == p else np_.to_json()


class EntityRewrite:
    """What a storage needs to rename an entity: how to rewrite each kind of stored JSON, and which entity moves."""

    def __init__(self, old: str, new: str) -> None:
        self.old, self.new = old, new

    def key(self, k: Key) -> Key:
        return k if k.entity != self.old else Key(entity=self.new, attr=k.attr)

    def text(self, s: str) -> str:
        return s.replace(self.old, self.new)

    def belief(self, text: str) -> str:
        return belief_json(text, self.key, self.text)

    def outbox(self, text: str | None) -> str | None:
        return outbox_json(text, self.key, self.text)

    def job(self, text: str) -> str:
        return job_json(text, self.key, self.text)


# ---------------------------------------------------------------------------- the storage wrapper


class PseudoStorage:
    """Translates plain keys to their stored form for every keyed storage call, so the engine never needs to know
    which entities were pseudonymised. Un-keyed calls pass straight through; calls that *return* keys return them in stored form."""

    def __init__(self, raw: Storage, ps: EntityPseudonyms) -> None:
        self._raw = raw
        self._ps = ps

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)

    # -- row rewriting on the way in
    def _belief_row(self, row: BeliefRow) -> BeliefRow:
        if not self._ps.active:
            return row
        return replace(
            row, key=self._ps.key(row.key), deps=tuple((self._ps.key(k), v) for k, v in row.deps),
            belief=belief_json(row.belief, self._ps.key),
        )

    # -- beliefs
    def current_version(self, key: Key) -> int | None:
        return self._raw.current_version(self._ps.key(key))

    def put_belief(self, row: BeliefRow) -> None:
        self._raw.put_belief(self._belief_row(row))

    def set_current(self, key: Key, version: int) -> None:
        self._raw.set_current(self._ps.key(key), version)

    def belief_row(self, key: Key, version: int) -> BeliefRow | None:
        return self._raw.belief_row(self._ps.key(key), version)

    def belief_row_as_of(self, key: Key, lsn: int) -> BeliefRow | None:
        return self._raw.belief_row_as_of(self._ps.key(key), lsn)

    def key_dependents(self, key: Key) -> list[Key]:
        return self._raw.key_dependents(self._ps.key(key))

    def mark_unreconstructable(self, key: Key, version: int) -> None:
        self._raw.mark_unreconstructable(self._ps.key(key), version)

    def redact_belief(self, key: Key, version: int, redacted: str) -> None:
        self._raw.redact_belief(self._ps.key(key), version, redacted)

    def belief_rows_for_key(self, key: Key, max_lsn: int | None) -> list[BeliefRow]:
        return self._raw.belief_rows_for_key(self._ps.key(key), max_lsn)

    # -- generation barrier
    def required_generation(self, key: Key) -> int:
        return self._raw.required_generation(self._ps.key(key))

    def set_required(self, key: Key, generation: int) -> None:
        self._raw.set_required(self._ps.key(key), generation)

    def put_mark(self, row: MarkRow) -> None:
        self._raw.put_mark(replace(row, key=self._ps.key(row.key)) if self._ps.active else row)

    def required_at(self, key: Key, lsn: int) -> int:
        return self._raw.required_at(self._ps.key(key), lsn)

    def put_job(self, row: JobRow) -> None:
        self._raw.put_job(self._job_row(row))

    def replace_job(self, row: JobRow) -> None:
        self._raw.replace_job(self._job_row(row))

    def _job_row(self, row: JobRow) -> JobRow:
        return row if not self._ps.active else replace(row, payload=job_json(row.payload, self._ps.key))

    # -- subscriptions and outbox
    def put_subscription(self, plan_id: str, key: Key) -> None:
        self._raw.put_subscription(plan_id, self._ps.key(key))

    def plans_for_key(self, key: Key) -> list[str]:
        return self._raw.plans_for_key(self._ps.key(key))

    def put_outbox(self, row: OutboxRow) -> None:
        if self._ps.active:
            row = replace(row, key=self._ps.key(row.key), payload=outbox_json(row.payload, self._ps.key))
        self._raw.put_outbox(row)

    def redact_outbox(self, key: Key, versions: tuple[int, ...]) -> None:
        self._raw.redact_outbox(self._ps.key(key), versions)


__all__ = [
    "META_NAME",
    "PREFIX",
    "EntityPseudonyms",
    "EntityRewrite",
    "PseudoStorage",
    "belief_json",
    "job_json",
    "outbox_json",
    "ref_of",
    "rekey_belief",
    "rekey_segment",
    "rekey_view",
]
