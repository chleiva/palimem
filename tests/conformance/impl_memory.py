"""Conformance adapter over :class:`palimem.memory.Memory` (Lane M).

    python -m tests.conformance.runner --impl tests.conformance.impl_memory:MemoryImplementation
    PALIMEM_CONFORMANCE_BACKEND=sqlite python -m tests.conformance.runner --impl tests.conformance.impl_memory:MemoryImplementation

The adapter translates the JSON ops of ``docs/CONFORMANCE.md`` into calls on ``Memory``. It returns ``NotImplemented``
for an op the pipeline does not do yet (merge, tamper, backup/restore, find, extractor, crash points other than the store's
own steps...), so such fixtures are reported as skipped with a reason, never as failures. It does not edit expectations:
whatever the pipeline answers is compared with the hand-derived fixture.

Setup translation (compact schema -> contract ``Attr`` + kernel schema):

* ``single_stable`` / ``multi_set`` with ``inertia: false`` are accepted (inertia is moot for them) and the kernel schema
  carries inertia on; ``single_changeable`` with ``inertia: false`` has no specified semantics (S-08) -> not implemented.
* a derived rule ``head(e,c) <- body1(e,x), body2(x,c) [unless exc(e,true)]`` is parsed into the kernel's rule form;
  variables are bare names, ``true``/``false`` are boolean constants.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from palimem.admission import AdmissionConfig
from palimem.kernel import AttrSpec, KernelSchema, KernelUnsupported, RuleSpec
from palimem.memory import Memory, NotReconstructableError
from palimem.policy import JUSTIFIED
from palimem.store import (
    ErasureReason,
    InMemoryBackend,
    SQLiteBackend,
    StoreError,
    Tombstone,
)
from palimem.types import (
    Answer,
    Attr,
    AttrClass,
    AuthorityRule,
    Completeness,
    CompletenessMode,
    CompletenessScope,
    ExplainQuery,
    Key,
    LogEntry,
    Profile,
    Query,
    Report,
    Rule,
    Schema,
    SemanticConfig,
    ValidationError,
    ValueType,
)
from palimem.types.limits import DEFAULT_ENVIRONMENT_BUDGET

_RULE = re.compile(r"^\s*(\w+)\(([^)]*)\)\s*<-\s*(.+?)\s*(?:unless\s+(.+))?$")
_LIT = re.compile(r"(\w+)\(([^)]*)\)")


def _term(t: str) -> Any:
    t = t.strip()
    if t == "true":
        return True
    if t == "false":
        return False
    return "?" + t


def parse_rule(fn: str) -> RuleSpec:
    m = _RULE.match(fn)
    if not m:
        raise KernelUnsupported(f"unparsable rule: {fn!r}")
    head_attr, head_args, body, exc = m.groups()
    h = [a.strip() for a in head_args.split(",")]
    if len(h) != 2:
        raise KernelUnsupported(f"rule head must be binary: {fn!r}")

    def lits(text: str) -> tuple[tuple[str, str, Any], ...]:
        out = []
        for attr, args in _LIT.findall(text):
            a = [x.strip() for x in args.split(",")]
            if len(a) != 2:
                raise KernelUnsupported(f"literal must be binary: {attr}({args})")
            out.append((attr, str(_term(a[0])), _term(a[1])))
        return tuple(out)

    return RuleSpec(
        id=f"rule:{head_attr}", head=(head_attr, str(_term(h[0])), _term(h[1])), body=lits(body),
        exceptions=lits(exc) if exc else (),
    )


def _completeness(v: str | None) -> Completeness:
    if v in (None, "open"):
        return Completeness()
    if v == "declared":
        return Completeness(mode=CompletenessMode.DECLARED, scope=CompletenessScope())
    return Completeness(mode=CompletenessMode.BY_ENUMERATION)


def build_schemas(setup: dict[str, Any]) -> tuple[Schema, KernelSchema]:
    attrs: list[Attr] = []
    specs: dict[str, AttrSpec] = {}
    rules: list[RuleSpec] = []
    for name, a in setup["schema"].items():
        cls = AttrClass(a["class"])
        inertia = bool(a.get("inertia", False))
        rule = a.get("rule")
        attrs.append(
            Attr(
                name=name, attr_class=cls, value_type=ValueType(a.get("value_type", "string")), inertia=inertia,
                rule=None if rule is None else Rule(reads=tuple(rule["reads"]), fn=rule["fn"]),
                completeness=_completeness(a.get("completeness")),
            )
        )
        if cls is AttrClass.DERIVED:
            assert rule is not None
            r = parse_rule(rule["fn"])
            rules.append(r)
            specs[name] = AttrSpec(name, "single", True, error_allowed=False, derived=True)
        elif cls is AttrClass.SINGLE_CHANGEABLE:
            if not inertia:
                raise KernelUnsupported(f"{name}: inertia=false on a changeable attribute has no specified semantics (S-08)")
            specs[name] = AttrSpec(name, "single", True)
        elif cls is AttrClass.SINGLE_STABLE:
            specs[name] = AttrSpec(name, "single", False)
        else:
            specs[name] = AttrSpec(name, "multi", False, competing_values=False)
    return Schema(version=1, attrs=tuple(attrs)), KernelSchema(attrs=specs, rules=tuple(rules))


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)  # strictly increasing unless an op carries an explicit `at`
        return self.now

    def set(self, ts: str) -> None:
        self.now = datetime.fromisoformat(ts) - timedelta(seconds=1)


class MemoryImplementation:
    name = "palimem.Memory"

    def __init__(self) -> None:
        self.mem: Memory | None = None
        self.clock = _Clock()
        self._last_events: list[Any] = []
        self._seen_entities: set[str] = set()
        self._backend_kind = os.environ.get("PALIMEM_CONFORMANCE_BACKEND", "memory")

    # ------------------------------------------------------------------ protocol

    def capabilities(self) -> set[str]:
        return {"budget_control", "completion_jobs", "delete", "hash_chain", "profile_revise_stream_v1", "outbox"}

    def start(self, setup: dict[str, Any]) -> Any:
        self.clock = _Clock()
        self._last_events = []
        self._seen_entities = set()
        profile = Profile(setup.get("profile", "open-world"))
        try:
            schema, ks = build_schemas(setup)
            rules = tuple(AuthorityRule.from_dict(r) for r in setup.get("authority", ()))
            admission = AdmissionConfig(profile=profile, rules=rules)
            self._traversal: int | None = (setup.get("limits") or {}).get("traversal_budget")
            self._env_budget: int = (setup.get("limits") or {}).get("environment_budget", DEFAULT_ENVIRONMENT_BUDGET)
            self._schema, self._ks, self._admission, self._profile = schema, ks, admission, profile
            self._build_memory()
        except KernelUnsupported as e:
            if "expect_load_error" in setup:
                return {"error": {"detail": str(e)}}
            raise NotImplementedError(str(e)) from e
        except (ValidationError, KeyError, ValueError) as e:
            return {"error": {"detail": str(e)}}
        return None

    def _build_memory(self) -> None:
        kw: dict[str, Any] = {"clock": self.clock, "store_secret": b"conformance-secret-0123456789abcd"}
        if self._traversal is not None:
            kw["traversal_budget"] = self._traversal
        backend = InMemoryBackend(**kw) if self._backend_kind == "memory" else SQLiteBackend(":memory:", **kw)
        self.backend = backend
        self.mem = Memory(
            backend, self._schema, kernel_schema=self._ks,
            semantic=SemanticConfig(self_update=False, profile=self._profile), admission=self._admission,
            policy=JUSTIFIED, budget=self._env_budget,
        )

    def close(self) -> None:
        if self.mem is not None:
            self.mem.close()

    # ------------------------------------------------------------------ ops

    def execute(self, op: dict[str, Any]) -> Any:
        assert self.mem is not None
        fn: Callable[[dict[str, Any]], Any] | None = getattr(self, f"op_{op['op']}", None)
        if fn is None:
            return NotImplemented
        try:
            return fn(op)
        except KernelUnsupported:
            return NotImplemented
        except NotReconstructableError as e:
            return {"error": {"code": "not_reconstructable", "detail": str(e)}}
        except (ValueError, LookupError, ValidationError, StoreError) as e:
            return {"error": {"code": type(e).__name__, "detail": str(e)}}

    def op_configure(self, op: dict[str, Any]) -> Any:
        assert self.mem is not None
        limits = op.get("limits") or {}
        if "collapse" in op:
            return NotImplemented
        unknown = set(limits) - {"environment_budget", "traversal_budget", "revision_budget"}
        if unknown:
            return NotImplemented
        if "environment_budget" in limits:
            self.mem.pipeline.budget = int(limits["environment_budget"])
            self._env_budget = int(limits["environment_budget"])
        if "revision_budget" in limits:
            self.mem.pipeline.revision_budget = int(limits["revision_budget"])
        if "traversal_budget" in limits:
            # the store takes it at construction and has no setter: a test adapter may set the engine attribute
            self.backend._traversal_budget = int(limits["traversal_budget"])  # type: ignore[attr-defined]
        return {"ok": True}

    def op_append(self, op: dict[str, Any]) -> Any:
        assert self.mem is not None
        if op.get("crash"):
            return NotImplemented
        if op.get("at"):
            self.clock.set(op["at"])
        report = Report.from_dict(op["report"])
        self._seen_entities.add(report.key.entity)
        res = self.mem.append(report, idempotency_key=op.get("idempotency_key"), complete=False)
        assert res.entry is not None
        rid = res.entry.report.id
        own = next(a for a in res.admissions if a.report_id == rid)
        ev = self.mem.pipeline.evaluate(res.entry.lsn)
        assert rid is not None
        return {
            "report_id": rid, "lsn": res.entry.lsn, "generation": res.generation,
            "recorded_cue": ev.decisions[rid].effective_cue.value,
            "admission": {"outcome": own.outcome.value, "reason": own.reason.value, "admission_version": own.admission_version},
            "duplicate": res.replayed,
        }

    def op_query(self, op: dict[str, Any]) -> Any:
        assert self.mem is not None
        q = dict(op["query"])
        q.setdefault("profile", self._profile.value)
        ans: Answer = self.mem.query(Query.from_dict(q))
        return ans.to_dict()

    def op_explain(self, op: dict[str, Any]) -> Any:
        assert self.mem is not None
        d = {k: op[k] for k in ("key", "mode", "depth", "valid_at", "belief_as_of") if k in op}
        return self.mem.explain(ExplainQuery.from_dict(d)).to_dict()

    def op_reports(self, op: dict[str, Any]) -> Any:
        assert self.mem is not None
        flt = op.get("filter") or {}
        ev = self.mem.evaluation(None)
        rows: list[dict[str, Any]] = []
        for row in self.backend.scan(1):
            if isinstance(row, Tombstone):
                rows.append({"id": row.report_id, "lsn": row.lsn, "tombstone": True, "entry_hash": row.entry_hash})
                continue
            assert isinstance(row, LogEntry)
            r = row.report
            rid = r.id
            assert rid is not None
            d = ev.decisions[rid]
            rec = d.record
            item = {
                "id": rid, "lsn": row.lsn, "cue": d.effective_cue.value, "key": r.key.to_dict(), "actor": r.actor,
                "origin": r.origin.value, "source": r.source.to_dict(),
                "proposition": None if r.proposition is None else r.proposition.to_dict(), "target": r.target,
                "withdrawn": rid in ev.withdrawn, "tombstone": False, "entry_hash": row.entry_hash,
                "admission": {
                    "outcome": rec.outcome.value, "reason": rec.reason.value,
                    # one confirmer is a scalar id (the fixtures' shape), several are a list, none is null
                    "confirmed_by": None if not rec.confirmed_by else (rec.confirmed_by[0] if len(rec.confirmed_by) == 1 else list(rec.confirmed_by)),
                    "admission_version": rec.admission_version,
                },
                "valid_from": None if r.valid_from is None else r.valid_from.isoformat(),
                "extractor": None if r.extractor is None else r.extractor.to_dict(),
            }
            if all(
                (k == "key" and r.key.to_dict() == v) or (k != "key" and (item.get(k) == v or (k == "cue" and r.cue.value == v)))
                for k, v in flt.items()
            ):
                rows.append(item)
        return {"count": len(rows), "rows": rows}

    def op_complete_jobs(self, op: dict[str, Any]) -> Any:
        assert self.mem is not None
        rep = self.backend.complete_pending(self.mem.reviser)
        return {
            "completed": [k.to_dict() for k in rep.stamped],  # keys this run wrote a version for
            "skipped": [k.to_dict() for k in rep.skipped],  # keys left alone: a newer generation had completed them
            "jobs_done": rep.jobs_done, "keys_stamped": rep.keys_stamped, "skipped_newer": rep.skipped_newer,
        }

    def op_delete(self, op: dict[str, Any]) -> Any:
        assert self.mem is not None
        target = op["target"]
        requester = op.get("actor")
        try:
            reason = ErasureReason(op.get("reason", ErasureReason.ERASURE_REQUEST.value))
        except ValueError:
            reason = ErasureReason.OTHER
        tomb = self.mem.delete(target, reason, requester=requester)
        # the requester is stored only as a pseudonym: it is echoed here only when the stored pseudonym really is that
        # principal's (a check under the store secret), never read back from plain text
        actor = requester if requester is not None and self.mem.tombstone_requested_by(tomb, requester) else None
        return {
            "tombstone": {"id": tomb.report_id, "actor": actor, "reason": tomb.reason_class.value, "entry_hash": tomb.entry_hash},
            "erasure_report": {
                "report_id": tomb.report_id,
                "no_longer_reconstructable": [{"key_ref": k, "version": v} for k, v in tomb.affected_versions],
            },
        }

    def op_verify_log(self, op: dict[str, Any]) -> Any:
        res = self.backend.verify_log(op.get("from_lsn") or 1, op.get("to_lsn"))
        first = min((p.lsn for p in res.problems if p.lsn is not None), default=None)
        out: dict[str, Any] = {"ok": res.ok}
        if first is not None:
            out["first_bad_lsn"] = first
        out["rows"] = [{"lsn": r.lsn, "linked": r.linked, "content_verified": r.content_verified} for r in res.rows]
        return out

    def op_verify_beliefs(self, op: dict[str, Any]) -> Any:
        assert self.mem is not None
        res = self.backend.verify_beliefs(self.mem.reviser)
        return {"ok": res.ok, "keys": [p.key.to_dict() for p in res.problems if p.key is not None]}

    def op_recover(self, op: dict[str, Any]) -> Any:
        if op.get("point"):
            return NotImplemented
        return {"recovered": self.backend.recover().ok}

    def op_export_head(self, op: dict[str, Any]) -> Any:
        h = self.backend.export_head()
        return {"head_hash": h.entry_hash, "lsn": h.lsn}

    def op_subscribe(self, op: dict[str, Any]) -> Any:
        self.backend.subscribe(op["plan_id"], [Key.from_dict(k) for k in op["keys"]])
        return {"subscription_id": op["plan_id"]}

    def op_deliver(self, op: dict[str, Any]) -> Any:
        events = self.backend.pending_events()
        self._last_events = list(events)
        return {"events": [
            {"event_id": e.event_id, "plan_id": e.plan_id, "key": e.key.to_dict(), "old_version": e.old_version,
             "new_version": e.new_version} for e in events]}

    def op_ack(self, op: dict[str, Any]) -> Any:
        for e in self._last_events:
            self.backend.ack_event(e.event_id, e.plan_id)
        n = len(self._last_events)
        self._last_events = []
        return {"acked": n}


Implementation = MemoryImplementation

STATUS_FILE = Path(__file__).parent / "memory_status.json"


def current_status() -> dict[str, Any]:
    """Run the suite against this adapter and summarise. ``memory_status.json`` is the recorded baseline (a ratchet:
    see test_impl_memory).

    * ``pass``: scenario fixtures that pass (they need nothing outside the repo);
    * ``pass_needs_data``: harness checks that pass **because the frozen study data is present**;
    * ``needs_data``: harness checks skipped because that data (or the study checkout) is absent here;
    * ``fail``: failing fixtures with their first failure line; ``skip``: skipped for any other reason.

    The two ``*_needs_data`` states are one fact seen from two machines, so the baseline is stable with and without the
    data: regenerate it *with* the data (it then records ``pass_needs_data``); a machine without the data reports
    ``needs_data`` for the same ids and the ratchet accepts that, while a machine with the data demands they pass."""
    from . import checks
    from .runner import FAIL, PASS, SKIP, run_all

    out = run_all(MemoryImplementation())

    def needs_data(o: Any) -> bool:
        return o.status == SKIP and o.reason.startswith(checks.UNAVAILABLE_PREFIX)

    return {
        "implementation": MemoryImplementation.name,
        "pass": sorted(o.id for o in out if o.status == PASS and o.kind != "harness_check"),
        "pass_needs_data": sorted(o.id for o in out if o.status == PASS and o.kind == "harness_check"),
        "needs_data": sorted(o.id for o in out if needs_data(o)),
        "fail": {o.id: (o.failures[0] if o.failures else o.reason) for o in out if o.status == FAIL},
        "skip": {o.id: o.reason for o in out if o.status == SKIP and not needs_data(o)},
    }


if __name__ == "__main__":  # python -m tests.conformance.impl_memory  -> rewrite the recorded baseline
    STATUS_FILE.write_text(json.dumps(current_status(), indent=1, sort_keys=True) + "\n")
    print(f"wrote {STATUS_FILE}")
