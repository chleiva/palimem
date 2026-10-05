"""The host side of the trust boundary (docs/API_TRUST_BOUNDARY.md §3.1): the only code that may bind identity.

``Host`` wraps the host-level :class:`palimem.memory.Memory` core with what a deployment adds around it:

* a **connector registry** (source id, source class and origin group are properties of a registered
  connector, never of an event's text or of a tool call),
* a **host-held event ledger** and :meth:`Host.ingest_event`, which runs an extractor over an event and binds
  every identity field from the connector (the extractor only proposes content),
* **authority** management (:meth:`Host.set_authority`, :meth:`Host.set_source_class`), both versioned
  admission inputs,
* an **audit log** of every downgrade, denial and refusal,
* :meth:`Host.bind_session`, the only way to obtain the agent tool tier.

It must never be handed to an LLM. The agent-facing tools are :class:`palimem.agent.tools.AgentTools`.
"""

from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Literal

from palimem.admission import Authorizer, SourceStatus
from palimem.extract import ExtractionContext, Extractor
from palimem.extract.claims import TargetHint
from palimem.kernel.derive import MAX_DEPTH
from palimem.memory import Memory as CoreMemory
from palimem.policy import ABSTAIN, JUSTIFIED, PRESETS, PolicyObject
from palimem.store import AppendResult, Tombstone, VerifyResult
from palimem.types import (
    Answer,
    AuthorityRule,
    Cue,
    ExplainQuery,
    Explanation,
    Key,
    LogEntry,
    Origin,
    Power,
    PrincipalKind,
    Query,
    Report,
    Source,
    ValidationError,
    check_proposition_for_attr,
)
from palimem.types.authority import check_principal, principal_kind
from palimem.types.enums import AttrClass
from palimem.types.values import (
    EnumerationProp,
    MemberProp,
    NotMemberProp,
    NotValueProp,
    ValueProp,
)

from .audit import AuditLog
from .proposals import Proposal, ProposalError, ProposalQueue

#: Ruling 16 (2026-10-05): the HOST default abstains on an unresolved key (and always says what would settle it) ...
DEFAULT_POLICY_LABEL = "p-default"
#: ... while an AGENT session defaults to asking: abstaining would throw away the verification action an agent can take.
DEFAULT_AGENT_POLICY_LABEL = "p-ask"
MAX_EXPLAIN_DEPTH = MAX_DEPTH
#: Fields that exist on the log row, not on a Report: never caller-supplied, anywhere (R1, S-05, hash chain).
LOG_ROW_FIELDS = ("id", "recorded_at", "lsn", "prev_hash", "entry_hash", "salt", "admission")
ConnectorKind = Literal["user_message", "tool_result", "document", "feed", "system"]


class HostError(Exception):
    """A refusal by the host with a stable ``code`` (the code is part of the tool contract)."""

    code = "host_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class InvalidAuthorityRule(HostError):
    code = "invalid_authority_rule"


class ExtractorRequired(HostError):
    code = "extractor_required"


@dataclass(frozen=True)
class Notice:
    """A machine-readable remark attached to a tool result (``field_ignored``, ``scope_denied``, ...)."""

    code: str
    field: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code}
        if self.field is not None:
            out["field"] = self.field
        if self.detail is not None:
            out["detail"] = self.detail
        return out


@dataclass(frozen=True, kw_only=True)
class ConnectorSpec:
    """A registered connector: the only source of ``source``, ``source_class``, ``origin_group`` and a default actor."""

    connector_id: str
    source_id: str
    source_class: str
    origin_group: str
    kind: ConnectorKind = "feed"
    principal: str | None = None
    auto_ingest: bool = False

    def __post_init__(self) -> None:
        for name in ("connector_id", "source_id", "source_class", "origin_group"):
            if not getattr(self, name).strip():
                raise ValidationError(f"connector.{name}: expected a non-empty string")
        if self.principal is not None:
            check_principal(self.principal, "connector.principal")

    @property
    def default_principal(self) -> str:
        if self.principal is not None:
            return self.principal
        name = self.connector_id.split(":", 1)[1] if ":" in self.connector_id else self.connector_id
        return f"connector:{name}"


@dataclass(frozen=True, kw_only=True)
class ConnectorEvent:
    """An event held by the host. ``source``, class and origin group are *not* fields: they come from the connector."""

    event_id: str
    connector_id: str
    raw: str
    received_at: datetime | None = None
    principal: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)
    session_id: str | None = None  # when set, only that session may cite the event

    def __post_init__(self) -> None:
        if not self.event_id.strip() or not self.connector_id.strip():
            raise ValidationError("event: event_id and connector_id are required")
        if self.principal is not None:
            check_principal(self.principal, "event.principal")


@dataclass(frozen=True, kw_only=True)
class SessionContext:
    """What the host binds for one agent session. Nothing in it is supplied by the LLM."""

    session_id: str
    agent_principal: str
    end_user_principal: str | None = None
    allowed_attrs: tuple[str, ...] | None = None
    policy_version: str = DEFAULT_AGENT_POLICY_LABEL  # a label in the host's policy registry (agent default: ask)
    max_explanation_budget: int = 50
    max_depth: int = MAX_EXPLAIN_DEPTH
    max_alternatives: int = 5
    max_text_length: int = 4000
    max_writes: int | None = None  # per bound session object; None = unlimited
    # Ruling 17 (2026-10-05): an agent may never declare an attribute. An unknown attribute is queued as a proposal for the
    # host, unless the host pre-approved it (`allowed_attrs` lists it) or opted this session into `auto_declare`. Only the
    # host sets these: nothing in a tool call can.
    auto_declare: bool = False
    max_proposals: int = 10  # pending proposals a session may hold

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValidationError("session.session_id: expected a non-empty string")
        check_principal(self.agent_principal, "session.agent_principal")
        if principal_kind(self.agent_principal) is not PrincipalKind.AGENT:
            raise ValidationError("session.agent_principal: must be a principal of kind 'agent'")
        if self.end_user_principal is not None:
            check_principal(self.end_user_principal, "session.end_user_principal")
        if self.max_explanation_budget < 1 or self.max_depth < 1 or self.max_alternatives < 1 or self.max_text_length < 1:
            raise ValidationError("session: limits must be positive")

    def in_scope(self, attr: str) -> bool:
        return self.allowed_attrs is None or attr in self.allowed_attrs


@dataclass(frozen=True)
class HostAppend:
    report_id: str | None
    lsn: int | None
    replayed: bool
    admitted: bool
    outcome: str | None
    reason: str | None
    notices: tuple[Notice, ...] = ()
    result: AppendResult | None = field(default=None, compare=False, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id, "lsn": self.lsn, "replayed": self.replayed, "admitted": self.admitted,
            "outcome": self.outcome, "reason": self.reason, "notices": [n.to_dict() for n in self.notices],
        }


@dataclass(frozen=True)
class IngestResult:
    report_ids: tuple[str, ...]
    notices: tuple[Notice, ...] = ()
    rejections: tuple[str, ...] = ()
    admitted: tuple[bool, ...] = ()


@dataclass(frozen=True)
class ReportRow:
    id: str
    lsn: int
    key: Key
    cue: str
    effective_cue: str
    origin: str
    source_id: str
    source_class: str
    actor: str
    origin_group: str
    target: str | None
    outcome: str
    reason: str
    withdrawn: bool
    recorded_at: datetime
    raw_ref: str | None
    prev_hash: str | None
    entry_hash: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "lsn": self.lsn, "key": [self.key.entity, self.key.attr], "cue": self.cue,
            "effective_cue": self.effective_cue, "origin": self.origin,
            "source": {"id": self.source_id, "class": self.source_class}, "actor": self.actor,
            "origin_group": self.origin_group, "target": self.target, "admission": self.outcome,
            "reason": self.reason, "withdrawn": self.withdrawn, "recorded_at": self.recorded_at.isoformat(),
            "raw_ref": self.raw_ref, "prev_hash": self.prev_hash, "entry_hash": self.entry_hash,
        }


@dataclass(frozen=True)
class KeyRef:
    entity: str
    attr: str
    kernel_status: str | None = None
    score: int = 0  # lexical match strength; only the ordering and ties matter

    def to_dict(self) -> dict[str, Any]:
        return {"entity": self.entity, "attr": self.attr, "kernel_status": self.kernel_status}


_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(s: str) -> set[str]:
    return set(_TOKEN.findall(s.casefold().replace("_", " ")))


def _value_of(p: Any) -> Any:
    if isinstance(p, ValueProp | MemberProp | NotMemberProp | NotValueProp):
        return p.value
    if isinstance(p, EnumerationProp):
        return list(p.values)
    return None


class Host:
    """Host-level API (T0 only). See the module docstring and docs/API_TRUST_BOUNDARY.md."""

    def __init__(
        self, memory: CoreMemory, *, audit: AuditLog | None = None, extractor: Extractor | None = None,
        policies: Mapping[str, PolicyObject] | None = None, on_undeclared: Callable[[str], None] | None = None,
        proposals: ProposalQueue | None = None, declare_attr: Callable[[str, AttrClass | None], None] | None = None,
    ) -> None:
        self.mem = memory
        self.on_undeclared = on_undeclared
        self.declare_attr = declare_attr  # the host's own declaration hook (never reachable from a tool call)
        self.proposals = proposals if proposals is not None else ProposalQueue()
        self.audit = audit if audit is not None else AuditLog()
        self.extractor = extractor
        self._policies: dict[str, PolicyObject] = {DEFAULT_POLICY_LABEL: ABSTAIN, DEFAULT_AGENT_POLICY_LABEL: JUSTIFIED, **PRESETS}
        if policies:
            self._policies.update(policies)
        self._connectors: dict[str, ConnectorSpec] = {}
        self._events: dict[str, ConnectorEvent] = {}
        self._lock = threading.RLock()

    def rebind(self, memory: CoreMemory) -> None:
        """Point the host at a rebuilt core (the facade does this after it extends a zero-config schema)."""
        self.mem = memory

    def ensure_declared(self, attr: str) -> bool:
        """True when ``attr`` is declared, asking the ``on_undeclared`` hook (zero-config profile) to declare it first."""
        if any(a.name == attr for a in self.mem.schema.attrs):
            return True
        if self.on_undeclared is not None:
            self.on_undeclared(attr)
        return any(a.name == attr for a in self.mem.schema.attrs)

    # ------------------------------------------------------------------ attribute declaration and proposals (ruling 17)

    def is_declared(self, attr: str) -> bool:
        return any(a.name == attr for a in self.mem.schema.attrs)

    def declare_for_session(
        self, attr: str, *, reason: Literal["allowed_attrs", "auto_declare", "proposal"], session_id: str,
        attr_class: AttrClass | None = None,
    ) -> bool:
        """The HOST declares ``attr`` on behalf of a session it has approved. True when ``attr`` is declared afterwards."""
        if self.is_declared(attr):
            return True
        if self.declare_attr is None:
            return False
        self.declare_attr(attr, attr_class)
        if not self.is_declared(attr):
            return False
        self.audit.append("attr_declared", session=session_id, attr=attr, reason=reason)
        return True

    def propose_attr(
        self, ctx: SessionContext, *, entity: str, attr: str, value: Any, kind: Literal["statement", "hypothesis"],
        request_id: str | None,
    ) -> tuple[Proposal, bool]:
        """Queue an agent's proposal of an unknown attribute (see :mod:`palimem.agent.proposals`)."""
        prop, created = self.proposals.propose(
            session_id=ctx.session_id, agent_principal=ctx.agent_principal, entity=entity, attr=attr, value=value,
            kind=kind, request_id=request_id, max_pending=ctx.max_proposals,
        )
        if created:
            self.audit.append("attr_proposed", session=ctx.session_id, attr=attr, proposal=prop.id)
        return prop, created

    @staticmethod
    def _require_owner(actor: str) -> None:
        check_principal(actor, "actor")
        if principal_kind(actor) not in (PrincipalKind.USER, PrincipalKind.SYSTEM):
            raise HostError(
                f"{actor!r}: only a user or system principal may decide an attribute proposal", code="not_authorised"
            )

    def accept_proposal(
        self, proposal_id: str, *, actor: str, attr_class: AttrClass | str | None = None, apply: bool = True,
    ) -> Proposal:
        """Host-only: declare the proposed attribute and (by default) record the queued fact as the agent's own report."""
        self._require_owner(actor)
        prop = self.proposals.get(proposal_id)
        if prop.status != "pending":
            raise ProposalError(f"proposal {proposal_id!r} is already {prop.status}", code="proposal_decided")
        cls = None if attr_class is None else (attr_class if isinstance(attr_class, AttrClass) else AttrClass(attr_class))
        if cls is AttrClass.DERIVED:
            raise HostError("a derived attribute needs a rule: declare it in the schema, not by proposal", code="derived_attr")
        if not self.declare_for_session(prop.attr, reason="proposal", session_id=prop.session_id, attr_class=cls):
            raise HostError(
                f"cannot declare {prop.attr!r}: this host has no declaration hook; declare it in the schema",
                code="declare_unavailable",
            )
        ids: tuple[str, ...] = ()
        if apply:
            a = self.mem.schema.attr(prop.attr)
            if a.attr_class is AttrClass.DERIVED:
                raise HostError(f"attribute {prop.attr!r} is derived and cannot be remembered directly", code="derived_attr")
            proposition = MemberProp(value=prop.value) if a.attr_class is AttrClass.MULTI_SET else ValueProp(value=prop.value)
            check_proposition_for_attr(a, proposition)
            origin = Origin.AGENT_HYPOTHESIS if prop.kind == "hypothesis" else Origin.AGENT_STATEMENT
            me = prop.agent_principal
            rep = Report(
                key=Key(entity=prop.entity, attr=prop.attr), cue=Cue.ASSERT, proposition=proposition,
                source=Source(id=me, cls="agent"), origin=origin, origin_group=me, actor=me,
            )
            ha = self.append(rep, idempotency_key=f"proposal:{prop.id}")
            ids = (ha.report_id,) if ha.report_id is not None else ()
        decided = self.proposals.decide(
            proposal_id, accepted=True, actor=actor, attr_class=None if cls is None else cls.value, applied_report_ids=ids
        )
        self.audit.append("attr_accepted", session=prop.session_id, proposal=proposal_id, actor=actor, attr=prop.attr,
                          applied=len(ids))
        return decided

    def reject_proposal(self, proposal_id: str, *, actor: str, reason: str | None = None) -> Proposal:
        """Host-only: drop a proposal (nothing is declared or recorded; the queued value is redacted)."""
        self._require_owner(actor)
        prop = self.proposals.get(proposal_id)
        decided = self.proposals.decide(proposal_id, accepted=False, actor=actor, reason=reason)
        self.audit.append("attr_rejected", session=prop.session_id, proposal=proposal_id, actor=actor, attr=prop.attr)
        return decided

    # ------------------------------------------------------------------ registries

    def register_connector(self, spec: ConnectorSpec) -> None:
        with self._lock:
            self._connectors[spec.connector_id] = spec

    def connector(self, connector_id: str) -> ConnectorSpec:
        try:
            return self._connectors[connector_id]
        except KeyError:
            raise HostError(f"unknown connector {connector_id!r}", code="connector_unknown") from None

    def add_event(self, event: ConnectorEvent) -> None:
        """Put an event in the host-held ledger. Citing it from a session is the only way an LLM can point at evidence."""
        self.connector(event.connector_id)
        with self._lock:
            self._events[event.event_id] = event

    def event(self, event_id: str, session_id: str | None = None) -> ConnectorEvent | None:
        ev = self._events.get(event_id)
        if ev is None:
            return None
        if ev.session_id is not None and ev.session_id != session_id:
            return None  # a foreign event looks exactly like a missing one (R5, R7)
        return ev

    def register_policy(self, label: str, policy: PolicyObject) -> None:
        self._policies[label] = policy

    def policy_for(self, label: str) -> PolicyObject:
        try:
            return self._policies[label]
        except KeyError:
            raise HostError(f"unknown policy {label!r}", code="policy_unknown") from None

    # ------------------------------------------------------------------ write path (host tier)

    def append(self, report: Report | Mapping[str, Any], *, idempotency_key: str, complete: bool = True) -> HostAppend:
        """Append one report as given (the host is trusted). A mapping is decoded strictly; the log-row fields
        ``prev_hash``, ``entry_hash``, ``lsn``, ``recorded_at``, ``id``... are stripped with a notice, because the log
        assigns them (R1, fixture tb-20)."""
        notices: list[Notice] = []
        if isinstance(report, Mapping):
            d = dict(report)
            for f in LOG_ROW_FIELDS:
                if f in d:
                    d.pop(f)
                    notices.append(Notice("field_ignored", f))
            if notices:
                self.audit.append("trust_downgrade", tool="append", fields=[n.field or "" for n in notices])
            rep = Report.from_dict(d)
        else:
            rep = report
        res = self.mem.append(rep, idempotency_key=idempotency_key, complete=complete)
        return self._host_append(res, tuple(notices))

    def _host_append(self, res: AppendResult, notices: tuple[Notice, ...] = ()) -> HostAppend:
        e = res.entry
        outcome = reason = None
        admitted = False
        if e is not None and e.report.id is not None:
            for rec in res.admissions:
                if rec.report_id == e.report.id:
                    outcome, reason = rec.outcome.value, rec.reason.value
                    admitted = rec.outcome.value == "admissible"
                    break
        return HostAppend(
            report_id=e.report.id if e is not None else None, lsn=e.lsn if e is not None else None,
            replayed=res.replayed, admitted=admitted, outcome=outcome, reason=reason, notices=notices, result=res,
        )

    def withdraw(
        self, report_id: str, *, actor: str, source: Source | None = None, origin_group: str | None = None,
        idempotency_key: str | None = None,
    ) -> HostAppend:
        """Host-tier withdrawal by a human or system principal (R6 invariant c: not via the agent tools). Without
        ``source`` the withdrawal is made *as the target's own source*, the common 'a source retracts its own report'."""
        check_principal(actor, "actor")
        t = self.mem.backend.get_entry(report_id)
        if t is None or isinstance(t, Tombstone):
            raise LookupError(f"unknown or erased report {report_id}")
        src = source if source is not None else t.report.source
        res = self.mem.withdraw(
            report_id, source=src, actor=actor, origin_group=origin_group or t.report.origin_group,
            idempotency_key=idempotency_key,
        )
        return self._host_append(res)

    def delete(self, report_id: str, *, requester: str | None = None) -> Tombstone:
        return self.mem.delete(report_id, requester=requester)

    # ------------------------------------------------------------------ events and extraction

    def ingest_event(
        self, event_id: str, *, extractor: Extractor | None = None, idempotency_key: str | None = None,
        session_id: str | None = None, allowed_attrs: Sequence[str] | None = None,
    ) -> IngestResult:
        """Extract an event held in the ledger and append the resulting reports.

        Identity comes from the registered connector; the extractor only proposes content. Authority-bearing cues
        the extractor *requests* (``withdraw``, ``dispute``) are checked against the connector's principal and land as
        ``allege`` when it has no authority (docs/API_TRUST_BOUNDARY.md §7)."""
        ev = self.event(event_id, session_id)
        if ev is None:
            raise HostError(f"unknown event {event_id!r}", code="event_unknown")
        spec = self.connector(ev.connector_id)
        ex = extractor or self.extractor
        if ex is None:
            raise ExtractorRequired("no extractor is configured on the host: pass one to ingest_event or Host(...)")
        actor = ev.principal or spec.default_principal
        subject = actor.split(":", 1)[1] if principal_kind(actor) is PrincipalKind.USER else None
        ctx = ExtractionContext(
            source=Source(id=spec.source_id, cls=spec.source_class), origin_group=spec.origin_group, actor=actor,
            origin=Origin.EXTERNAL_OBSERVATION, observed_at=ev.received_at, raw_ref=ev.event_id,
            subject_entity=subject, schema=self.mem.schema,
            allowed_cues=frozenset({Cue.ASSERT, Cue.CHANGE, Cue.CORRECT, Cue.WITHDRAW, Cue.DISPUTE}),
            resolve_target=self._resolve_hint,
        )
        result = ex.extract(ev.raw, ctx)
        notices: list[Notice] = []
        if result.identity_fields_seen:
            self.audit.append(
                "trust_downgrade", tool="extractor", session=session_id,
                fields=list(_identity_fields_seen(result)), event_id=ev.event_id,
            )
            notices.extend(Notice("field_ignored", f) for f in _identity_fields_seen(result))
        ids: list[str] = []
        admitted: list[bool] = []
        skipped: list[str] = []
        for i, rep in enumerate(result.reports):
            if rep.key.attr.startswith("__"):  # reserved attributes are host-only: an extractor must not reach them
                skipped.append(f"scope_denied: attribute {rep.key.attr!r} is reserved")
                continue
            if allowed_attrs is not None and rep.key.attr not in allowed_attrs:
                skipped.append(f"scope_denied: attribute {rep.key.attr!r} is outside the session's scope")
                continue
            rep2 = self._authorise_request(rep)
            key = f"{idempotency_key or 'evt:' + ev.event_id}:{i}"
            ha = self.append(rep2, idempotency_key=key)
            if ha.report_id is not None:
                ids.append(ha.report_id)
            admitted.append(ha.admitted)
        return IngestResult(
            report_ids=tuple(ids), notices=tuple(notices),
            rejections=tuple(f"{r.reason}: {r.detail}" for r in result.rejections) + tuple(skipped),
            admitted=tuple(admitted),
        )

    def _resolve_hint(self, hint: TargetHint) -> str | None:
        """The newest report matching the hint (entity, optional attribute, optional value), or None."""
        entries = [e for e in self.mem.backend.scan() if isinstance(e, LogEntry)]
        for e in reversed(entries):
            r = e.report
            if r.cue not in (Cue.ASSERT, Cue.CHANGE, Cue.CORRECT) or r.key.entity != hint.entity:
                continue
            if hint.attr is not None and r.key.attr != hint.attr:
                continue
            if hint.value is not None and _value_of(r.proposition) != hint.value:
                continue
            return r.id
        return None

    def authorise(self, report: Report, power: Power) -> bool:
        """Would the admission stage let ``report.actor`` exercise ``power`` on ``report.target``?"""
        if report.target is None:
            return False
        t = self.mem.backend.get_entry(report.target)
        if t is None or isinstance(t, Tombstone):
            return False
        return Authorizer(self.mem.admission, self.mem.schema).check(report, power, t.report).allowed

    def _authorise_request(self, rep: Report) -> Report:
        """A withdraw or dispute that fails the authority check is written as the ``allege`` it will be recorded as."""
        power = {Cue.WITHDRAW: Power.WITHDRAW, Cue.DISPUTE: Power.DISPUTE}.get(rep.cue)
        if power is None or self.authorise(rep, power):
            return rep
        return replace(rep, cue=Cue.ALLEGE, proposition=None)

    # ------------------------------------------------------------------ authority and source classes

    def set_authority(self, rules: Sequence[AuthorityRule | Mapping[str, Any]]) -> int:
        """Replace the global grant table. A grant that would hand an agent withdraw/correct over external
        evidence is refused (S-07, R6 invariant a): nothing changes, the refusal is audited."""
        try:
            parsed = tuple(r if isinstance(r, AuthorityRule) else AuthorityRule.from_dict(r) for r in rules)
            cfg = self.mem.admission.successor(rules=parsed)
        except (ValidationError, ValueError, TypeError) as e:
            self.audit.append("authority_rule_refused", tool="set_authority", detail_text=str(e)[:200])
            raise InvalidAuthorityRule(str(e)) from e
        self.mem.set_admission(cfg)  # a versioned input: the store's input history is the record of the change
        return cfg.admission_version

    def set_source_class(self, source_id: str, status: SourceStatus | str, *, reason: str) -> int:
        """Override one source's admission status (``normal`` | ``quarantined`` | ``blocked``); a new admission version."""
        st = status if isinstance(status, SourceStatus) else SourceStatus(status)
        cfg0 = self.mem.admission
        cfg = cfg0.successor(source_status={**dict(cfg0.source_status), source_id: st})
        self.mem.set_admission(cfg)  # a versioned input; ``reason`` is accepted for the caller's own change log
        del reason
        return cfg.admission_version

    def source_class(self, source_id: str) -> dict[str, Any]:
        cls = None
        for e in self.mem.backend.scan():
            if isinstance(e, LogEntry) and e.report.source.id == source_id:
                cls = e.report.source.cls
        cls = cls or "unknown"
        status = self.mem.admission.status_of(Source(id=source_id, cls=cls)) if cls != "unknown" else SourceStatus.NORMAL
        return {"source_id": source_id, "class": cls, "status": status.value, "quarantined": status is SourceStatus.QUARANTINED}

    # ------------------------------------------------------------------ read side

    def _read_mem(self, policy_label: str) -> CoreMemory:
        return self.mem.with_policy(self.policy_for(policy_label))

    def query(self, q: Query, *, policy: str | None = None) -> Answer:
        return (self._read_mem(policy) if policy else self.mem).query(q)

    def explain(self, q: ExplainQuery) -> Explanation:
        return self.mem.explain(q)

    def find(self, text: str, *, limit: int = 20) -> list[KeyRef]:
        """Resolve a name the caller does not know exactly to candidate keys with their current kernel status.

        Lexical only (token overlap on entity ids and attribute names); entity resolution proper is task T-G3."""
        want = _tokens(text)
        if not want:
            return []
        entities = {e.report.key.entity for e in self.mem.backend.scan() if isinstance(e, LogEntry)}
        attrs = [a.name for a in self.mem.schema.attrs]
        named = [a for a in attrs if want & _tokens(a)]
        if named:  # the text names an attribute: only those attributes are candidates
            attrs = named
        scored: list[tuple[int, str, str]] = []
        for ent in sorted(entities):
            et = _tokens(ent)
            for attr in attrs:
                hit = len(want & et) * 2 + len(want & _tokens(attr))
                if hit and (want & et or not et or named):
                    scored.append((hit, ent, attr))
        scored.sort(key=lambda t: (-t[0], t[1], t[2]))
        out: list[KeyRef] = []
        for hit, ent, attr in scored[:limit]:
            ans = self.mem.query(Query(key=Key(entity=ent, attr=attr), profile=self.mem.semantic.profile))
            status = getattr(ans, "kernel_status", None)
            out.append(KeyRef(ent, attr, status.value if status is not None else None, hit))
        return out

    def subscribe(self, plan_id: str, keys: Sequence[Key]) -> None:
        self.mem.backend.subscribe(plan_id, keys)

    def reports(
        self, *, id: str | None = None, key: Key | None = None, origin: str | None = None, actor: str | None = None,
        cue: str | None = None,
    ) -> list[ReportRow]:
        """Reports in log order with their admission outcome and whether a withdrawal applies (audit view)."""
        ev = self.mem.evaluation()
        rows: list[ReportRow] = []
        for e in self.mem.backend.scan():
            if not isinstance(e, LogEntry) or e.report.id is None:
                continue
            r = e.report
            rid = e.report.id
            if id is not None and rid != id:
                continue
            if key is not None and r.key != key:
                continue
            if origin is not None and r.origin.value != origin:
                continue
            if actor is not None and r.actor != actor:
                continue
            if cue is not None and r.cue.value != cue:
                continue
            dec = ev.decisions.get(rid)
            rows.append(ReportRow(
                id=rid, lsn=e.lsn, key=r.key, cue=r.cue.value,
                effective_cue=dec.effective_cue.value if dec is not None else r.cue.value, origin=r.origin.value,
                source_id=r.source.id, source_class=r.source.cls, actor=r.actor, origin_group=r.origin_group,
                target=r.target, outcome=dec.record.outcome.value if dec is not None else "unknown",
                reason=dec.record.reason.value if dec is not None else "unknown", withdrawn=rid in ev.withdrawn,
                recorded_at=e.recorded_at, raw_ref=r.raw_ref, prev_hash=e.prev_hash, entry_hash=e.entry_hash,
            ))
        return rows

    def verify_log(self, from_lsn: int = 1, to_lsn: int | None = None) -> VerifyResult:
        return self.mem.backend.verify_log(from_lsn, to_lsn)

    # ------------------------------------------------------------------ sessions

    def bind_session(self, ctx: SessionContext) -> Any:
        """The only way to obtain the agent tool tier. Imported lazily: tools build on this class."""
        from .tools import AgentTools

        self.policy_for(ctx.policy_version)  # fail at bind time, not at the first call
        return AgentTools(self, ctx)

    def event_hash(self, raw: str) -> str:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _identity_fields_seen(result: Any) -> tuple[str, ...]:
    """Names of identity fields an extractor's raw claims carried, when the extractor reports them.

    ``ExtractionResult`` only flags that identity fields were seen; stubs and LLM extractors may attach the names as
    ``identity_fields`` for the audit trail, otherwise the generic marker is recorded."""
    names = getattr(result, "identity_fields", None)
    return tuple(names) if names else ("identity",)


__all__ = [
    "DEFAULT_AGENT_POLICY_LABEL", "DEFAULT_POLICY_LABEL", "LOG_ROW_FIELDS", "MAX_EXPLAIN_DEPTH", "AuditLog", "ConnectorEvent", "ConnectorKind",
    "ConnectorSpec", "ExtractorRequired", "Host", "HostAppend", "HostError", "IngestResult", "InvalidAuthorityRule",
    "KeyRef", "Notice", "ReportRow", "SessionContext",
]
