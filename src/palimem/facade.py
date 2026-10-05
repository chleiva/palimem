"""The public ``Memory``: three calls cover the common case, the full API sits underneath (docs/AGENT_GUIDE.md).

::

    from palimem import Memory
    m = Memory("agent.db")                                          # SQLite; ":memory:" for a throwaway store
    m.observe({"entity": "alice", "attr": "employer", "value": "Acme"}, source="hr_system")
    a = m.ask("employer", "alice")                                  # an Answer: kernel_status, decision, provenance
    m.withdraw(report_id, actor="user:alice")                       # authority-checked; cascades through justifications

This is the **host** API: whoever calls it names the source, origin and actor, so it must be called by trusted code.
An LLM gets :meth:`Memory.agent_session` instead, which binds those fields itself.

Zero-config profile (T-F7): with no schema, an attribute is declared the first time it is observed as a *multi-valued,
open-world* attribute (a stable set: members do not compete and are never replaced). That is the safest class:
absence is ``unknown``, never ``established``. The design's "no inertia" cannot be expressed today: the kernel refuses
``inertia=False`` because S-08 leaves its semantics undefined, so ``inertia`` is ``True`` (harmless for a stable set).
Declare a schema (``Memory(path, schema=...)`` or :meth:`Memory.declare`) for single-valued or changeable attributes.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Self, cast

from palimem._reopen import stored_inputs
from palimem.admission import AdmissionConfig
from palimem.agent import (
    AgentTools,
    AuditLog,
    ExtractorRequired,
    Host,
    HostAppend,
    KeyRef,
    SessionContext,
)
from palimem.extract import ExtractionContext, Extractor
from palimem.memory import Memory as CoreMemory
from palimem.policy import PRESETS, PolicyObject
from palimem.store import Backend, ErasureReason, SQLiteBackend, Tombstone, VerifyResult
from palimem.types import (
    Answer,
    Attr,
    AttrClass,
    BeliefAsOf,
    Completeness,
    Cue,
    ExplainMode,
    ExplainQuery,
    Explanation,
    Key,
    Origin,
    Profile,
    Query,
    Report,
    Schema,
    SemanticConfig,
    Source,
    ValidationError,
    ValueType,
    check_proposition_for_attr,
)
from palimem.types.authority import check_principal
from palimem.types.values import MemberProp, ValueProp

STORE_SECRET_ENV = "PALIMEM_STORE_SECRET"


@dataclass(frozen=True)
class Observed:
    """What :meth:`Memory.observe` appended."""

    report_ids: tuple[str, ...]
    admitted: tuple[bool, ...]
    rejections: tuple[str, ...] = ()

    @property
    def report_id(self) -> str | None:
        """The id of the single appended report (``None`` when nothing was appended, the first when several)."""
        return self.report_ids[0] if self.report_ids else None


def _as_source(source: str | Source) -> Source:
    return source if isinstance(source, Source) else Source(id=source, cls="standard")


def _as_origin(origin: str | Origin) -> Origin:
    return origin if isinstance(origin, Origin) else Origin(origin)


class Memory:
    """A justified memory in one object. See the module docstring."""

    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        schema: Schema | None = None,
        entities: Sequence[str] | None = None,
        extractor: Extractor | None = None,
        self_update: bool = False,
        profile: Profile = Profile.OPEN_WORLD,
        policy: str | PolicyObject = "justified",
        store_secret: bytes | None = None,
        audit_path: str | Path | None = None,
        clock: Callable[[], datetime] | None = None,
        backend: Backend | None = None,
    ) -> None:
        if store_secret is None and os.environ.get(STORE_SECRET_ENV):
            store_secret = os.environ[STORE_SECRET_ENV].encode("utf-8")
        be: Backend
        if backend is not None:
            be = backend
        else:
            # the Backend protocol declares `capabilities` as a plain attribute; Engine exposes it read-only
            be = cast(Backend, SQLiteBackend(path, store_secret=store_secret, clock=clock))
        self.backend = be
        stored_schema, stored_sem, stored_adm, stored_pol = stored_inputs(self.backend)
        self._zero_config = schema is None
        use_schema = schema if schema is not None else (stored_schema or Schema(version=1, attrs=()))
        semantic = stored_sem if stored_sem is not None else SemanticConfig(self_update=self_update, profile=profile)
        if stored_sem is not None and (stored_sem.self_update, stored_sem.profile) != (self_update, profile):
            semantic = SemanticConfig(self_update=self_update, profile=profile)
        admission = stored_adm if stored_adm is not None else AdmissionConfig(profile=semantic.profile)
        want = PRESETS[policy] if isinstance(policy, str) else policy
        if stored_pol is not None and stored_pol.name == want.name and stored_pol.priors == want.priors:
            want = stored_pol  # reopening under the same policy adopts the stored version
        elif stored_pol is not None:
            want = replace(want, version=max(want.version, stored_pol.version + 1))
        self._entities = tuple(entities) if entities is not None else None
        self._extractor = extractor
        self._core = self._build_core(use_schema, semantic, admission, want)
        audit = AuditLog(
            audit_path if audit_path is not None else (None if str(path) == ":memory:" else f"{path}.audit.jsonl")
        )
        self.host = Host(self._core, audit=audit, extractor=extractor, on_undeclared=self._declare_default)

    # ------------------------------------------------------------------ construction helpers

    def _build_core(
        self, schema: Schema, semantic: SemanticConfig, admission: AdmissionConfig, policy: PolicyObject
    ) -> CoreMemory:
        return CoreMemory(
            self.backend, schema, semantic=semantic, admission=admission, policy=policy, entities=self._entities,
        )

    def _rebuild(self, schema: Schema) -> None:
        core = self._build_core(schema, self._core.semantic, self._core.admission, self._core.policy)
        self._core = core
        self.host.rebind(core)

    def _declare_default(self, name: str) -> None:
        """Zero-config: declare an unseen attribute as multi-valued, open-world, no inertia."""
        if self._zero_config:
            self.declare(name, AttrClass.MULTI_SET)

    def declare(
        self, name: str, attr_class: AttrClass | str = AttrClass.SINGLE_CHANGEABLE, *, inertia: bool | None = None,
        value_type: ValueType = ValueType.STRING, completeness: Completeness | None = None,
    ) -> None:
        """Declare an attribute (a new schema version). ``single_changeable`` is the usual class for 'employer'."""
        cls = attr_class if isinstance(attr_class, AttrClass) else AttrClass(attr_class)
        if cls is AttrClass.DERIVED:
            raise ValidationError("declare(): a derived attribute needs a rule; build a Schema with palimem.types.Attr")
        if any(a.name == name for a in self._core.schema.attrs):
            raise ValidationError(f"attribute {name!r} is already declared")
        a = Attr(
            name=name, attr_class=cls, value_type=value_type,
            inertia=True if inertia is None else inertia,  # the kernel refuses inertia=False (S-08 leaves it undefined)
            completeness=completeness or Completeness(),
        )
        s = self._core.schema
        self._rebuild(Schema(version=s.version + 1, attrs=s.attrs + (a,)))

    @property
    def schema(self) -> Schema:
        return self._core.schema

    @property
    def core(self) -> CoreMemory:
        """The host-level core (escape hatch for full control; never hand it to an LLM)."""
        return self._core

    # ------------------------------------------------------------------ the three calls

    def observe(
        self,
        evidence: str | Report | Mapping[str, Any],
        *,
        source: str | Source,
        origin: str | Origin = Origin.EXTERNAL_OBSERVATION,
        actor: str | None = None,
        origin_group: str | None = None,
        cue: Cue = Cue.ASSERT,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> Observed:
        """Append evidence: a typed ``Report``, a typed claim ``{"entity", "attr", "value"}``, or plain text.

        Plain text needs an extractor (``Memory(..., extractor=...)``): palimem never guesses a key from prose.
        ``source`` is required: the memory is only as good as the provenance you name."""
        src = _as_source(source)
        org = _as_origin(origin)
        who = actor or f"connector:{src.id}"
        check_principal(who, "actor")
        group = origin_group or src.id
        if isinstance(evidence, Report):
            ha = self.host.append(evidence, idempotency_key=idempotency_key or _fresh())
            return self._observed([ha])
        if isinstance(evidence, Mapping):
            rep = self._claim_to_report(evidence, src, org, who, group, cue, valid_from, valid_to)
            ha = self.host.append(rep, idempotency_key=idempotency_key or _fresh())
            return self._observed([ha])
        if self._extractor is None:
            raise ExtractorRequired(
                "observe(text) needs an extractor: pass Memory(..., extractor=...), or give a typed claim "
                "{'entity': ..., 'attr': ..., 'value': ...} or a Report"
            )
        ctx = ExtractionContext(
            source=src, origin_group=group, actor=who, origin=org, schema=self.schema,
        )
        res = self._extractor.extract(evidence, ctx)
        out = [
            self.host.append(r, idempotency_key=(f"{idempotency_key}:{i}" if idempotency_key else _fresh()))
            for i, r in enumerate(res.reports)
        ]
        return self._observed(out, tuple(f"{r.reason}: {r.detail}" for r in res.rejections))

    def _claim_to_report(
        self, claim: Mapping[str, Any], src: Source, org: Origin, who: str, group: str, cue: Cue,
        valid_from: datetime | None, valid_to: datetime | None,
    ) -> Report:
        extra = set(claim) - {"entity", "attr", "value"}
        if extra or not {"entity", "attr", "value"} <= set(claim):
            raise ValidationError("a typed claim is exactly {'entity', 'attr', 'value'}")
        attr = str(claim["attr"])
        if not self.host.ensure_declared(attr):
            raise ValidationError(f"attribute {attr!r} is not declared; call declare() or pass schema=")
        a = self.schema.attr(attr)
        prop = MemberProp(value=claim["value"]) if a.attr_class is AttrClass.MULTI_SET else ValueProp(value=claim["value"])
        check_proposition_for_attr(a, prop)
        return Report(
            key=Key(entity=str(claim["entity"]), attr=attr), cue=cue, proposition=prop, source=src, origin=org,
            origin_group=group, actor=who, valid_from=valid_from, valid_to=valid_to,
        )

    @staticmethod
    def _observed(appends: Sequence[HostAppend], rejections: tuple[str, ...] = ()) -> Observed:
        return Observed(
            report_ids=tuple(a.report_id for a in appends if a.report_id is not None),
            admitted=tuple(a.admitted for a in appends), rejections=rejections,
        )

    def ask(
        self, attr: str, entity: str, *, valid_at: datetime | None = None, belief_as_of: BeliefAsOf | None = None,
        profile: Profile | None = None, policy: str | None = None,
    ) -> Answer:
        """What the evidence justifies about ``entity``'s ``attr``: an output-contract-v2 ``Answer``.

        ``kernel_status`` (what the evidence warrants) and ``decision`` (commit | abstain | ask) are separate fields."""
        q = Query(
            key=Key(entity=entity, attr=attr), valid_at=valid_at, belief_as_of=belief_as_of,
            profile=profile or self._core.semantic.profile,
        )
        return self.host.query(q, policy=policy)

    def withdraw(
        self, report_id: str, *, actor: str, source: str | Source | None = None, idempotency_key: str | None = None,
    ) -> HostAppend:
        """Withdraw a report. Authority is checked by admission: by default only the report's own source may; without
        ``source`` the withdrawal is made as the target's own source. A failed check is logged as an ``allege`` and
        changes nothing. Cascades through every conclusion that depended on the report."""
        return self.host.withdraw(
            report_id, actor=actor, source=None if source is None else _as_source(source),
            idempotency_key=idempotency_key,
        )

    # ------------------------------------------------------------------ the layer underneath

    def explain(
        self, attr: str, entity: str, *, valid_at: datetime | None = None, belief_as_of: BeliefAsOf | None = None,
        mode: ExplainMode | str = ExplainMode.ALL, depth: int | None = None,
    ) -> Explanation:
        return self.host.explain(ExplainQuery(
            key=Key(entity=entity, attr=attr), valid_at=valid_at, belief_as_of=belief_as_of,
            mode=mode if isinstance(mode, ExplainMode) else ExplainMode(mode), depth=depth,
        ))

    def find(self, text: str, *, limit: int = 20) -> list[KeyRef]:
        """Candidate keys for a name you do not know exactly (lexical; entity resolution proper is T-G3)."""
        return self.host.find(text, limit=limit)

    def subscribe(self, plan_id: str, keys: Sequence[tuple[str, str]]) -> None:
        """Register the beliefs a plan rests on, as ``(attr, entity)`` pairs; changes arrive through the outbox."""
        self.host.subscribe(plan_id, [Key(entity=e, attr=a) for a, e in keys])

    def delete(self, report_id: str, reason: ErasureReason = ErasureReason.ERASURE_REQUEST) -> Tombstone:
        """Erase a report (needs ``store_secret``): content and derived values go, a tombstone keeps the chain."""
        return self.host.mem.delete(report_id, reason)

    def verify(self) -> VerifyResult:
        """Check the log's hash chain."""
        return self.host.verify_log()

    def agent_session(
        self, agent_principal: str, *, session_id: str | None = None, **limits: Any
    ) -> AgentTools:
        """The tools an LLM may call, bound to ``agent_principal`` (the host fixes source, origin and actor)."""
        ctx = SessionContext(session_id=session_id or _fresh(), agent_principal=agent_principal, **limits)
        tools = self.host.bind_session(ctx)
        assert isinstance(tools, AgentTools)
        return tools

    def close(self) -> None:
        self._core.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _fresh() -> str:
    import uuid

    return uuid.uuid4().hex


__all__ = ["STORE_SECRET_ENV", "Memory", "Observed"]
