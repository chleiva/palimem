"""``Memory``: the host-level core of the SDK (Lane M). Not the three-call agent facade: that sits on top of it.

``Memory`` wires the four stages into one object over a backend::

    append(report) -> log -> admission -> kernel -> belief versions        (write path, one store transaction)
    query(Query)   -> read_belief (generation barrier) -> policy.decide    (read path; nothing replays the log)

It is the **host API** of ``docs/API_TRUST_BOUNDARY.md``: the caller supplies ``source``, ``origin`` and ``actor``
and is trusted to have bound them from connector or session metadata. The agent-facing tool API (a separate task)
binds those fields itself and must never expose this class to an LLM.

Read paths use ``Backend.read_belief`` (the barrier), never ``current_belief``. Three *audit* methods
(:meth:`Memory.admitted`, :meth:`Memory.evidence`, :meth:`Memory.justification`) recompute from the admitted evidence
at a snapshot; they exist because some questions (the paper's yes/no slots, which need the set of admissible
interpretations) cannot be answered from stored segments, and for differential testing. They are not the serving path.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from palimem.admission import AdmissionConfig, Admitter, Evaluation, EvidenceSet
from palimem.engine import KernelReviser, Pipeline, StoreAdmitter, direct_entries
from palimem.kernel import (
    DerivedJustification,
    Justification,
    JustificationProvider,
    KernelSchema,
    ResourceLimitedResult,
    justify_derived,
)
from palimem.kernel.derive import Provider
from palimem.kernel.justify import Family
from palimem.policy import JUSTIFIED, DecisionContext, PolicyObject, decide
from palimem.store import (
    AppendResult,
    Backend,
    ErasureReason,
    InputKind,
    LimitedRead,
    NotReconstructable,
    StoreError,
    Tombstone,
)
from palimem.store.views import belief_view, select_segment
from palimem.types import (
    Answer,
    BeliefAsOf,
    BeliefView,
    Cue,
    ExplainMode,
    ExplainQuery,
    Explanation,
    Inference,
    Key,
    LogEntry,
    Origin,
    Query,
    Report,
    ResourceLimited,
    ResourceLimitedReason,
    Schema,
    SegmentBounds,
    SemanticConfig,
    Source,
    Support,
    canonical_json,
)
from palimem.types import Segment as PSegment
from palimem.types._codec import Value
from palimem.types.limits import DEFAULT_ENVIRONMENT_BUDGET

ChangeFrom = Callable[[Report], Value | None]


class NotReconstructableError(StoreError):
    """The version in force at the requested snapshot was redacted by an erasure (S-13). The contract has no
    ``Answer`` variant for it yet (open question Q2 of docs/STORAGE.md), so the host API raises."""

    def __init__(self, nr: NotReconstructable) -> None:
        super().__init__(f"belief of {nr.key.entity}/{nr.key.attr} version {nr.version} was erased")
        self.info = nr


# --------------------------------------------------------------------------- versioned-input payloads


def semantic_payload(semantic: SemanticConfig) -> dict[str, Any]:
    return semantic.to_dict()


def admission_payload(config: AdmissionConfig) -> dict[str, Any]:
    return {
        "admission_version": config.admission_version,
        "profile": config.profile.value,
        "rules": [r.to_dict() for r in config.rules],
        "class_status": {k: v.value for k, v in sorted(config.class_status.items())},
        "source_status": {k: v.value for k, v in sorted(config.source_status.items())},
        "acting_reports_must_be_live": config.acting_reports_must_be_live,
    }


def policy_payload(policy: PolicyObject) -> dict[str, Any]:
    return {
        "version": policy.version,
        "name": policy.name,
        "priors": dict(sorted(policy.priors.items())),
        "abstain_threshold": policy.abstain_threshold,
        "ask_threshold": policy.ask_threshold,
        "utility": dict(sorted(policy.utility.items())),
        "selector": policy.selector.value,
    }


class _LazyProvider:
    """Audit-path provider: base keys justified on demand from the admitted evidence of one snapshot."""

    def __init__(self, mem: Memory, ks: KernelSchema, direct: Mapping[Key, list[LogEntry]]) -> None:
        self.mem, self.ks, self.direct = mem, ks, direct
        self.cache: dict[Key, Justification] = {}

    def base(self, key: Key) -> Justification:
        hit = self.cache.get(key)
        if hit is None:
            j = self.mem.pipeline.justify_base(self.ks, key, self.direct.get(key, []))
            if isinstance(j, ResourceLimitedResult):
                raise RuntimeError(f"resource limited: {j.detail}")
            hit = self.cache[key] = j
        return hit

    def candidates(self, key: Key, t: int) -> Family:
        return self.base(key).candidates_at(t)

    def breakpoints(self, key: Key) -> frozenset[int]:
        return self.base(key).breakpoints()


class Memory:
    """The host-level memory core over one backend."""

    def __init__(
        self,
        backend: Backend,
        schema: Schema,
        *,
        semantic: SemanticConfig,
        admission: AdmissionConfig,
        policy: PolicyObject = JUSTIFIED,
        kernel_schema: KernelSchema | None = None,
        entities: Sequence[str] | None = None,
        budget: int = DEFAULT_ENVIRONMENT_BUDGET,
        change_from_of: ChangeFrom | None = None,
        admitter_class: type[Admitter] = Admitter,
    ) -> None:
        if admission.profile is not semantic.profile:
            raise ValueError("admission profile and semantic profile must agree")
        self.backend = backend
        self.schema = schema
        self.semantic = semantic
        self.policy = policy
        self._admitter_class = admitter_class
        ks = kernel_schema if kernel_schema is not None else KernelSchema.from_schema(schema, profile=semantic.profile)
        self.pipeline = Pipeline(
            schema=schema, kernel_schema=ks, semantic=semantic, admitter=admitter_class(admission, schema),
            entities=entities, budget=budget, change_from_of=change_from_of,
        )
        self.pipeline.bind(backend)
        self._admitter = StoreAdmitter(self.pipeline)
        self.reviser = KernelReviser(self.pipeline)
        self._semantic_version = 1
        self._ensure_inputs(admission)

    # ------------------------------------------------------------------ configuration (versioned inputs)

    @property
    def profile(self) -> Any:
        return self.semantic.profile

    @property
    def admission(self) -> AdmissionConfig:
        return self.pipeline.admitter.config

    def _ensure_inputs(self, admission: AdmissionConfig) -> None:
        be = self.backend
        stored = be.schema()
        if stored is None:
            be.put_schema(self.schema)
        elif stored != self.schema:
            if stored.version >= self.schema.version:
                raise ValueError("schema differs from the stored one; give it a higher version")
            be.put_schema(self.schema)
        self._put_if_new(InputKind.SEMANTIC, semantic_payload(self.semantic), None)
        found = be.input_at(InputKind.SEMANTIC)
        assert found is not None
        self._semantic_version = found[0]
        self._put_if_new(InputKind.ADMISSION, admission_payload(admission), admission.admission_version)
        self._put_if_new(InputKind.POLICY, policy_payload(self.policy), self.policy.version)

    def _put_if_new(self, kind: InputKind, payload: dict[str, Any], version: int | None) -> None:
        found = self.backend.input_at(kind)
        if found is not None and canonical_json(dict(found[1])) == canonical_json(payload):
            return
        nxt = (found[0] + 1) if found is not None else 1
        v = nxt if version is None else version
        if found is not None and v <= found[0]:
            raise ValueError(f"{kind.value} input differs from the stored one; give it a higher version")
        self.backend.put_input(kind, v, payload)

    def set_admission(self, config: AdmissionConfig) -> None:
        """A changed admission configuration is a new admission version; it governs appends from the next one."""
        if config.profile is not self.semantic.profile:
            raise ValueError("admission profile and semantic profile must agree")
        self._put_if_new(InputKind.ADMISSION, admission_payload(config), config.admission_version)
        self.pipeline.set_admitter(self._admitter_class(config, self.schema))

    def set_policy(self, policy: PolicyObject) -> None:
        self._put_if_new(InputKind.POLICY, policy_payload(policy), policy.version)
        self.policy = policy

    # ------------------------------------------------------------------ write path

    def _check_report(self, report: Report) -> None:
        try:
            attr = self.schema.attr(report.key.attr)
        except KeyError:
            raise ValueError(f"attribute {report.key.attr!r} is not declared in the schema") from None
        if attr.attr_class.value == "derived" and report.cue in (Cue.ASSERT, Cue.CHANGE, Cue.CORRECT):
            raise ValueError(f"attribute {attr.name!r} is derived: it is never asserted directly")

    def append(self, report: Report, *, idempotency_key: str | None = None) -> AppendResult:
        """Append one report (host API). Give an ``idempotency_key`` to make a retry after a crash safe; without
        one every call is a new report. Unfinished completion jobs are run afterwards, so a read never sees a
        stale key that the host could have repaired."""
        self._check_report(report)
        key = idempotency_key or uuid.uuid4().hex
        res = self.backend.append(report, idempotency_key=key, admitter=self._admitter, reviser=self.reviser)
        self.complete()
        return res

    def withdraw(
        self, target_id: str, *, source: Source, actor: str, origin_group: str | None = None,
        origin: Origin = Origin.EXTERNAL_OBSERVATION, idempotency_key: str | None = None,
    ) -> AppendResult:
        """Append a ``withdraw`` of ``target_id`` (authority is checked by admission; a failure lands as ``allege``)."""
        t = self.backend.get_entry(target_id)
        if t is None or isinstance(t, Tombstone):
            raise LookupError(f"unknown or erased report {target_id}")
        rep = Report(
            key=t.report.key, cue=Cue.WITHDRAW, target=target_id, source=source, origin=origin,
            origin_group=origin_group or source.id, actor=actor,
        )
        return self.append(rep, idempotency_key=idempotency_key)

    def delete(self, report_id: str, reason: ErasureReason = ErasureReason.ERASURE_REQUEST) -> Tombstone:
        """Erase a report (GDPR-style): content, raw reference and values derived only from it, with dependency
        repair; a tombstone keeps the hash chain verifiable (S-13)."""
        self.pipeline.invalidate()
        try:
            return self.backend.erase(report_id, reason, reviser=self.reviser)
        finally:
            self.pipeline.invalidate()

    def complete(self) -> None:
        """Run pending completion jobs (cheap when there are none)."""
        self.backend.complete_pending(self.reviser)

    # ------------------------------------------------------------------ read path

    def _head_lsn(self) -> int:
        return self.backend.head().lsn

    def lsn_of(self, as_of: BeliefAsOf | None) -> int:
        """Resolve a ``belief_as_of`` (LSN, timestamp or ``None`` = now) to a log position (S-05)."""
        if as_of is None:
            return self._head_lsn()
        if isinstance(as_of, int):
            return min(as_of, self._head_lsn())
        return self.backend.lsn_at(as_of)

    def read(self, key: Key, as_of: BeliefAsOf | None = None) -> Any:
        """The serving read under the generation barrier (:meth:`Backend.read_belief`)."""
        return self.backend.read_belief(key, as_of)

    def _virtual_view(self, key: Key, valid_at: datetime | None, as_of: BeliefAsOf | None) -> BeliefView:
        """A key with no log history at the snapshot is justified by an empty evidence set. The contract needs a
        ``BeliefView`` with a version, so it carries a *virtual* version 1 (ref ``virtual:...``), never stored."""
        ev = self.pipeline.evaluate(self.lsn_of(as_of))
        ks = self.pipeline.kernel_schema(ev)
        if ks.spec(key.attr).derived:
            segs: tuple[PSegment, ...] = justify_derived(ks, key, JustificationProvider({}), self.semantic).segments()
        else:
            j = self.pipeline.justify_base(ks, key, [])
            assert not isinstance(j, ResourceLimitedResult)
            segs = j.segments()
        seg = select_segment(segs, valid_at)
        assert seg is not None
        return BeliefView(
            key=key, version=1, required_generation=0, completed_generation=0, inference=Inference(complete=True),
            segment=seg, ref=f"virtual:{key.entity}:{key.attr}",
        )

    def _view_for(self, key: Key, valid_at: datetime | None, as_of: BeliefAsOf | None) -> BeliefView | ResourceLimited:
        try:
            self.schema.attr(key.attr)
        except KeyError:
            raise ValueError(f"attribute {key.attr!r} is not declared in the schema") from None
        r = self.backend.read_belief(key, as_of)
        if isinstance(r, NotReconstructable):
            raise NotReconstructableError(r)
        if isinstance(r, LimitedRead):
            raw = self.backend.belief_at(key, as_of if as_of is not None else self._head_lsn())
            if raw is not None and not raw.inference.complete and (raw.inference.reason or "").startswith("environment_budget"):
                last = r.to_answer(valid_at).last_complete
                return ResourceLimited(
                    reason=ResourceLimitedReason.ENVIRONMENT_BUDGET, required_generation=raw.required_generation,
                    completed_generation=raw.completed_generation, reason_key=key, last_complete=last,
                )
            return r.to_answer(valid_at)
        if r is None:
            return self._virtual_view(key, valid_at, as_of)
        v = belief_view(r, valid_at)
        assert v is not None
        return v

    def query(self, q: Query) -> Answer:
        """``query(key, valid_at, belief_as_of) -> Resolved | ResourceLimited`` (output contract v2)."""
        if q.profile is not self.semantic.profile:
            raise ValueError(f"query profile {q.profile.value!r} does not match the memory's {self.semantic.profile.value!r}")
        view = self._view_for(q.key, q.valid_at, q.belief_as_of)
        if isinstance(view, ResourceLimited):
            return view
        ctx = None
        if view.segment.kernel_status.value == "unresolved":
            ctx = DecisionContext.from_entries(self.pipeline.log.entries(upto_lsn=self.lsn_of(q.belief_as_of)))
        return decide(view, self.policy, ctx)

    def explain(self, q: ExplainQuery) -> Explanation:
        """Subset-minimal environments for the answered segment (S-12). Supports are task T-B4: until they exist
        the list is empty and the explanation says so by carrying no environments."""
        view = self._view_for(q.key, q.valid_at, q.belief_as_of)
        if isinstance(view, ResourceLimited):
            raise StoreError(f"cannot explain: {view.reason.value}")
        sup: tuple[Support, ...] = tuple(s for cid in sorted(view.segment.support) for s in view.segment.support[cid])
        if q.mode is ExplainMode.ONE:
            sup = sup[:1]
        return Explanation(
            key=q.key, segment=SegmentBounds(valid_from=view.segment.valid_from, valid_to=view.segment.valid_to),
            mode=q.mode, depth=q.depth, environments=sup,
        )

    # ------------------------------------------------------------------ audit paths (recompute from the log)

    def evaluation(self, as_of: BeliefAsOf | None = None) -> Evaluation:
        """Admission over the log prefix at a snapshot, under the current admission config."""
        return self.pipeline.evaluate(self.lsn_of(as_of))

    def admitted(self, as_of: BeliefAsOf | None = None) -> dict[str, LogEntry]:
        """report id -> entry for every report the kernel may read at the snapshot (all keys)."""
        out: dict[str, LogEntry] = {}
        for es in direct_entries(self.evaluation(as_of)).values():
            for e in es:
                assert e.report.id is not None
                out[e.report.id] = e
        return out

    def evidence(self, key: Key, as_of: BeliefAsOf | None = None) -> EvidenceSet:
        ev = self.evaluation(as_of)
        return self.pipeline.admitter.evidence_set_of(ev, key)

    def justification(
        self, key: Key, as_of: BeliefAsOf | None = None
    ) -> Justification | DerivedJustification | ResourceLimitedResult:
        """Re-justify ``key`` from the admitted evidence at a snapshot (kernel replay: the interpretation sets
        that yes/no questions need)."""
        ev = self.evaluation(as_of)
        ks = self.pipeline.kernel_schema(ev)
        direct = direct_entries(ev)
        if not ks.spec(key.attr).derived:
            return self.pipeline.justify_base(ks, key, direct.get(key, []))
        prov: Provider = _LazyProvider(self, ks, direct)
        return justify_derived(ks, key, prov, self.semantic)

    # ------------------------------------------------------------------ plumbing

    def with_policy(self, policy: PolicyObject) -> Memory:
        """A view of the same stored state under another policy preset (the kernel's answer is invariant under policy)."""
        m = replace_memory(self)
        m.policy = policy
        return m

    def close(self) -> None:
        self.backend.close()


def replace_memory(m: Memory) -> Memory:
    """Shallow copy sharing backend, pipeline and reviser."""
    c = Memory.__new__(Memory)
    c.__dict__.update(m.__dict__)
    return c


__all__ = ["Memory", "NotReconstructableError", "admission_payload", "policy_payload", "semantic_payload"]
