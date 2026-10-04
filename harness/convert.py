"""Study stream -> palimem contract objects, plus the compat-profile admission stub (Lane B).

``to_converted(stream)`` turns a study ``Stream`` (``revise_stream.model``) into

* a :class:`~palimem.kernel.KernelSchema` built directly from the study's attribute table (the contract
  ``Attr`` cannot express three of the paper's attribute kinds; see ``palimem.kernel.spec``),
* palimem ``LogEntry`` objects, one per representable study observation, with ``lsn`` = the observation's
  arrival index (1-based, over *all* study observations) and ``recorded_at`` = the study report day,
* side tables the contract does not carry: source-level retractions and the ``from`` value of change cues.

``CompatAdmission`` is the *temporary* admission stage of the ``revise-stream-v1`` profile: it reproduces
``Stream.admitted(tau)`` (blocked sources, report withdrawal, source withdrawal, A-SELF same-origin
self-correction) over palimem log entries, so that the kernel can be compared with the study's gold. The
real admission stage is task T-D1/T-D2; this stub exists so G1 parity can be measured before it lands.
It deliberately reproduces the paper's "retracted-correction quirk" (a withdrawn correction still
withdraws its target; S-02 flag ``a_self_requires_live_correction: false``).
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from palimem.kernel import AttrSpec, KernelSchema, RuleSpec, dt_of_day
from palimem.types import (
    Cue,
    Key,
    LogEntry,
    MemberProp,
    Origin,
    Report,
    Source,
    ValueProp,
)
from palimem.types._codec import Value

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def make_ulid(ts_ms: int, n: int) -> str:
    """Deterministic ULID: 48-bit millisecond timestamp, 80-bit counter."""
    t = ts_ms & ((1 << 48) - 1)
    chars = []
    for shift in range(45, -1, -5):
        chars.append(_CROCKFORD[(t >> shift) & 31])
    r = n & ((1 << 80) - 1)
    for shift in range(75, -1, -5):
        chars.append(_CROCKFORD[(r >> shift) & 31])
    return "".join(chars)


@dataclass
class Converted:
    stream_id: str
    kschema: KernelSchema
    entries: list[LogEntry]
    arrival_ts: list[datetime]  # recorded_at of every log position in arrival order (index = lsn - 1)
    source_retractions: list[tuple[int, str]]  # (lsn, source id); only in "sidetable" mode
    blocked_sources: frozenset[str]
    change_from: dict[str, Value]
    study_id: dict[str, str]  # ulid -> study observation id
    ulid_of: dict[str, str]  # study observation id -> ulid
    stats: dict[str, int] = field(default_factory=dict)
    source_retract_mode: str = "expand"
    expanded_from: dict[str, str] = field(default_factory=dict)  # synthetic withdraw ulid -> study retract id

    def lsn_at(self, ts: datetime) -> int:
        """S-05: map a timestamp to the last LSN whose recorded_at is at or before it (0 = none)."""
        return bisect_right(self.arrival_ts, ts)

    def lsn_at_day(self, day: int) -> int:
        return self.lsn_at(dt_of_day(day))


def kernel_schema_of(stream: Any) -> KernelSchema:
    attrs = {
        name: AttrSpec(
            name=name,
            cardinality=sp.cardinality,
            changeable=sp.changeable,
            error_allowed=sp.error_allowed,
            competing_values=sp.competing_values,
            derived=sp.derived,
            inertia=True,  # the paper applies the law of inertia to every attribute (A4)
        )
        for name, sp in stream.attributes.items()
    }
    rules = tuple(
        RuleSpec(id=r.id, head=tuple(r.head), body=tuple(tuple(b) for b in r.body), kind=r.kind,
                 exceptions=tuple(tuple(x) for x in r.exceptions))
        for r in stream.rules
    )
    return KernelSchema(attrs=attrs, rules=rules, entities=tuple(stream.entities))


def to_converted(stream: Any, source_retract: str = "expand") -> Converted:
    """Convert a study stream.

    ``source_retract`` selects how the paper's *source-level* retraction (a ``retract`` whose target is a
    source id; the contract has no source-scope withdraw) is represented:

    * ``"expand"`` (default; the coordinator's instruction): one ``withdraw`` report per assertion that the
      source had already had ingested at that point (exactly the set ``palimpsest/core.py::_ingest`` recomputes).
      LSN is then the arrival index of the *expanded* log. Assertions the source makes **after** its
      retraction are not withdrawn, whereas the paper's ``Stream.admitted`` also drops them: a real
      semantic difference, measured and classified by ``kernel_diff``.
    * ``"sidetable"``: the retraction is kept in a harness-local side table and applied by
      :class:`CompatAdmission` to all of the source's assertions (the paper's behaviour exactly).
    """
    if source_retract not in ("expand", "sidetable"):
        raise ValueError(f"source_retract must be 'expand' or 'sidetable', got {source_retract!r}")
    kschema = kernel_schema_of(stream)
    obs = sorted(stream.observations, key=lambda o: o.t_rep)  # stable: arrival order within a day
    stats = {"observations": len(obs), "withdraw_reports": 0, "source_retractions": 0,
             "source_retract_expanded_withdraws": 0, "cross_key_corrections": 0}
    key_of: dict[str, Key] = {}
    for o in obs:
        if o.kind == "assert":
            if o.ctx:
                raise ValueError(f"{o.id}: non-empty ctx is not representable in Key(entity, attr)")
            key_of[o.id] = Key(entity=o.entity, attr=o.attr)
    # arrival items: (observation, expansion target or None); a sidetable source retraction keeps a
    # position (it occupies an LSN) but produces no log entry
    items: list[tuple[Any, Any | None]] = []
    ingested_by_source: dict[str, list[Any]] = {}
    for o in obs:
        if o.kind == "retract" and o.target in stream.sources:
            stats["source_retractions"] += 1
            if source_retract == "sidetable":
                items.append((o, None))
            else:
                for tgt in ingested_by_source.get(o.target, []):
                    items.append((o, tgt))
            continue
        items.append((o, None))
        if o.kind == "assert":
            ingested_by_source.setdefault(o.source, []).append(o)
    ulid_of: dict[str, str] = {}
    study_id: dict[str, str] = {}
    expanded_from: dict[str, str] = {}
    arrival_ts: list[datetime] = []
    ulids: list[str] = []
    for lsn, (o, tgt) in enumerate(items, start=1):
        ts = dt_of_day(o.t_rep)
        arrival_ts.append(ts)
        u = make_ulid(int(ts.timestamp() * 1000), lsn)
        ulids.append(u)
        if tgt is None:
            ulid_of[o.id] = u
            study_id[u] = o.id
        else:
            expanded_from[u] = o.id
    entries: list[LogEntry] = []
    source_retractions: list[tuple[int, str]] = []
    change_from: dict[str, Value] = {}
    for lsn, (o, tgt) in enumerate(items, start=1):
        u, ts = ulids[lsn - 1], arrival_ts[lsn - 1]
        src = stream.sources[o.source]
        common: dict[str, Any] = {
            "source": Source(id=o.source, cls=src.cls),
            "origin": Origin.EXTERNAL_OBSERVATION,
            "origin_group": src.origin,
            "actor": f"connector:{o.source}",
            "id": u,
        }
        if o.kind == "retract":
            if tgt is not None:  # expanded source-level retraction: withdraw one ingested assertion
                rep = Report(key=key_of[tgt.id], cue=Cue.WITHDRAW, target=ulid_of[tgt.id], **common)
                stats["source_retract_expanded_withdraws"] += 1
            elif o.target in stream.sources:  # sidetable source-level retraction
                source_retractions.append((lsn, o.target))
                continue
            else:
                rep = Report(key=key_of[o.target], cue=Cue.WITHDRAW, target=ulid_of[o.target], **common)
                stats["withdraw_reports"] += 1
        else:
            key = key_of[o.id]
            spec = kschema.spec(o.attr)
            prop = MemberProp(value=o.value) if spec.cardinality == "multi" else ValueProp(value=o.value)
            valid_from = dt_of_day(int(o.valid_t)) if o.valid_cue in ("since", "at") else None
            if o.op_cue == "correction":
                cue, target = Cue.CORRECT, ulid_of[o.op_of]
                if key_of[o.op_of] != key:
                    stats["cross_key_corrections"] += 1
            elif o.op_cue == "change":
                cue, target = Cue.CHANGE, None
                if o.op_from is not None:
                    change_from[u] = o.op_from
            else:
                cue, target = Cue.ASSERT, None
            rep = Report(key=key, cue=cue, proposition=prop, target=target, valid_from=valid_from, **common)
        entries.append(LogEntry(lsn=lsn, recorded_at=ts, report=rep))
    blocked = frozenset(sid for sid, s in stream.sources.items() if s.cls == "blocked")
    return Converted(stream_id=stream.stream_id, kschema=kschema, entries=entries, arrival_ts=arrival_ts,
                     source_retractions=source_retractions, blocked_sources=blocked, change_from=change_from,
                     study_id=study_id, ulid_of=ulid_of, stats=stats, source_retract_mode=source_retract,
                     expanded_from=expanded_from)


class CompatAdmission:
    """``revise-stream-v1`` admission over a converted log (temporary stub for T-D1/T-D2).

    ``acting_reports_must_be_live=False`` (the compat profile): a same-origin correction withdraws its target
    even if the correction itself was later withdrawn or its source blocked (the paper's behaviour; 387
    retracted corrections in Setting 1). With ``True`` (the product semantics, S-02) only a *live*
    correction acts; a withdrawn correction restores its target. Lane D measured that 84 of 314 comparable
    streams differ, so the compat profile needs the flag off to reproduce the gold.
    """

    def __init__(self, conv: Converted, *, acting_reports_must_be_live: bool = False) -> None:
        self.conv = conv
        self.acting_reports_must_be_live = acting_reports_must_be_live
        self._cache: dict[int, dict[Key, list[LogEntry]]] = {}

    def admitted_by_key(self, upto_lsn: int) -> dict[Key, list[LogEntry]]:
        hit = self._cache.get(upto_lsn)
        if hit is not None:
            return hit
        conv = self.conv
        prefix = [e for e in conv.entries if e.lsn <= upto_lsn]
        by_id = {e.report.id: e for e in prefix}
        retracted_src = {sid for lsn, sid in conv.source_retractions if lsn <= upto_lsn}
        explicit: set[str] = {e.report.target for e in prefix
                              if e.report.cue is Cue.WITHDRAW and e.report.target in by_id}  # type: ignore[misc]

        def live(e: LogEntry) -> bool:
            r = e.report
            return not (r.source.id in conv.blocked_sources or r.id in explicit or r.source.id in retracted_src)

        retracted = set(explicit)
        for e in prefix:
            r = e.report
            # A-SELF: a correction whose origin group equals its target's withdraws the target
            same_origin = r.cue is Cue.CORRECT and r.target in by_id and by_id[r.target].report.origin_group == r.origin_group
            if same_origin and (live(e) or not self.acting_reports_must_be_live):
                retracted.add(r.target)  # type: ignore[arg-type]
        out: dict[Key, list[LogEntry]] = {}
        for e in prefix:
            r = e.report
            if r.cue not in (Cue.ASSERT, Cue.CHANGE, Cue.CORRECT):
                continue
            if r.source.id in conv.blocked_sources or r.id in retracted or r.source.id in retracted_src:
                continue
            out.setdefault(r.key, []).append(e)
        self._cache[upto_lsn] = out
        return out
