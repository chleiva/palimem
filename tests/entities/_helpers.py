"""Builders for the entity-merge tests: the toy employer / hq_city / work_city schema plus the reserved merge attribute."""

from __future__ import annotations

from typing import Any

from palimem.admission import AdmissionConfig
from palimem.compat import schema_from_kernel
from palimem.entities import Entities, merge_attr_spec
from palimem.entities.registry import ENTITY_MERGE_ATTR
from palimem.kernel import KernelSchema
from palimem.memory import Memory
from palimem.policy import JUSTIFIED
from palimem.store import Backend
from palimem.types import Profile, SemanticConfig
from tests._pipeline_helpers import toy_kernel_schema


def merge_kernel_schema() -> KernelSchema:
    ks = toy_kernel_schema()
    attrs = dict(ks.attrs)
    attrs[ENTITY_MERGE_ATTR] = merge_attr_spec()  # type: ignore[assignment]
    return KernelSchema(attrs=attrs, rules=ks.rules, entities=ks.entities)


def merge_memory(backend: Backend, **kw: Any) -> Memory:
    """A pipeline whose schema opts in to entity merges. The entity universe comes from the log (``entities=None``)."""
    ks = merge_kernel_schema()
    return Memory(
        backend, schema_from_kernel(ks), kernel_schema=ks, entities=None,
        semantic=SemanticConfig(self_update=False, profile=Profile.OPEN_WORLD),
        admission=AdmissionConfig(profile=Profile.OPEN_WORLD), policy=JUSTIFIED, **kw,
    )


def merge_setup(backend: Backend) -> tuple[Memory, Entities]:
    m = merge_memory(backend)
    return m, Entities(m)


def versions(m: Memory) -> dict[tuple[str, str], int]:
    """Current belief version of every stored key (to see exactly which beliefs a decision rewrote)."""
    out: dict[tuple[str, str], int] = {}
    for k in m.backend._s.current_keys():  # type: ignore[attr-defined]
        v = m.backend._s.current_version(k)  # type: ignore[attr-defined]
        if v is not None:
            out[(k.entity, k.attr)] = v
    return out


def core(m: Memory, entity: str, attr: str) -> Any:
    """The content that must survive a merge and its reversal: segments, pins and *which* keys a belief depends on
    (the dependency *versions* legitimately advance when a base belief is rewritten)."""
    from palimem.types import Key

    b = m.backend.current_belief(Key(entity=entity, attr=attr))
    assert b is not None
    d = b.to_dict()
    return {
        "segments": d["segments"], "pinned": d["pinned"],
        "depends_on": [(x["key"]["entity"], x["key"]["attr"]) for x in d["depends_on"]],
    }
