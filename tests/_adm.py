"""Test builder for admission/policy tests: hand-built logs of typed reports."""

from __future__ import annotations

from datetime import UTC, datetime

from palimem.admission import ListLog, derive_ulid
from palimem.types import (
    BeliefOfProp,
    Cue,
    Key,
    LogEntry,
    Origin,
    Proposition,
    Report,
    Source,
    ValueProp,
)

T0 = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)


class Log:
    """Appends reports with deterministic ids ``r1, r2, ...`` (``log.ids['r1']``) and LSN 1, 2, ..."""

    def __init__(self) -> None:
        self.list = ListLog()
        self.ids: dict[str, str] = {}
        self.entries: dict[str, LogEntry] = {}

    def id(self, ref: str) -> str:
        return self.ids[ref]

    def add(
        self,
        ref: str,
        cue: Cue = Cue.ASSERT,
        *,
        value: str | None = "Acme",
        entity: str = "alice",
        attr: str = "employer",
        source: str = "press",
        cls: str = "standard",
        origin: Origin = Origin.EXTERNAL_OBSERVATION,
        group: str | None = None,
        actor: str | None = None,
        target: str | None = None,
        proposition: Proposition | None = None,
    ) -> LogEntry:
        rid = derive_ulid("test-report", ref)
        if proposition is None and value is not None and cue not in (Cue.WITHDRAW,):
            proposition = ValueProp(value=value)
        report = Report(
            id=rid,
            key=Key(entity=entity, attr=attr),
            cue=cue,
            proposition=proposition,
            target=None if target is None else self.ids.get(target, target),
            source=Source(id=source, cls=cls),
            origin=origin,
            origin_group=group or f"g_{source}",
            actor=actor or f"connector:{source}",
        )
        entry = LogEntry(lsn=len(self.ids) + 1, recorded_at=T0, report=report)
        self.list.add(entry)
        self.ids[ref] = rid
        self.entries[ref] = entry
        return entry

    def attributed(self, ref: str, holder: str, value: str, **kw: object) -> LogEntry:
        return self.add(
            ref,
            origin=Origin.ATTRIBUTED,
            proposition=BeliefOfProp(holder=holder, proposition=ValueProp(value=value)),
            **kw,  # type: ignore[arg-type]
        )
