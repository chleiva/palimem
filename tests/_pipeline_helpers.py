"""Shared builders for the pipeline tests: a toy employer / hq_city / work_city schema, report builders and backends."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from palimem.admission import AdmissionConfig
from palimem.compat import schema_from_kernel
from palimem.kernel import AttrSpec, KernelSchema, RuleSpec
from palimem.memory import Memory
from palimem.policy import JUSTIFIED, PolicyObject
from palimem.store import Backend, InMemoryBackend, SQLiteBackend
from palimem.types import (
    Answer,
    Cue,
    Key,
    Origin,
    Profile,
    Query,
    Report,
    Resolved,
    SemanticConfig,
    Source,
    ValueProp,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def toy_kernel_schema() -> KernelSchema:
    return KernelSchema(
        attrs={
            "employer": AttrSpec("employer", "single", True),
            "hq_city": AttrSpec("hq_city", "single", False),
            "work_city": AttrSpec("work_city", "single", True, error_allowed=False, derived=True),
            "affiliations": AttrSpec("affiliations", "multi", False, competing_values=False),
        },
        rules=(
            RuleSpec(
                id="r1", head=("work_city", "?e", "?c"),
                body=(("employer", "?e", "?x"), ("hq_city", "?x", "?c")),
            ),
        ),
        entities=("alex", "veltran", "acme"),
    )


class Clock:
    """A controllable clock: the log's ``recorded_at`` (the day anchor of reports without a valid-time cue)."""

    def __init__(self) -> None:
        self.day = 0

    def __call__(self) -> datetime:
        return T0 + timedelta(days=self.day)


def make_backend(kind: str, clock: Clock, tmp_path: Path | None = None, **kw: Any) -> Backend:
    if kind == "memory":
        return InMemoryBackend(clock=clock, store_secret=b"s" * 32, **kw)
    path = ":memory:" if tmp_path is None else tmp_path / "pm.db"
    return SQLiteBackend(path, clock=clock, store_secret=b"s" * 32, **kw)


def toy_memory(
    backend: Backend, *, policy: PolicyObject = JUSTIFIED, self_update: bool = False,
    profile: Profile = Profile.OPEN_WORLD, admission: AdmissionConfig | None = None, **kw: Any,
) -> Memory:
    ks = toy_kernel_schema()
    return Memory(
        backend, schema_from_kernel(ks), kernel_schema=ks, entities=ks.entities,
        semantic=SemanticConfig(self_update=self_update, profile=profile),
        admission=admission or AdmissionConfig(profile=profile), policy=policy, **kw,
    )


def src(name: str, cls: str = "standard") -> Source:
    return Source(id=name, cls=cls)


def assertion(
    entity: str, attr: str, value: Any, source: str = "press", *, cue: Cue = Cue.ASSERT, group: str | None = None,
    origin: Origin = Origin.EXTERNAL_OBSERVATION, actor: str | None = None, target: str | None = None,
) -> Report:
    return Report(
        key=Key(entity=entity, attr=attr), cue=cue, proposition=ValueProp(value=value), source=src(source),
        origin=origin, origin_group=group or source, actor=actor or f"connector:{source}", target=target,
    )


def current(mem: Memory, entity: str, attr: str, **kw: Any) -> Answer:
    return mem.query(Query(key=Key(entity=entity, attr=attr), profile=mem.semantic.profile, **kw))


def established_value(ans: Answer) -> Any:
    assert isinstance(ans, Resolved), ans
    seg = ans.justified.segment
    if seg.established is None:
        return None
    f = seg.established.form
    return getattr(f, "value", None)


Maker = Callable[[], Memory]
