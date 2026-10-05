"""The agent tool API (T-F2): what an LLM may call, bound to a host-created session.

Every identity field is bound by the host: the source is ``agent:<principal>`` of class ``agent``, the origin is
``agent_statement`` or ``agent_hypothesis``, the origin group and actor are the bound agent principal. An argument
that names any of them (or anything else outside a tool's schema) is **stripped, answered with a notice and
audited**, never rejected wholesale and never silently accepted (docs/API_TRUST_BOUNDARY.md R1, R2).

Authority (R6): ``retract`` applies only to reports the same agent principal authored with an agent-class origin; any
other target is recorded as an ``allege`` and the result is ``logged_only``, identically for unknown ids and for
ids outside the session's scope (R7). ``dispute`` applies only where the host granted that principal a ``dispute``
rule; the tool is not even listed otherwise.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from palimem.extract import ExtractionContext
from palimem.store import Tombstone
from palimem.types import (
    Cue,
    ExplainMode,
    ExplainQuery,
    Key,
    Origin,
    Power,
    Query,
    Report,
    Source,
    ValidationError,
    check_proposition_for_attr,
)
from palimem.types._codec import ts_from_str
from palimem.types.answer import belief_as_of_from_json
from palimem.types.authority import AGENT_CLASS_ORIGINS
from palimem.types.enums import AttrClass
from palimem.types.values import MemberProp, ValueProp

from .host import ExtractorRequired, Host, HostError, Notice, SessionContext
from .render import answer_json, answer_text, explanation_json, explanation_text


class ToolError(HostError):
    code = "tool_error"


@dataclass(frozen=True)
class ToolOutput:
    data: dict[str, Any]
    text: str
    is_error: bool = False


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    writes: bool
    handler: Callable[..., dict[str, Any]] = field(compare=False, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "description": self.description, "inputSchema": self.input_schema,
            "annotations": {"readOnlyHint": not self.writes, "destructiveHint": False, "idempotentHint": not self.writes,
                            "openWorldHint": False},
        }


_STR: dict[str, Any] = {"type": "string"}


def _schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


_QUERY: dict[str, Any] = {
    "description": "A name to look up (e.g. 'alice employer') or an exact key {entity, attr}.",
    "oneOf": [_STR, {"type": "object", "properties": {"entity": _STR, "attr": _STR},
                      "required": ["entity", "attr"], "additionalProperties": False}],
}
_WHEN: dict[str, Any] = {"type": "string", "description": "ISO timestamp (UTC), default now."}
_AS_OF: dict[str, Any] = {
    "description": "What the memory believed at an earlier point in the log: a log position (integer) or an ISO timestamp.",
    "oneOf": [{"type": "integer", "minimum": 0}, _STR],
}


class AgentTools:
    """Tools bound to one session. Build with :meth:`Host.bind_session`; never construct from LLM input."""

    def __init__(self, host: Host, ctx: SessionContext) -> None:
        self.host = host
        self.ctx = ctx
        self._writes = 0
        self._specs = self._build_specs()

    # ------------------------------------------------------------------ catalogue and dispatch

    def _has_dispute_grant(self) -> bool:
        me = self.ctx.agent_principal
        cfg = self.host.mem.admission
        rules = list(cfg.effective_rules())
        for a in self.host.mem.schema.attrs:
            rules.extend(a.authority)
        return any(r.who.kind.value == "principal" and r.who.value == me and Power.DISPUTE in r.may for r in rules)

    def _build_specs(self) -> dict[str, ToolSpec]:
        specs = [
            ToolSpec(
                "remember",
                "Store something you were told or inferred. It is recorded as YOUR statement (or hypothesis); it never "
                "becomes established evidence by itself and never confirms anyone else's report. To record what an "
                "external event said, pass cite_event with its id: the evidence then takes its provenance from the event, "
                "not from your words. Give plain text, or entity+attr+value for a typed fact.",
                _schema({
                    "text": {**_STR, "description": "What to remember, in plain words."},
                    "kind": {"type": "string", "enum": ["note", "hypothesis"], "default": "note",
                             "description": "'hypothesis' for a guess; it is stored but never counts as corroboration."},
                    "about": {**_STR, "description": "The entity this note is about (a hint, not authoritative)."},
                    "cite_event": {**_STR, "description": "Id of an event the host already holds."},
                    "entity": _STR, "attr": _STR,
                    "value": {"description": "The value for entity.attr.", "type": ["string", "number", "boolean"]},
                    "request_id": {**_STR, "description": "Optional id that makes a retry of this call safe."},
                }),
                True, self.remember,
            ),
            ToolSpec(
                "recall",
                "Ask what the memory currently justifies about a key. Read kernel_status and decision together: "
                "'established' means the evidence settles it, 'unresolved' means it lists the alternatives, 'unknown' "
                "means no admissible evidence (never guess). If single_origin is true the answer rests on one origin "
                "group and is uncorroborated. Optionally ask as of a past valid time or an earlier point in the log.",
                _schema({
                    "query": _QUERY, "valid_at": _WHEN, "belief_as_of": _AS_OF,
                    "max_alternatives": {"type": "integer", "minimum": 1},
                }, ["query"]),
                False, self.recall,
            ),
            ToolSpec(
                "retract",
                "Withdraw a report you wrote yourself (any earlier session). It cannot withdraw evidence from external "
                "sources: such an attempt is only logged, and the result says so ('logged_only').",
                _schema({"report_id": _STR, "reason": _STR}, ["report_id"]),
                True, self.retract,
            ),
            ToolSpec(
                "explain",
                "Show which reports justify the current answer for a key: who said each, and from which origin group.",
                _schema({
                    "query": _QUERY, "valid_at": _WHEN, "belief_as_of": _AS_OF,
                    "mode": {"type": "string", "enum": ["one", "all"], "default": "one"},
                    "depth": {"type": "integer", "minimum": 1},
                }, ["query"]),
                False, self.explain,
            ),
        ]
        specs.append(ToolSpec(
            "dispute",
            "Dispute a report. Only effective where the host granted you dispute authority; otherwise it is only "
            "logged. A dispute never silences or quarantines the source.",
            _schema({"report_id": _STR, "reason": _STR}, ["report_id"]),
            True, self.dispute,
        ))
        return {s.name: s for s in specs}

    def tool_specs(self, *, read_only: bool = False) -> list[ToolSpec]:
        """The tools this session *lists* (``read_only`` leaves out every writing tool). ``dispute`` is listed only
        when the host granted this principal dispute authority; calling it anyway is harmless (logged only, R6/R7)."""
        granted = self._has_dispute_grant()
        return [
            s for s in self._specs.values()
            if not (read_only and s.writes) and not (s.name == "dispute" and not granted)
        ]

    def call(self, name: str, arguments: Mapping[str, Any] | None = None) -> ToolOutput:
        """Dispatch a tool call: strip and audit what a tool does not take (R2), call it, order the notices."""
        spec = self._specs.get(name)
        if spec is None:
            return ToolOutput({"error": {"code": "unknown_tool", "message": f"no tool named {name!r}"}}, "unknown tool", True)
        args = dict(arguments or {})
        allowed = set(spec.input_schema["properties"])
        order = list(args)
        stripped = [k for k in args if k not in allowed]
        notices: list[Notice] = [Notice("field_ignored", k) for k in stripped]
        if stripped:
            self.host.audit.append("trust_downgrade", tool=name, session=self.ctx.session_id, fields=stripped)
            for k in stripped:
                del args[k]
        try:
            _validate(spec.input_schema, args)
            if spec.writes:
                self._write_budget()
            data = spec.handler(**args)
        except (HostError, ValidationError, ValueError, LookupError) as e:
            code = getattr(e, "code", "invalid_arguments")
            return ToolOutput({"error": {"code": code, "message": str(e)}}, f"error ({code}): {e}", True)
        all_notices = notices + [_notice(n) for n in data.pop("_notices", [])]
        data["notices"] = [n.to_dict() for n in _ordered(all_notices, order)]
        return ToolOutput(data, _text_of(name, data))

    def _write_budget(self) -> None:
        self._writes += 1
        limit = self.ctx.max_writes
        if limit is not None and self._writes > limit:
            raise ToolError("write limit for this session reached", code="rate_limited")

    # ------------------------------------------------------------------ tools

    def remember(
        self, text: str | None = None, *, kind: str = "note", about: str | None = None, cite_event: str | None = None,
        entity: str | None = None, attr: str | None = None, value: Any = None, request_id: str | None = None,
    ) -> dict[str, Any]:
        ctx, host = self.ctx, self.host
        if text is not None and len(text) > ctx.max_text_length:
            raise ToolError(f"text longer than {ctx.max_text_length} characters", code="rate_limited")
        if cite_event is not None:
            return self._cite(cite_event, text)
        origin = Origin.AGENT_HYPOTHESIS if kind == "hypothesis" else Origin.AGENT_STATEMENT
        me = ctx.agent_principal
        source = Source(id=me, cls="agent")
        notices: list[Notice] = []
        reports: list[Report]
        if entity is not None or attr is not None or value is not None:
            if entity is None or attr is None or value is None:
                raise ToolError("a typed fact needs entity, attr and value together", code="invalid_arguments")
            if not ctx.in_scope(attr):  # check scope before anything can declare an attribute on the agent's say-so
                return {"report_ids": [], "origin": origin.value, "admitted": False,
                        "_notices": [Notice("scope_denied", "attr", attr)]}
            reports = [self._typed(entity, attr, value, origin, source)]
        else:
            if text is None or not text.strip():
                raise ToolError("remember needs text, or entity+attr+value", code="invalid_arguments")
            if host.extractor is None:
                raise ExtractorRequired(
                    "plain text needs an extractor on the host; pass entity+attr+value, or configure an extractor"
                )
            xctx = ExtractionContext(
                source=source, origin_group=me, actor=me, origin=origin, subject_entity=about, schema=host.mem.schema,
                allowed_cues=frozenset({Cue.ASSERT}),
            )
            res = host.extractor.extract(text, xctx)
            reports = list(res.reports)
            if res.identity_fields_seen:
                host.audit.append("trust_downgrade", tool="extractor", session=ctx.session_id, fields=["identity"])
            for rej in res.rejections:
                notices.append(Notice("claim_rejected", detail=f"{rej.reason}: {rej.detail}"[:200]))
        ids: list[str] = []
        admitted = False
        for i, rep in enumerate(reports):
            if not ctx.in_scope(rep.key.attr):
                notices.append(Notice("scope_denied", "attr", rep.key.attr))
                continue
            key = f"agent:{me}:{request_id}:{i}" if request_id else uuid.uuid4().hex
            ha = host.append(rep, idempotency_key=key)
            if ha.report_id is not None:
                ids.append(ha.report_id)
            admitted = admitted or ha.admitted
        if not reports and not notices:
            notices.append(Notice("nothing_extracted"))
        return {"report_ids": ids, "origin": origin.value, "admitted": admitted, "_notices": notices}

    def _typed(self, entity: str, attr: str, value: Any, origin: Origin, source: Source) -> Report:
        host = self.host
        host.ensure_declared(attr)
        try:
            a = host.mem.schema.attr(attr)
        except KeyError:
            raise ToolError(f"attribute {attr!r} is not declared", code="undeclared_attr") from None
        if a.attr_class is AttrClass.DERIVED:
            raise ToolError(f"attribute {attr!r} is derived and cannot be remembered directly", code="derived_attr")
        multi = a.attr_class is AttrClass.MULTI_SET
        prop = MemberProp(value=value) if multi else ValueProp(value=value)
        check_proposition_for_attr(a, prop)
        me = self.ctx.agent_principal
        return Report(
            key=Key(entity=entity, attr=attr), cue=Cue.ASSERT, proposition=prop, source=source, origin=origin,
            origin_group=me, actor=me,
        )

    def _cite(self, event_id: str, text: str | None) -> dict[str, Any]:
        ctx, host = self.ctx, self.host
        notices: list[Notice] = []
        if host.event(event_id, ctx.session_id) is None:
            host.audit.append("event_unknown", tool="remember", session=ctx.session_id)
            return {"report_ids": [], "origin": None, "admitted": False, "_notices": [Notice("event_unknown")]}
        if text is not None:
            notices.append(Notice("field_ignored", "text", "with cite_event the proposition comes from the event"))
        res = host.ingest_event(event_id, session_id=ctx.session_id, allowed_attrs=ctx.allowed_attrs)
        notices.extend(res.notices)
        for r in res.rejections:
            notices.append(Notice("claim_rejected", detail=r[:200]))
        return {
            "report_ids": list(res.report_ids), "origin": Origin.EXTERNAL_OBSERVATION.value,
            "admitted": any(res.admitted), "_notices": notices,
        }

    def _key_of(self, q: str | Mapping[str, Any]) -> tuple[Key | None, list[Any]]:
        """A key from an exact reference, or from a lookup when the text resolves to exactly one key."""
        if isinstance(q, Mapping):
            return Key(entity=str(q["entity"]), attr=str(q["attr"])), []
        found = self.host.find(q)
        if not found:
            return None, []
        # a clear best match resolves; a tie between several plausible keys is returned for the caller to choose
        if len(found) > 1 and found[0].score == found[1].score:
            return None, found
        return Key(entity=found[0].entity, attr=found[0].attr), found

    def recall(
        self, query: str | Mapping[str, Any], *, valid_at: str | None = None, belief_as_of: int | str | None = None,
        max_alternatives: int | None = None,
    ) -> dict[str, Any]:
        ctx, host = self.ctx, self.host
        notices: list[Notice] = []
        cap = ctx.max_alternatives
        if max_alternatives is not None:
            if max_alternatives > cap:
                notices.append(Notice("clamped", "max_alternatives"))
            cap = min(max_alternatives, cap)
        key, found = self._key_of(query)
        if key is None and found:
            return {
                "kind": "ambiguous", "candidates": [f.to_dict() for f in found if ctx.in_scope(f.attr)][:cap],
                "_notices": notices,
            }
        if key is None or not ctx.in_scope(key.attr):
            return {"kind": "no_match", "_notices": notices}
        as_of = belief_as_of_from_json(belief_as_of) if belief_as_of is not None else None
        va = None if valid_at is None else ts_from_str(valid_at, "valid_at")
        q = Query(key=key, valid_at=va, belief_as_of=as_of, profile=host.mem.semantic.profile)
        ans = host.query(q, policy=ctx.policy_version)
        out = answer_json(
            ans, host=host, key=key, as_of=as_of, policy_label=ctx.policy_version, max_alternatives=cap,
        )
        out["_notices"] = notices
        return out

    def explain(
        self, query: str | Mapping[str, Any], *, valid_at: str | None = None, belief_as_of: int | str | None = None,
        mode: str = "one", depth: int | None = None,
    ) -> dict[str, Any]:
        ctx, host = self.ctx, self.host
        notices: list[Notice] = []
        applied = ctx.max_depth if depth is None else depth
        if applied > ctx.max_depth:
            notices.append(Notice("clamped", "depth"))
            applied = ctx.max_depth
        key, found = self._key_of(query)
        if key is None or not ctx.in_scope(key.attr):
            return {"kind": "no_match", "candidates": [f.to_dict() for f in found if ctx.in_scope(f.attr)][:3],
                    "_notices": notices}
        as_of = belief_as_of_from_json(belief_as_of) if belief_as_of is not None else None
        va = None if valid_at is None else ts_from_str(valid_at, "valid_at")
        exp = host.explain(ExplainQuery(
            key=key, valid_at=va, belief_as_of=as_of, mode=ExplainMode(mode), depth=applied,
        ))
        ans = host.query(
            Query(key=key, valid_at=va, belief_as_of=as_of, profile=host.mem.semantic.profile), policy=ctx.policy_version
        )
        out = explanation_json(exp, host=host, depth_applied=applied, key=key, answer=ans)
        out["_notices"] = notices
        return out

    def retract(self, report_id: str, *, reason: str | None = None) -> dict[str, Any]:
        return self._operate(Cue.WITHDRAW, Power.WITHDRAW, report_id, reason, "retract")

    def dispute(self, report_id: str, *, reason: str | None = None) -> dict[str, Any]:
        return self._operate(Cue.DISPUTE, Power.DISPUTE, report_id, reason, "dispute")

    def _operate(self, cue: Cue, power: Power, report_id: str, reason: str | None, tool: str) -> dict[str, Any]:
        ctx, host = self.ctx, self.host
        denied = {"effect": "logged_only", "_notices": [Notice("scope_denied")]}
        t = host.mem.backend.get_entry(report_id)
        if t is None or isinstance(t, Tombstone) or not ctx.in_scope(t.report.key.attr):
            # unknown, erased and out-of-scope targets are indistinguishable (R7); only the audit trail knows
            host.audit.append("scope_denied", tool=tool, session=ctx.session_id, target=report_id)
            return denied
        me = ctx.agent_principal
        target = t.report
        rep = Report(
            key=target.key, cue=cue, target=report_id, source=Source(id=me, cls="agent"),
            origin=Origin.AGENT_STATEMENT, origin_group=me, actor=me,
        )
        allowed = host.authorise(rep, power)
        if power is Power.WITHDRAW:  # R6: only the agent's own agent-class reports, from any earlier session
            allowed = allowed and target.actor == me and target.origin in AGENT_CLASS_ORIGINS
        if not allowed:
            host.append(replace(rep, cue=Cue.ALLEGE), idempotency_key=uuid.uuid4().hex)
            return denied
        ha = host.append(rep, idempotency_key=uuid.uuid4().hex)
        effect = "applied" if ha.outcome == "admissible" else "logged_only"
        out: dict[str, Any] = {"effect": effect, "_notices": []}
        if effect == "logged_only":
            out["_notices"].append(Notice("scope_denied"))
        return out


def _notice(n: Any) -> Notice:
    return n if isinstance(n, Notice) else Notice(**n)


def _ordered(notices: list[Notice], arg_order: list[str]) -> list[Notice]:
    """Notices in the order the call named their arguments; notices about no argument come last."""
    pos = {a: i for i, a in enumerate(arg_order)}
    return sorted(notices, key=lambda n: pos.get(n.field or "", len(pos) + 1))


def _validate(schema: Mapping[str, Any], args: Mapping[str, Any]) -> None:
    props = schema["properties"]
    for r in schema.get("required", ()):
        if r not in args:
            raise ToolError(f"missing required argument {r!r}", code="invalid_arguments")
    for k, v in args.items():
        spec = props[k]
        if not _fits(spec, v):
            raise ToolError(f"argument {k!r} has the wrong type or value", code="invalid_arguments")


def _fits(spec: Mapping[str, Any], v: Any) -> bool:
    if "oneOf" in spec:
        return any(_fits(s, v) for s in spec["oneOf"])
    t = spec.get("type")
    types = t if isinstance(t, list) else [t] if t else []
    ok = True
    if types:
        ok = any(_is(tp, v) for tp in types)
    if ok and "enum" in spec:
        ok = v in spec["enum"]
    if ok and "minimum" in spec and isinstance(v, int | float) and not isinstance(v, bool):
        ok = v >= spec["minimum"]
    if ok and spec.get("type") == "object" and isinstance(v, Mapping):
        ok = all(r in v for r in spec.get("required", ())) and all(k in spec.get("properties", {}) for k in v)
    return ok


def _is(tp: str, v: Any) -> bool:
    if tp == "string":
        return isinstance(v, str)
    if tp == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if tp == "number":
        return isinstance(v, int | float) and not isinstance(v, bool)
    if tp == "boolean":
        return isinstance(v, bool)
    if tp == "object":
        return isinstance(v, Mapping)
    return False


def _text_of(name: str, data: Mapping[str, Any]) -> str:
    if name == "recall":
        kind = data.get("kind")
        if kind == "resolved" or kind == "resource_limited":
            return answer_text(data)
        if kind == "ambiguous":
            c = ", ".join(f"{x['entity']}/{x['attr']}" for x in data["candidates"])
            return f"Several keys match: {c}. Ask again with an exact {{entity, attr}}."
        return "No matching key in this session's scope."
    if name == "explain":
        return explanation_text(data) if data.get("kind") == "explanation" else "No matching key in this session's scope."
    if name == "remember":
        if data.get("origin") is None:
            return "Nothing recorded (unknown event)."
        n = len(data["report_ids"])
        if data["origin"] == "external_observation":
            return f"Recorded {n} report(s) from the cited event with the event's own provenance."
        return (
            f"Recorded {n} report(s) as your {data['origin'].replace('agent_', '')}. "
            "This is not evidence: it will not be treated as established or as corroboration."
        )
    if name in ("retract", "dispute"):
        if data["effect"] == "applied":
            return f"{name}: applied."
        return f"{name}: not applied (logged only). You may only act on your own reports or where the host granted you authority."
    return ""


__all__ = ["AgentTools", "ToolError", "ToolOutput", "ToolSpec"]
