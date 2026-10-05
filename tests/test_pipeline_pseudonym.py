"""Orphaned-entity pseudonymisation through the whole pipeline (real kernel and admission), on both backends:
deleting the only evidence about an entity removes the entity name from every index table, the key stays addressable,
beliefs of other entities that depended on it keep working, and verification (both scopes) stays green."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from palimem.memory import Memory
from palimem.store import SQLiteBackend
from palimem.types import KernelStatus, Resolved
from tests._pipeline_helpers import (
    Clock,
    assertion,
    current,
    established_value,
    make_backend,
    toy_memory,
)

SECRET = b"s" * 32


def build(kind: str, tmp_path: Path) -> tuple[Memory, Path | None]:
    clock = Clock()
    if kind == "memory":
        return toy_memory(make_backend("memory", clock)), None
    path = tmp_path / "pm.db"
    return toy_memory(SQLiteBackend(path, clock=clock, store_secret=SECRET)), path


def dump(m: Memory, path: Path | None) -> dict[str, str]:
    """Everything the store holds, per table, as text (the raw storage: no key translation)."""
    if path is None:
        st = m.backend.storage  # type: ignore[attr-defined]
        return {
            "log": repr(list(st.log.values())), "beliefs": repr(list(st.beliefs.values())), "current": repr(st.current) + repr(st.required),
            "marks": repr(st.marks), "jobs": repr(st.job_rows), "outbox": repr(list(st.outbox.values())), "subs": repr(st.subs),
        }
    con = sqlite3.connect(path)
    try:
        return {n: repr(con.execute(f"SELECT * FROM {n}").fetchall()) for (n,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    finally:
        con.close()


@pytest.fixture(params=["memory", "sqlite"])
def world(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[Memory, Path | None, str]:
    m, path = build(request.param, tmp_path)
    r1 = m.append(assertion("alex", "employer", "veltran", source="press"))
    m.append(assertion("veltran", "hq_city", "tessaly", source="registry"))
    assert r1.entry is not None and r1.entry.report.id is not None
    assert established_value(current(m, "alex", "work_city")) == "tessaly"  # alex -> veltran -> tessaly (derived)
    return m, path, r1.entry.report.id


def index_tables(t: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in t.items() if k in ("beliefs", "belief_pins", "belief_deps", "current_belief", "current", "marks", "subscriptions", "subs", "outbox", "completion_jobs", "jobs")}


def test_the_only_evidence_about_an_entity_erased_removes_its_name_from_every_index_table(world: tuple[Memory, Path | None, str]) -> None:
    m, path, rid = world
    assert "alex" in "".join(index_tables(dump(m, path)).values())  # sanity
    m.delete(rid)
    for name, text in index_tables(dump(m, path)).items():
        assert "alex" not in text, f"alex survives in {name}"
    assert "veltran" in "".join(index_tables(dump(m, path)).values())  # veltran still has live evidence: it keeps its name


def test_the_orphaned_key_is_still_addressable_and_other_beliefs_keep_working(world: tuple[Memory, Path | None, str]) -> None:
    m, _, rid = world
    m.delete(rid)
    for attr in ("employer", "work_city"):
        ans = current(m, "alex", attr)
        assert isinstance(ans, Resolved) and ans.kernel_status is KernelStatus.UNKNOWN
    assert established_value(current(m, "veltran", "hq_city")) == "tessaly"  # an unrelated live key is untouched
    assert m.verify("log").ok and m.verify("beliefs").ok and m.verify("all").ok
    assert m.backend.recover().ok


def test_the_entity_coming_back_is_re_identified_and_derived_values_return(world: tuple[Memory, Path | None, str]) -> None:
    m, path, rid = world
    m.delete(rid)
    m.append(assertion("alex", "employer", "veltran", source="press2"))
    t = index_tables(dump(m, path))
    assert "erased:" not in "".join(t.values()) and "alex" in "".join(t.values())
    assert established_value(current(m, "alex", "employer")) == "veltran"
    assert established_value(current(m, "alex", "work_city")) == "tessaly"
    assert m.verify("all").ok


def test_the_export_carries_no_orphaned_name(world: tuple[Memory, Path | None, str]) -> None:
    m, _, rid = world
    m.delete(rid)
    out = "".join(m.backend.export_jsonl())
    assert "alex" not in out
