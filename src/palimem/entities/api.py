"""The host API of entity resolution: record merges, undo them, propose them, and resolve names (``find``).

Everything here is **privileged host code**. The agent tool API has no merge tool, refuses the reserved
``__entity_merge__`` attribute, and the registry ignores any marker an agent principal could write
(:func:`palimem.entities.registry.from_host`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from palimem.entities.layer import EntityLayer
from palimem.entities.normalize import canonical_form
from palimem.entities.registry import (
    ENTITY_MERGE_ATTR,
    MergeDecision,
    MergeOp,
    encode_marker,
)
from palimem.entities.resolver import (
    LexicalResolver,
    MergeProposal,
    ResolverBackend,
    ResolverPolicy,
    propose_merges,
    similarity,
)
from palimem.types import (
    BeliefAsOf,
    Cue,
    Key,
    LogEntry,
    MemberProp,
    Origin,
    Query,
    Source,
)
from palimem.types import Report as _Report

if TYPE_CHECKING:
    from palimem.memory import Memory


class EntitiesError(Exception):
    """Base class of entity-resolution errors."""


class EntitiesNotEnabled(EntitiesError):
    """The schema does not declare the reserved ``__entity_merge__`` attribute (see :func:`enable_entity_merges`)."""


class UnknownEntity(EntitiesError):
    """A merge named an entity that appears in no report."""


class MergeRejected(EntitiesError):
    """The merge marker was logged but not honoured (not admitted, or not from a host principal)."""


def attach_layer(memory: Memory) -> EntityLayer:
    """Attach (or return) the entity layer of a :class:`~palimem.memory.Memory`. Called automatically when the schema
    declares the reserved attribute, so a reopened store keeps resolving merged entities."""
    layer = memory.pipeline.layer
    if layer is None:
        layer = EntityLayer()
        memory.pipeline.layer = layer
        layer.bind(memory.backend)
    return layer


def merge_attr_spec() -> object:
    """The kernel's attribute spec of the reserved attribute (a set of marker texts, no inertia)."""
    from palimem.kernel import AttrSpec

    return AttrSpec(ENTITY_MERGE_ATTR, "multi", False, competing_values=False)


def enable_entity_merges(schema: object) -> object:
    """A copy of a contract ``Schema`` that declares the reserved attribute (version bumped by one)."""
    from dataclasses import replace

    from palimem.types import Attr, AttrClass, ValueType

    if any(a.name == ENTITY_MERGE_ATTR for a in schema.attrs):  # type: ignore[attr-defined]
        return schema
    attr = Attr(name=ENTITY_MERGE_ATTR, attr_class=AttrClass.MULTI_SET, value_type=ValueType.STRING)
    return replace(schema, version=schema.version + 1, attrs=(*schema.attrs, attr))  # type: ignore[type-var,attr-defined]


@dataclass(frozen=True)
class MergeRecord:
    """A merge the host recorded (the honoured decision plus the entity classes it produced)."""

    decision: MergeDecision
    representative: str
    members: tuple[str, ...]
    rewritten: tuple[Key, ...] = ()  # the beliefs this decision wrote (the marker's own key included)

    @property
    def id(self) -> str:
        return self.decision.id


@dataclass(frozen=True)
class KeyCandidate:
    """One answer of :meth:`Entities.find`: a key, the surface names that matched, and its current status."""

    entity: str  # the representative of the class
    attr: str
    matched: tuple[str, ...]  # member names (or the text itself) that scored
    score: float
    kernel_status: str | None
    ambiguous: bool  # another class scored within ``AMBIGUITY_MARGIN`` of the best

    @property
    def key(self) -> Key:
        return Key(entity=self.entity, attr=self.attr)


AMBIGUITY_MARGIN = 0.1


class Entities:
    """Entity resolution over one :class:`~palimem.memory.Memory` (host API)."""

    def __init__(
        self, memory: Memory, *, source: Source | None = None, actor: str = "system:entity-resolution",
        origin_group: str = "system:entity-resolution", kind: str = "auto", aliases: object | None = None,
    ) -> None:
        self.mem = memory
        self.enabled = any(a.name == ENTITY_MERGE_ATTR for a in memory.schema.attrs)
        """``find`` and ``propose`` work on any store; recording merges needs the reserved attribute in the schema."""
        self.layer = attach_layer(memory) if self.enabled else EntityLayer()
        if not self.enabled:
            self.layer.bind(memory.backend)
        self.source = source or Source(id=actor, cls="trusted")
        self.actor = actor
        self.origin_group = origin_group
        self.kind = kind
        self._attr_aliases: dict[str, str] = {}
        self._backend = LexicalResolver(kind=kind, aliases=aliases)  # type: ignore[arg-type]
        self._ent_scanned = 0
        self._ent_counts: dict[str, int] = {}

    def _need_enabled(self) -> None:
        if not self.enabled:
            raise EntitiesNotEnabled(
                f"declare the reserved attribute {ENTITY_MERGE_ATTR!r} (enable_entity_merges) before recording merges"
            )

    # -- entities in the log

    def _scan_entities(self) -> None:
        head = self.mem.backend.head().lsn
        if head <= self._ent_scanned:
            return
        for row in self.mem.backend.scan(self._ent_scanned + 1, head):
            if isinstance(row, LogEntry) and row.report.key.attr != ENTITY_MERGE_ATTR:
                self._ent_counts[row.report.key.entity] = self._ent_counts.get(row.report.key.entity, 0) + 1
        self._ent_scanned = head

    def entity_counts(self) -> Mapping[str, int]:
        """Entity -> number of reports about it (merge markers excluded)."""
        self._scan_entities()
        return dict(self._ent_counts)

    def known_entities(self) -> tuple[str, ...]:
        return tuple(sorted(self.entity_counts()))

    # -- state

    def _state(self, as_of: BeliefAsOf | None = None):  # type: ignore[no-untyped-def]
        return self.layer.state_at(self.mem.lsn_of(as_of))

    def canonical(self, entity: str, as_of: BeliefAsOf | None = None) -> str:
        return self._state(as_of).canon(entity)

    def members(self, entity: str, as_of: BeliefAsOf | None = None) -> tuple[str, ...]:
        return self._state(as_of).members(entity)

    def merges(self, as_of: BeliefAsOf | None = None) -> tuple[MergeDecision, ...]:
        """The active merges at a snapshot."""
        lsn = self.mem.lsn_of(as_of)
        self.layer.sync(lsn)
        return self.layer.registry.active_merges(lsn)

    def history(self, as_of: BeliefAsOf | None = None) -> tuple[MergeDecision, ...]:
        """Every honoured decision (merges and unmerges) up to a snapshot, in log order."""
        lsn = self.mem.lsn_of(as_of)
        self.layer.sync(lsn)
        return self.layer.registry.history(lsn)

    # -- decisions

    def _append(self, entity: str, text: str, idempotency_key: str | None) -> tuple[str, tuple[Key, ...]]:
        rep = _Report(
            key=Key(entity=entity, attr=ENTITY_MERGE_ATTR), cue=Cue.ASSERT, proposition=MemberProp(value=text),
            source=self.source, origin=Origin.EXTERNAL_OBSERVATION, origin_group=self.origin_group, actor=self.actor,
        )
        res = self.mem.append(rep, idempotency_key=idempotency_key)
        assert res.entry is not None and res.entry.report.id is not None
        return res.entry.report.id, tuple(b.key for b in res.beliefs)

    def merge(
        self, alias: str, into: str, *, reason: str, method: str = "manual", score: float | None = None,
        idempotency_key: str | None = None,
    ) -> MergeRecord:
        """Record that ``alias`` and ``into`` are one entity (the class of ``alias`` joins the class of ``into``).

        The decision is a marker report in the evidence log (its report id is the merge id), so it is chained,
        idempotent and reversible. Raises :class:`UnknownEntity` for a name that appears in no report and
        :class:`EntitiesError` when both are already one class."""
        self._need_enabled()
        if not alias or not into or alias == into:
            raise EntitiesError("a merge needs two different entity names")
        known = self.entity_counts()
        for e in (alias, into):
            if e not in known:
                raise UnknownEntity(f"{e!r} appears in no report")
        st = self._state()
        if st.canon(alias) == st.canon(into):
            raise EntitiesError(f"{alias!r} and {into!r} are already the same entity")
        mid, written = self._append(
            alias, encode_marker(MergeOp.MERGE, into=into, reason=reason, method=method, score=score), idempotency_key
        )
        return self._record_of(mid, written)

    def unmerge(self, merge_id: str, *, reason: str, idempotency_key: str | None = None) -> MergeRecord:
        """Undo a merge. The beliefs that consumed evidence across it (and only those) are recomputed."""
        self._need_enabled()
        head = self.mem.lsn_of(None)
        self.layer.sync(head)
        active = {d.id: d for d in self.layer.registry.active_merges(head)}
        if merge_id not in active:
            raise EntitiesError(f"{merge_id} is not an active merge")
        target = active[merge_id]
        did, written = self._append(
            target.alias, encode_marker(MergeOp.UNMERGE, target=merge_id, reason=reason, method="manual"), idempotency_key
        )
        return self._record_of(did, written)

    def _record_of(self, decision_id: str, written: tuple[Key, ...] = ()) -> MergeRecord:
        head = self.mem.lsn_of(None)
        self.layer.sync(head)
        for d in self.layer.registry.history(head):
            if d.id == decision_id:
                st = self.layer.registry.state_at(head)
                return MergeRecord(
                    decision=d, representative=st.canon(d.alias), members=st.members(d.alias), rewritten=written
                )
        raise MergeRejected(
            f"decision {decision_id} was logged but not honoured (not admitted, not from a system/user principal, "
            "or malformed)"
        )

    # -- proposals

    def propose(
        self, *, backend: ResolverBackend | None = None, policy: ResolverPolicy | None = None,
    ) -> list[MergeProposal]:
        """Merge proposals over the entities in the log. Nothing is applied: each is a candidate decision for a host
        (or user) to :meth:`apply` or discard."""
        counts = self.entity_counts()
        st = self._state()
        classes = [frozenset(m) for m in st.classes().values()]
        return propose_merges(
            sorted(counts), backend=backend or self._backend, policy=policy, counts=counts, same_class=classes,
            kind=self.kind,
        )

    def apply(self, proposal: MergeProposal, *, reason: str | None = None, idempotency_key: str | None = None) -> MergeRecord:
        """Apply one proposal as a recorded merge decision (the score, method and resolver version travel with it)."""
        return self.merge(
            proposal.alias, proposal.into,
            reason=reason or f"{proposal.method} {proposal.resolver_version}: {proposal.reason}",
            method=proposal.method, score=proposal.score,
            idempotency_key=idempotency_key or f"merge:{proposal.id}",
        )

    # -- find

    def declare_attr_alias(self, text: str, attr: str) -> None:
        """Teach ``find`` that ``text`` names the declared attribute ``attr`` (schema-layer canonicalisation of attribute
        names: ``works at`` -> ``employer``)."""
        if not any(a.name == attr for a in self.mem.schema.attrs):
            raise EntitiesError(f"{attr!r} is not a declared attribute")
        self._attr_aliases[canonical_form(text, kind="auto")] = attr

    def _attrs_for(self, text: str) -> list[tuple[str, float]]:
        t = canonical_form(text, kind="auto")
        out: list[tuple[str, float]] = []
        for a in self.mem.schema.attrs:
            if a.name == ENTITY_MERGE_ATTR or a.name.startswith("__"):
                continue
            name = canonical_form(a.name.replace("_", " "), kind="auto")
            score = 1.0 if t == name else similarity(t, name, kind="auto").score
            if self._attr_aliases.get(t) == a.name:
                score = 1.0
            out.append((a.name, score))
        return out

    def find(
        self, entity_text: str | None = None, attr_text: str | None = None, *, limit: int = 10,
        threshold: float = 0.5, as_of: BeliefAsOf | None = None,
    ) -> list[KeyCandidate]:
        """Resolve names the caller does not know exactly to candidate keys, through the same canonicalisation the
        resolver uses, and report each key's current kernel status so ambiguity is visible *before* a read.

        Names of one merged class collapse to its representative. ``ambiguous`` is set on every candidate when more
        than one class scored within :data:`AMBIGUITY_MARGIN` of the best. With only ``attr_text`` the result lists
        every entity holding that attribute."""
        if entity_text is None and attr_text is None:
            raise EntitiesError("find needs entity_text, attr_text or both")
        st = self._state(as_of)
        counts = self.entity_counts()
        # entity classes scoring against the text
        classes: dict[str, tuple[float, list[str]]] = {}
        if entity_text is not None:
            for ent in sorted(counts):
                sim = self._backend.score(entity_text, ent)
                if sim.blocked or sim.score < threshold:
                    continue
                rep = st.canon(ent)
                best, names = classes.get(rep, (0.0, []))
                classes[rep] = (max(best, sim.score), [*names, ent])
        else:
            for ent in sorted(counts):
                rep = st.canon(ent)
                best, names = classes.get(rep, (1.0, []))
                classes[rep] = (1.0, [*names, ent])
        attrs: list[tuple[str, float]]
        if attr_text is not None:
            attrs = [(a, s) for a, s in self._attrs_for(attr_text) if s >= threshold]
        else:
            attrs = [(a.name, 1.0) for a in self.mem.schema.attrs if not a.name.startswith("__")]
        top = max((s for s, _ in classes.values()), default=0.0)
        ambiguous = sum(1 for s, _ in classes.values() if s >= top - AMBIGUITY_MARGIN) > 1
        out: list[KeyCandidate] = []
        for rep, (escore, names) in classes.items():
            for attr, ascore in attrs:
                ans = self.mem.query(
                    Query(key=Key(entity=rep, attr=attr), profile=self.mem.semantic.profile, belief_as_of=as_of)
                )
                status = getattr(ans, "kernel_status", None)
                if attr_text is not None and entity_text is None and (status is None or status.value == "unknown"):
                    continue
                out.append(KeyCandidate(
                    entity=rep, attr=attr, matched=tuple(sorted(set(names))), score=round(escore * ascore, 4),
                    kernel_status=status.value if status is not None else None,
                    ambiguous=ambiguous,
                ))
        out.sort(key=lambda c: (-c.score, c.entity, c.attr))
        return out[:limit]


def merge_pairs(ents: Iterable[tuple[str, str]], entities: Entities, *, reason: str) -> list[MergeRecord]:
    """Convenience: merge several (alias, into) pairs with one reason (each is its own decision)."""
    return [entities.merge(a, b, reason=reason) for a, b in ents]


__all__ = [
    "AMBIGUITY_MARGIN", "Entities", "EntitiesError", "EntitiesNotEnabled", "KeyCandidate", "MergeRecord",
    "MergeRejected", "UnknownEntity", "attach_layer", "enable_entity_merges", "merge_attr_spec", "merge_pairs",
]
