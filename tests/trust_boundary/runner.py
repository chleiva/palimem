"""Runner for the trust-boundary fixtures (tests/fixtures/trust_boundary, format in its README.md).

It builds a :class:`palimem.agent.Host` over a SQLite store from a fixture's ``setup``, executes the ``steps`` through
the host tier or the agent tool tier, and checks the ``expect`` block with the subset matcher of the conformance suite.
Fixtures are never edited to pass; a fixture the implementation cannot meet is listed with its cause in
``status.json`` (a ratchet, see ``test_runner.py``).

The fixtures use a few shorthands of their own, translated here: ``key: [entity, attr]``, propositions as
``{"form": "value", "v": X}`` and a symbolic ``ref`` per prior report (``$r1`` is its id).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from palimem.admission import AdmissionConfig
from palimem.agent import (
    AuditLog,
    ConnectorEvent,
    ConnectorSpec,
    Host,
    HostError,
    SessionContext,
)
from palimem.extract import (
    ExtractedClaim,
    ExtractionContext,
    ExtractionResult,
    TargetHint,
    build_reports,
)
from palimem.memory import Memory as CoreMemory
from palimem.policy import JUSTIFIED
from palimem.store import SQLiteBackend
from palimem.types import (
    Attr,
    AttrClass,
    AuthorityRule,
    Cue,
    Key,
    LogEntry,
    Origin,
    Query,
    Report,
    Schema,
    SemanticConfig,
    Source,
    ValueType,
)
from palimem.types._codec import ts_from_str
from palimem.types.answer import belief_as_of_from_json
from palimem.types.report import Extractor as Stamp
from palimem.types.values import (
    EnumerationProp,
    MemberProp,
    NotMemberProp,
    NotValueProp,
    ValueProp,
)
from tests.conformance.matcher import State, match, resolve_deep

TB_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "trust_boundary"
_STAMP = Stamp(model="stub", version="1", prompt_hash="0" * 64)
_PLAIN = {"key", "proposition", "cue", "valid_from", "valid_to", "cue_request", "target_request"}


def load_fixtures() -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted(TB_DIR.glob("tb-*.json"))]


# --------------------------------------------------------------------------- the fixtures' shorthands


def _prop(p: Mapping[str, Any] | None) -> Any:
    if p is None:
        return None
    form = p["form"]
    if form == "value":
        return ValueProp(value=p["v"])
    if form == "member":
        return MemberProp(value=p["v"])
    if form == "not_member":
        return NotMemberProp(value=p["v"])
    if form == "not_value":
        return NotValueProp(value=p["v"])
    if form == "enumeration":
        return EnumerationProp(values=tuple(p["values"]))
    raise ValueError(f"unsupported fixture proposition {p!r}")


def _short(p: Any) -> dict[str, Any] | None:
    """A proposition in fixture shorthand (``v``), for comparing log rows."""
    if p is None:
        return None
    if isinstance(p, ValueProp | MemberProp | NotMemberProp | NotValueProp):
        return {"form": p.form, "v": p.value}
    if isinstance(p, EnumerationProp):
        return {"form": p.form, "values": list(p.values)}
    return {"form": p.form}


def _entry_dict(e: LogEntry) -> dict[str, Any]:
    r = e.report
    return {
        "id": r.id, "lsn": e.lsn, "key": [r.key.entity, r.key.attr], "cue": r.cue.value, "origin": r.origin.value,
        "source": {"id": r.source.id, "class": r.source.cls}, "origin_group": r.origin_group, "actor": r.actor,
        "target": r.target, "proposition": _short(r.proposition), "raw_ref": r.raw_ref,
        "recorded_at": e.recorded_at.isoformat(), "prev_hash": e.prev_hash, "entry_hash": e.entry_hash,
        "extractor": None if r.extractor is None else r.extractor.model,
    }


class Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


# --------------------------------------------------------------------------- the stub extractor


@dataclass(frozen=True)
class StubResult(ExtractionResult):
    identity_fields: tuple[str, ...] = ()


_PATTERNS: tuple[tuple[re.Pattern[str], str, int, int], ...] = (
    (re.compile(r"\b(\w+) works at (\w+)"), "employer", 1, 2),
    (re.compile(r"\bI (?:just )?started at (\w+)"), "employer", 0, 1),
    (re.compile(r"\b(\w+) HQ is in (\w+)"), "hq_city", 1, 2),
)


class StubExtractor:
    """Deterministic stand-in for the extractor. Event ids in ``extractor_stub`` return their canned records
    (including any identity fields, which the host must discard); other text goes through three phrase patterns."""

    def __init__(self, canned: Mapping[str, list[dict[str, Any]]], values: Mapping[str, dict[str, Any]]) -> None:
        self.canned = canned
        self.values = values  # ref -> {"entity", "attr", "value"} of prior reports, to turn target_request into a hint

    def extract(self, text: str, ctx: ExtractionContext) -> ExtractionResult:
        records = self.canned.get(ctx.raw_ref or "")
        identity: list[str] = []
        claims: list[ExtractedClaim] = []
        if records is not None:
            for rec in records:
                identity.extend(k for k in rec if k not in _PLAIN and k not in identity)
                claims.append(self._claim(rec))
        else:
            for rx, attr, ent_g, val_g in _PATTERNS:
                m = rx.search(text)
                if m:
                    entity = m.group(ent_g).lower() if ent_g else "I"
                    claims.append(ExtractedClaim(
                        cue=Cue.ASSERT, entity=entity, attr=attr, proposition=ValueProp(value=m.group(val_g)),
                    ))
                    break
        reports, rejections, notes = build_reports(tuple(claims), ctx, _STAMP)
        return StubResult(
            reports=reports, rejections=rejections, notes=notes, claims=tuple(claims),
            identity_fields_seen=bool(identity), stamp=_STAMP, identity_fields=tuple(identity),
        )

    def _claim(self, rec: Mapping[str, Any]) -> ExtractedClaim:
        if "cue_request" in rec:
            ref = self.values[rec["target_request"]]
            return ExtractedClaim(
                cue=Cue(rec["cue_request"]), entity=ref["entity"], attr=ref["attr"], proposition=None,
                target_hint=TargetHint(entity=ref["entity"], attr=ref["attr"], value=ref["value"]),
            )
        return ExtractedClaim(
            cue=Cue(rec["cue"]), entity=rec["key"][0], attr=rec["key"][1], proposition=_prop(rec["proposition"]),
        )


# --------------------------------------------------------------------------- building and running a fixture


def _schema(setup: Mapping[str, Any]) -> Schema:
    attrs = []
    for name, spec in setup["schema"].items():
        vt = ValueType.INT if name == "salary" else ValueType.STRING
        attrs.append(Attr(
            name=name, attr_class=AttrClass(spec["class"]), value_type=vt, inertia=bool(spec.get("inertia", True)),
        ))
    return Schema(version=1, attrs=tuple(attrs))


class World:
    """One fixture's host, clock, reference table and baselines."""

    def __init__(self, fx: Mapping[str, Any]) -> None:
        s = fx["setup"]
        self.setup = s
        self.clock = Clock(ts_from_str(s["now"]))
        rules = tuple(AuthorityRule.from_dict(r) for r in s.get("authority", []))
        backend = SQLiteBackend(":memory:", clock=self.clock, store_secret=b"k" * 32)
        schema = _schema(s)
        core = CoreMemory(
            backend, schema, semantic=SemanticConfig(self_update=False),
            admission=AdmissionConfig(rules=rules), policy=JUSTIFIED,
        )
        self.state = State()
        self.values: dict[str, dict[str, Any]] = {}
        canned = s.get("extractor_stub", {})
        self.host = Host(core, audit=AuditLog(clock=self.clock), extractor=StubExtractor(canned, self.values))
        for cid, c in s.get("connectors", {}).items():
            self.host.register_connector(ConnectorSpec(
                connector_id=cid, source_id=c["source_id"], source_class=c["source_class"],
                origin_group=c["origin_group"], kind=c["kind"], principal=c.get("principal"),
            ))
        for ev in s.get("events", []):
            self.host.add_event(ConnectorEvent(
                event_id=ev["event_id"], connector_id=ev["connector"], raw=ev["raw"], principal=ev.get("principal"),
            ))
        for row in s.get("log", []):
            self._seed(row)
        self.base_lsn = self.host.mem.backend.head().lsn
        self.base_audit = len(self.host.audit)
        self.clock.t = ts_from_str(s["now"])

    def _seed(self, row: Mapping[str, Any]) -> None:
        self.clock.t = ts_from_str(row["recorded_at"])
        target = None
        if row.get("target"):
            target = resolve_deep(row["target"], self.state)
        rep = Report(
            key=Key(entity=row["key"][0], attr=row["key"][1]), cue=Cue(row["cue"]), proposition=_prop(row.get("proposition")),
            target=target, source=Source(id=row["source"]["id"], cls=row["source"]["class"]),
            origin=Origin(row["origin"]), origin_group=row["origin_group"], actor=row["actor"],
        )
        ha = self.host.append(rep, idempotency_key=f"seed:{row['ref']}")
        assert ha.report_id is not None
        e = self.host.mem.backend.get_entry(ha.report_id)
        assert isinstance(e, LogEntry)
        self.state.refs[row["ref"]] = {"id": ha.report_id, "lsn": e.lsn, "entry_hash": e.entry_hash}
        prop = row.get("proposition")
        self.values[row["ref"]] = {"entity": row["key"][0], "attr": row["key"][1], "value": None if prop is None else prop.get("v")}

    # ------------------------------------------------------------------ steps

    def session(self, override: Mapping[str, Any] | None) -> SessionContext:
        d = dict(self.setup["session"])
        if override:
            d.update(override)
        return SessionContext(
            session_id=d["session_id"], agent_principal=d["agent_principal"],
            end_user_principal=d.get("end_user_principal"),
            allowed_attrs=None if d.get("allowed_attrs") is None else tuple(d["allowed_attrs"]),
            policy_version=d.get("policy_version", "p-default"),
            max_explanation_budget=d.get("max_explanation_budget", 50),
        )

    def step(self, st: Mapping[str, Any]) -> dict[str, Any]:
        args = resolve_deep(st.get("args", {}), self.state)
        call = st["call"]
        if st["tier"] == "agent":
            tools = self.host.bind_session(self.session(st.get("as_session")))
            return dict(tools.call(call, args).data)
        try:
            return self._host_call(call, args)
        except HostError as e:
            return {"error": {"code": e.code, "message": str(e)}}

    def _host_call(self, call: str, args: Mapping[str, Any]) -> dict[str, Any]:
        h = self.host
        if call == "ingest_event":
            r = h.ingest_event(args["event_id"])
            return {"report_ids": list(r.report_ids), "notices": [n.to_dict() for n in r.notices],
                    "rejections": list(r.rejections)}
        if call == "set_authority":
            return {"admission_version": h.set_authority(args["rules"])}
        if call == "append":
            rd = dict(args["report"])
            rep = {
                **{k: v for k, v in rd.items() if k not in ("key", "source")},
                "key": {"entity": rd["key"][0], "attr": rd["key"][1]}, "cue": rd["cue"], "origin": rd["origin"],
                "source": {"id": rd["source"]["id"], "class": rd["source"]["class"]},
            }
            return h.append(rep, idempotency_key=args["idempotency_key"]).to_dict()
        if call == "verify_log":
            res = h.verify_log(1 if args.get("from", 0) in (0, None) else int(args["from"]))
            return {"ok": res.ok, "problems": [p.__dict__ if hasattr(p, "__dict__") else str(p) for p in res.problems]}
        raise ValueError(f"unsupported host call {call!r} in fixture")

    # ------------------------------------------------------------------ expectations

    def log_delta(self) -> list[dict[str, Any]]:
        out = []
        for e in self.host.mem.backend.scan(self.base_lsn + 1):
            if isinstance(e, LogEntry):
                out.append(_entry_dict(e))
        return out

    def audit_delta(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self.host.audit.rows(self.base_audit)]

    def answer(self, q: Mapping[str, Any]) -> Any:
        q = resolve_deep(q, self.state)
        kind = q["kind"]
        if kind == "belief":
            key = Key(entity=q["key"][0], attr=q["key"][1])
            va = ts_from_str(q["valid_at"]) if q.get("valid_at") else None
            asof = belief_as_of_from_json(q["belief_as_of"]) if q.get("belief_as_of") is not None else None
            ans = self.host.query(Query(key=key, valid_at=va, belief_as_of=asof), policy="p-default")
            from palimem.agent import answer_json

            return answer_json(
                ans, host=self.host, key=key, as_of=asof, policy_label="p-default", max_alternatives=50,
            )
        if kind == "reports":
            flt = q.get("filter", {})
            rows = [r.to_dict() for r in self.host.reports(id=flt.get("id"), origin=flt.get("origin"), actor=flt.get("actor"))]
            if "id" not in flt:  # a listing by attribute shows live evidence: withdrawn reports and operator rows are not in it
                rows = [r for r in rows if not r["withdrawn"] and r["cue"] in ("assert", "change", "correct")]
            out: dict[str, Any] = {"rows": rows, "count": len(rows)}
            if len(rows) == 1:
                out.update(rows[0])
            return out
        if kind == "source_class":
            return self.host.source_class(q["source_id"])
        raise ValueError(f"unsupported answer kind {kind!r}")


def run_fixture(fx: Mapping[str, Any]) -> list[str]:
    """Run one fixture; returns the list of mismatches (empty = pass)."""
    w = World(fx)
    problems: list[str] = []
    exp = fx["expect"]
    results = [w.step(st) for st in fx["steps"]]
    for i, want in enumerate(exp.get("steps", [])):
        act: Any = results[i]
        if "error" in want:
            problems += [f"step {i}: {m}" for m in match(want["error"], act.get("error", {}))]
        else:
            problems += [f"step {i}: {m}" for m in match(resolve_deep(want["result"], w.state), act)]
    for a, b in exp.get("equal_results", []):
        if results[a] != results[b]:
            problems.append(f"steps {a} and {b} differ: {results[a]!r} vs {results[b]!r}")
    if "log_delta" in exp:
        problems += [f"log_delta: {m}" for m in match(resolve_deep(exp["log_delta"], w.state), w.log_delta())]
    if "audit_delta" in exp:
        problems += [f"audit_delta: {m}" for m in match(exp["audit_delta"], w.audit_delta())]
    for j, ans in enumerate(exp.get("answers", [])):
        problems += [f"answer {j}: {m}" for m in match(resolve_deep(ans["match"], w.state), w.answer(ans["query"]))]
    return problems
