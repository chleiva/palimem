"""Random *valid* instances of every palimem contract type (stdlib ``random`` only).

Every generator takes a ``random.Random`` so tests are reproducible from a seed. The instances are
valid by construction; the tests check that the constructor accepts them, that they round-trip
through canonical JSON, and that their ``to_dict()`` validates against the committed schemas.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from palimem.types import (
    AdmissionOutcome,
    AdmissionReason,
    AdmissionRecord,
    Attr,
    AttrClass,
    AuthorityRule,
    AuthorityTable,
    Belief,
    BeliefOfForm,
    BeliefOfProp,
    BeliefView,
    Candidate,
    CandidateForm,
    Completeness,
    CompletenessMode,
    CompletenessScope,
    Cue,
    Decision,
    Dependency,
    EmptyForm,
    EnumerationProp,
    ExplainMode,
    ExplainQuery,
    Explanation,
    ExplanationState,
    Extractor,
    Inference,
    Inquiry,
    Interval,
    InvalidatedBy,
    InvalidatedKind,
    KernelStatus,
    Key,
    KeyScope,
    LastComplete,
    LogEntry,
    MemberProp,
    MergeMarker,
    MergeOp,
    MergeRecord,
    NotMemberForm,
    NotMemberProp,
    NotValueForm,
    NotValueProp,
    Origin,
    Pin,
    PolicyInfo,
    Power,
    Precision,
    Profile,
    Proposition,
    Query,
    Report,
    Resolved,
    ResolverInfo,
    ResourceLimited,
    ResourceLimitedReason,
    Rule,
    RuleFired,
    Schema,
    Segment,
    SegmentBounds,
    SetForm,
    Source,
    Support,
    Targets,
    ValueForm,
    ValueProp,
    ValueType,
    Versions,
    Who,
    WhoKind,
)
from palimem.types.authority import AGENT_CLASS_ORIGINS

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_WORDS = ["acme", "globex", "paris", "london", "alice", "bob", "x1", "ünï", "a b", 'q"t', "z\\"]
_BASE = datetime(2026, 1, 1, tzinfo=UTC)


def ulid(rng: random.Random) -> str:
    return "".join(rng.choice(_CROCKFORD) for _ in range(26))


def ts(rng: random.Random) -> datetime:
    return _BASE + timedelta(seconds=rng.randrange(0, 365 * 86400), microseconds=rng.choice([0, 0, rng.randrange(1_000_000)]))


def increasing_ts(rng: random.Random, n: int) -> list[datetime]:
    t = _BASE
    out = []
    for _ in range(n):
        t = t + timedelta(seconds=rng.randrange(1, 10 * 86400), microseconds=rng.choice([0, rng.randrange(1000)]))
        out.append(t)
    return out


def maybe(rng: random.Random, v: Any, p: float = 0.5) -> Any:
    return v if rng.random() < p else None


def value(rng: random.Random) -> Any:
    kind = rng.randrange(5)
    if kind == 0:
        return rng.choice(_WORDS)
    if kind == 1:
        return rng.randrange(-5, 1000)
    if kind == 2:
        return round(rng.uniform(-100, 100), 3)
    if kind == 3:
        return rng.random() < 0.5
    return f"v{rng.randrange(50)}"


def key(rng: random.Random) -> Key:
    return Key(entity=rng.choice(["alice", "bob", "acme", "e:42"]), attr=rng.choice(["employer", "city", "affiliations", "hq_city"]))


def plain_proposition(rng: random.Random) -> Proposition:
    k = rng.randrange(5)
    if k == 0:
        return ValueProp(value=value(rng))
    if k == 1:
        return MemberProp(value=value(rng))
    if k == 2:
        return NotMemberProp(value=value(rng))
    if k == 3:
        return NotValueProp(value=value(rng))
    return EnumerationProp(values=tuple(value(rng) for _ in range(rng.randrange(0, 4))))


def proposition(rng: random.Random) -> Proposition:
    if rng.random() < 0.2:
        return BeliefOfProp(holder=rng.choice(["alice", "bob"]), proposition=plain_proposition(rng))
    return plain_proposition(rng)


def candidate_form(rng: random.Random) -> CandidateForm:
    k = rng.randrange(6)
    if k == 0:
        return ValueForm(value=value(rng))
    if k == 1:
        return SetForm(values=tuple(value(rng) for _ in range(rng.randrange(1, 4))))
    if k == 2:
        return EmptyForm()
    if k == 3:
        return NotValueForm(value=value(rng))
    if k == 4:
        return NotMemberForm(value=value(rng))
    return BeliefOfForm(holder="alice", proposition=plain_proposition(rng))


def candidate(rng: random.Random, k: Key | None = None) -> Candidate:
    return Candidate(key=k or key(rng), form=candidate_form(rng))


def support(rng: random.Random) -> Support:
    lo, hi = sorted([ts(rng), ts(rng)])
    return Support(
        environment=tuple(ulid(rng) for _ in range(rng.randrange(1, 4))),
        valid_from=maybe(rng, lo), valid_to=maybe(rng, hi),
        precision=rng.choice(list(Precision)),
    )


def _distinct_candidates(rng: random.Random, k: Key, n: int, forms: list[CandidateForm] | None = None) -> list[Candidate]:
    out: dict[str, Candidate] = {}
    while len(out) < n:
        c = Candidate(key=k, form=rng.choice(forms) if forms else candidate_form(rng))
        out[c.id] = c
    return list(out.values())


def _support_for(rng: random.Random, cands: list[Candidate]) -> dict[str, tuple[Support, ...]]:
    return {c.id: tuple(support(rng) for _ in range(rng.randrange(1, 3))) for c in cands if rng.random() < 0.7}


def segment(rng: random.Random, k: Key | None = None, lo: datetime | None = None, hi: datetime | None = None) -> Segment:
    k = k or key(rng)
    status = rng.choice(list(KernelStatus))
    sup_kwargs: dict[str, Any] = {}
    if status is KernelStatus.ESTABLISHED:
        form = rng.choice([ValueForm(value=value(rng)), SetForm(values=(value(rng),)), BeliefOfForm(holder="b", proposition=ValueProp(value=1))])
        est, alts = Candidate(key=k, form=form), ()
    elif status is KernelStatus.ESTABLISHED_EMPTY:
        est, alts = Candidate(key=k, form=EmptyForm()), ()
    elif status is KernelStatus.ESTABLISHED_FALSE:
        est, alts = Candidate(key=k, form=rng.choice([NotValueForm(value=value(rng)), NotMemberForm(value=value(rng))])), ()
    elif status is KernelStatus.UNRESOLVED:
        est, alts = None, tuple(_distinct_candidates(rng, k, rng.randrange(2, 4)))
    else:
        est, alts = None, ()
    cands = ([est] if est else []) + list(alts)
    if cands:
        sup_kwargs["support"] = _support_for(rng, cands)
    return Segment(valid_from=lo, valid_to=hi, kernel_status=status, established=est, alternatives=alts, **sup_kwargs)


def inference(rng: random.Random, complete: bool) -> Inference:
    return Inference(complete=True) if complete else Inference(complete=False, reason=rng.choice(["traversal budget", "restart"]))


def generations(rng: random.Random) -> tuple[int, int, bool]:
    """(required, completed, complete): complete iff completed == required (and completed <= required)."""
    if rng.random() < 0.6:
        req = rng.randrange(0, 20)
        return req, req, True
    req = rng.randrange(1, 20)
    return req, rng.randrange(0, req), False


def belief(rng: random.Random) -> Belief:
    k = key(rng)
    n = rng.randrange(0, 4)
    cuts = increasing_ts(rng, n)
    bounds = [None, *cuts, None]
    segs = tuple(segment(rng, k, bounds[i], bounds[i + 1]) for i in range(n + 1)) if n else ()
    req, comp, complete = generations(rng)
    return Belief(
        key=k, version=rng.randrange(1, 9), lsn=rng.randrange(1, 100), required_generation=req, completed_generation=comp,
        segments=segs,
        pinned=tuple(Pin(report_id=ulid(rng), admission_version=rng.randrange(1, 4)) for _ in range(rng.randrange(0, 4))),
        depends_on=tuple(Dependency(key=Key(entity="zz", attr=f"d{i}"), version=rng.randrange(1, 4)) for i in range(rng.randrange(0, 3))),
        invalidated_by=maybe(rng, InvalidatedBy(kind=rng.choice(list(InvalidatedKind)), id=ulid(rng))),
        versions=Versions(schema=rng.randrange(1, 4), semantic=rng.randrange(1, 4), admission=rng.randrange(1, 4)),
        inference=inference(rng, complete), recorded_at=ts(rng),
    )


def belief_view(rng: random.Random) -> BeliefView:
    k = key(rng)
    req, comp, complete = generations(rng)
    return BeliefView(
        key=k, version=rng.randrange(1, 9), required_generation=req, completed_generation=comp,
        inference=inference(rng, complete), segment=segment(rng, k), ref=f"belief:{k.entity}/{k.attr}@{rng.randrange(1, 9)}",
    )


def query(rng: random.Random) -> Query:
    return Query(
        key=key(rng), valid_at=maybe(rng, ts(rng)), belief_as_of=rng.choice([None, rng.randrange(0, 1000), ts(rng)]),
        profile=rng.choice(list(Profile)), explanation_budget=maybe(rng, rng.randrange(0, 10)),
    )


def explain_query(rng: random.Random) -> ExplainQuery:
    return ExplainQuery(
        key=key(rng), valid_at=maybe(rng, ts(rng)), belief_as_of=rng.choice([None, rng.randrange(0, 1000), ts(rng)]),
        mode=rng.choice(list(ExplainMode)), depth=maybe(rng, rng.randrange(1, 5)),
    )


def segment_bounds(rng: random.Random) -> SegmentBounds:
    a, b = sorted(increasing_ts(rng, 2))
    return SegmentBounds(valid_from=maybe(rng, a), valid_to=maybe(rng, b))


def explanation(rng: random.Random) -> Explanation:
    mode = rng.choice(list(ExplainMode))
    n = rng.randrange(0, 2 if mode is ExplainMode.ONE else 4)
    return Explanation(
        key=key(rng), segment=segment_bounds(rng), mode=mode, depth=maybe(rng, rng.randrange(1, 4)),
        state=rng.choice(list(ExplanationState)), environments=tuple(support(rng) for _ in range(n)),
    )


def resolved(rng: random.Random) -> Resolved:
    view = belief_view(rng)
    seg = view.segment
    cands = ([seg.established] if seg.established else []) + list(seg.alternatives)
    decision = rng.choice(list(Decision))
    if decision is Decision.COMMIT and not cands:
        decision = Decision.ABSTAIN
    assertion = rng.choice(cands) if decision is Decision.COMMIT else None
    inquiry = None
    if decision is Decision.ASK:
        pool = cands or [Candidate(key=view.key, form=ValueForm(value=1))]
        inquiry = Inquiry(competing=tuple(pool), missing=(key(rng),) if rng.random() < 0.5 else (), resolvers=("trusted",) if rng.random() < 0.5 else ())
    return Resolved(
        segment=SegmentBounds(valid_from=seg.valid_from, valid_to=seg.valid_to), kernel_status=seg.kernel_status, decision=decision,
        assertion=assertion, alternatives=tuple(c for c in cands if rng.random() < 0.5),
        provenance=tuple(support(rng) for _ in range(rng.randrange(0, 3))), explanation=rng.choice(list(ExplanationState)),
        policy=PolicyInfo(version=rng.randrange(1, 4), rule_fired=rng.choice(list(RuleFired))),
        confidence=maybe(rng, round(rng.random(), 3)), inquiry=inquiry, justified=view,
    )


def resource_limited(rng: random.Random) -> ResourceLimited:
    reason = rng.choice(list(ResourceLimitedReason))
    req = rng.randrange(1, 20)
    comp = req if reason is ResourceLimitedReason.ENVIRONMENT_BUDGET and rng.random() < 0.5 else rng.randrange(0, req)
    if reason is ResourceLimitedReason.STORE_DIRTY and rng.random() < 0.3:
        comp = req
    keyed = reason in (ResourceLimitedReason.STALE_DEPENDENCY, ResourceLimitedReason.ENVIRONMENT_BUDGET)
    last = None
    if rng.random() < 0.4:
        last = LastComplete(belief_as_of=rng.choice([rng.randrange(0, 100), ts(rng)]), view=belief_view(rng))
    return ResourceLimited(reason=reason, reason_key=key(rng) if keyed else None, required_generation=req, completed_generation=comp, last_complete=last)


def answer(rng: random.Random) -> Resolved | ResourceLimited:
    return resolved(rng) if rng.random() < 0.7 else resource_limited(rng)


def source(rng: random.Random) -> Source:
    return Source(id=rng.choice(["registry", "press", "wire", "chat"]), cls=rng.choice(["trusted", "standard", "low", "quarantined"]))


def principal(rng: random.Random) -> str:
    return f"{rng.choice(['agent', 'user', 'connector', 'system'])}:{rng.choice(['a', 'planner', 'alice', 'registry', 'x.y'])}"


def report(rng: random.Random) -> Report:
    cue = rng.choice(list(Cue))
    origin = rng.choice(list(Origin))
    prop: Proposition | None
    if cue is Cue.WITHDRAW:
        prop = None
    elif cue in (Cue.DISPUTE, Cue.ALLEGE):
        prop = maybe(rng, proposition(rng))
    else:
        prop = proposition(rng)
    if origin is Origin.ATTRIBUTED and prop is not None and not isinstance(prop, BeliefOfProp):
        prop = BeliefOfProp(holder="bob", proposition=prop if not isinstance(prop, BeliefOfProp) else ValueProp(value=1))
    target = ulid(rng) if cue in (Cue.CORRECT, Cue.WITHDRAW, Cue.DISPUTE, Cue.ALLEGE) else None
    lo, hi = sorted([ts(rng), ts(rng)])
    return Report(
        id=maybe(rng, ulid(rng), 0.7), key=key(rng), proposition=prop, cue=cue, target=target, source=source(rng), origin=origin,
        origin_group=rng.choice(["g1", "g2", "mirror-3"]), actor=principal(rng), observed_at=maybe(rng, ts(rng)),
        valid_from=maybe(rng, lo), valid_to=maybe(rng, hi), precision=rng.choice(list(Precision)),
        raw_ref=maybe(rng, f"raw://{rng.randrange(1000)}"),
        extractor=maybe(rng, Extractor(model="m", version="1", prompt_hash=f"{rng.randrange(1 << 32):08x}")),
    )


def log_entry(rng: random.Random) -> LogEntry:
    r = report(rng)
    r = replace(r, id=ulid(rng)) if r.id is None else r
    return LogEntry(
        lsn=rng.randrange(1, 10_000), recorded_at=ts(rng), report=r,
        prev_hash=maybe(rng, f"{rng.getrandbits(256):064x}"), entry_hash=maybe(rng, f"{rng.getrandbits(256):064x}"),
    )


def who(rng: random.Random, allow_origin_group: bool = True) -> Who:
    kinds = [WhoKind.ANY, WhoKind.TARGET_SOURCE, WhoKind.TARGET_ACTOR, WhoKind.PRINCIPAL]
    if allow_origin_group:
        kinds += [WhoKind.TARGET_ORIGIN_GROUP, WhoKind.ORIGIN_GROUP]
    k = rng.choice(kinds)
    if k is WhoKind.PRINCIPAL:
        return Who(kind=k, value=f"{rng.choice(['user', 'connector', 'system'])}:{rng.choice(['a', 'b'])}")
    if k is WhoKind.ORIGIN_GROUP:
        return Who(kind=k, value=rng.choice(["g1", "g2"]))
    return Who(kind=k)


REPORT_POWERS = [p for p in Power if p is not Power.MERGE]  # `merge` is granted on its own, by identity


def authority_rule(rng: random.Random, allow_origin_group: bool = True) -> AuthorityRule:
    if rng.random() < 0.2:  # an agent grant that satisfies the S-07 invariant
        return AuthorityRule(
            who=Who(kind=WhoKind.PRINCIPAL, value=f"agent:{rng.choice(['a', 'planner'])}"),
            may=tuple(rng.sample(REPORT_POWERS, rng.randrange(1, 4))),
            over_origins=tuple(rng.sample(sorted(AGENT_CLASS_ORIGINS, key=lambda o: o.value), 2)),
        )
    if rng.random() < 0.15:  # a merge grant: by identity, on its own
        return AuthorityRule(
            who=rng.choice([Who(kind=WhoKind.ANY), Who(kind=WhoKind.PRINCIPAL, value=f"{rng.choice(['user', 'system', 'connector'])}:ops")]),
            may=(Power.MERGE,), on=KeyScope(attr=rng.choice(["*", "__entity_merge__"]), entity=rng.choice(["*", "acme*"])),
        )
    return AuthorityRule(
        who=who(rng, allow_origin_group), may=tuple(rng.sample(REPORT_POWERS, rng.randrange(1, 4))),
        on=KeyScope(attr=rng.choice(["*", "emp*", "city"]), entity=rng.choice(["*", "alice", "e:*"])),
        targets=rng.choice(list(Targets)),
        over_origins=maybe(rng, tuple(rng.sample(list(Origin), rng.randrange(1, 4)))),
    )


def authority_table(rng: random.Random) -> AuthorityTable:
    return AuthorityTable(admission_version=rng.randrange(1, 9), rules=tuple(authority_rule(rng) for _ in range(rng.randrange(0, 4))))


def attr(rng: random.Random, name: str | None = None, cls: AttrClass | None = None) -> Attr:
    cls = cls or rng.choice(list(AttrClass))
    name = name or rng.choice(["employer", "city", "affiliations", "hq_city", "work_city"])
    mode = rng.choice(list(CompletenessMode))
    scope = None
    if mode is CompletenessMode.DECLARED:
        lo, hi = sorted([ts(rng), ts(rng)])
        scope = CompletenessScope(
            source_classes=maybe(rng, tuple(rng.sample(["trusted", "standard", "low"], 2))),
            interval=maybe(rng, Interval(start=maybe(rng, lo), end=maybe(rng, hi))),
        )
    rules = tuple(AuthorityRule(
        who=who(rng), may=tuple(rng.sample(REPORT_POWERS, rng.randrange(1, 3))), on=KeyScope(attr=rng.choice(["*", name[:2] + "*", name])),
    ) for _ in range(rng.randrange(0, 3)))
    return Attr(
        name=name, attr_class=cls, value_type=rng.choice(list(ValueType)), inertia=rng.random() < 0.5,
        rule=Rule(reads=("r1", "r2")[: rng.randrange(1, 3)], fn="f(x) <- g(x)") if cls is AttrClass.DERIVED else None,
        completeness=Completeness(mode=mode, scope=scope), authority=rules,
    )


def schema(rng: random.Random) -> Schema:
    base = [attr(rng, "r1", AttrClass.SINGLE_STABLE), attr(rng, "r2", AttrClass.SINGLE_STABLE)]
    extra = [attr(rng, f"x{i}") for i in range(rng.randrange(0, 3))]
    return Schema(version=rng.randrange(1, 5), attrs=tuple(base + extra))


def admission_record(rng: random.Random) -> AdmissionRecord:
    outcome = rng.choice(list(AdmissionOutcome))
    reasons = {
        AdmissionOutcome.ADMISSIBLE: [AdmissionReason.ADMITTED, AdmissionReason.CONFIRMED],
        AdmissionOutcome.QUARANTINED: [AdmissionReason.SOURCE_QUARANTINED],
        AdmissionOutcome.EXCLUDED: [AdmissionReason.ORIGIN_NOT_ADMISSIBLE, AdmissionReason.SOURCE_BLOCKED, AdmissionReason.AUTHORITY_FAILED, AdmissionReason.TARGET_MISSING],
    }
    reason = rng.choice(reasons[outcome])
    confirmed = tuple(ulid(rng) for _ in range(rng.randrange(1, 3))) if reason is AdmissionReason.CONFIRMED else ()
    return AdmissionRecord(id=ulid(rng), report_id=ulid(rng), outcome=outcome, reason=reason, admission_version=rng.randrange(1, 9), confirmed_by=confirmed)


def resolver_info(rng: random.Random) -> ResolverInfo:
    return ResolverInfo(
        method=rng.choice(["manual", "lexical", "embedding"]), score=maybe(rng, round(rng.random(), 4)),
        version=maybe(rng, rng.choice(["1", "2"])),
    )


def merge_marker(rng: random.Random) -> MergeMarker:
    if rng.random() < 0.5:
        return MergeMarker(op=MergeOp.MERGE, into=rng.choice(["acme", "globex inc"]), reason="same entity", resolver=resolver_info(rng))
    return MergeMarker(op=MergeOp.UNMERGE, target=ulid(rng), reason="false merge", resolver=resolver_info(rng))


def merge_record(rng: random.Random) -> MergeRecord:
    members = tuple(sorted(rng.sample(["acme", "acme inc", "acme corp", "globex", "globex inc"], rng.randrange(2, 5))))
    rid = ulid(rng)
    reversed_by = maybe(rng, ulid(rng))
    if reversed_by == rid:
        reversed_by = None
    return MergeRecord(
        id=rid, members=members, representative=rng.choice(members), reason="same entity", resolver=resolver_info(rng),
        admission_version=rng.randrange(1, 9), reversed_by=reversed_by,
    )


def candidate_any(rng: random.Random) -> Candidate:
    return candidate(rng)


GENERATORS: dict[str, Any] = {
    "report": report,
    "proposition": proposition,
    "attr": attr,
    "schema": schema,
    "candidate": candidate_any,
    "support": support,
    "segment": segment,
    "belief": belief,
    "belief_view": belief_view,
    "query": query,
    "answer": answer,
    "log_entry": log_entry,
    "admission_record": admission_record,
    "merge_marker": merge_marker,
    "merge_record": merge_record,
    "authority_rule": authority_rule,
    "authority_table": authority_table,
    "explain_query": explain_query,
    "explanation": explanation,
}
