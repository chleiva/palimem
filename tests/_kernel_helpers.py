"""Builders for kernel tests: log entries and kernel schemas without the study."""

from __future__ import annotations

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


def ulid(n: int) -> str:
    out = []
    for shift in range(75, -1, -5):
        out.append(_CROCKFORD[(n >> shift) & 31])
    return "0" * 10 + "".join(out)


def entry(
    lsn: int,
    entity: str,
    attr: str,
    value: Value,
    *,
    day: int | None = None,
    cue: str = "assert",
    origin_group: str = "g1",
    source: str = "s1",
    target: int | None = None,
    since: int | None = None,
    multi: bool = False,
    change_from: Value | None = None,
) -> LogEntry:
    """A log entry whose report id is ``ulid(lsn)``; ``target`` is the lsn of the targeted report."""
    cue_e = {"assert": Cue.ASSERT, "change": Cue.CHANGE, "correct": Cue.CORRECT}[cue]
    prop = MemberProp(value=value) if multi else ValueProp(value=value)
    rep = Report(
        id=ulid(lsn),
        key=Key(entity=entity, attr=attr),
        cue=cue_e,
        proposition=prop,
        target=None if target is None else ulid(target),
        source=Source(id=source, cls="standard"),
        origin=Origin.EXTERNAL_OBSERVATION,
        origin_group=origin_group,
        actor=f"connector:{source}",
        valid_from=None if since is None else dt_of_day(since),
        change_from=change_from,
    )
    return LogEntry(lsn=lsn, recorded_at=dt_of_day(lsn if day is None else day), report=rep)


def schema(*attrs: AttrSpec, rules: tuple[RuleSpec, ...] = (), entities: tuple[str, ...] = ("e",)) -> KernelSchema:
    return KernelSchema(attrs={a.name: a for a in attrs}, rules=rules, entities=entities)


def single_changeable(name: str) -> AttrSpec:
    return AttrSpec(name=name, cardinality="single", changeable=True)


def single_stable(name: str) -> AttrSpec:
    return AttrSpec(name=name, cardinality="single", changeable=False)


def multi_changeable(name: str) -> AttrSpec:
    return AttrSpec(name=name, cardinality="multi", changeable=True)


def derived(name: str, cardinality: str = "single") -> AttrSpec:
    return AttrSpec(name=name, cardinality=cardinality, changeable=True, error_allowed=False, derived=True)  # type: ignore[arg-type]
