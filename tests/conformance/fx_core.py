"""Scenario constructor shared by the fixture modules."""

from __future__ import annotations

from typing import Any

from .dsl import SOURCES


def d(month: int, day: int, hour: int = 10, year: int = 2026) -> str:
    return f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:00:00Z"


def day(month: int, dd: int, year: int = 2026) -> str:
    return f"{year:04d}-{month:02d}-{dd:02d}T00:00:00Z"


def scen(id: str, title: str, gate: str, area: str, covers: list[str], why: str,
         schema: dict[str, Any], ops: list[dict[str, Any]], *, source: str,
         requires: list[str] | None = None, deps: list[str] | None = None,
         status: str = "active", reason: str | None = None, profile: str = "open-world",
         sources: dict[str, Any] | None = None, authority: list[dict[str, Any]] | None = None,
         limits: dict[str, Any] | None = None) -> dict[str, Any]:
    used = {o["report"]["source"]["id"] for o in ops if o.get("op") == "append"}
    for o in ops:
        for r in o.get("extractor_stub", []):
            used.add(r["source"]["id"])
    reg = sources if sources is not None else {k: SOURCES[k] for k in sorted(used) if k in SOURCES}
    setup: dict[str, Any] = {"profile": profile, "schema": schema, "sources": reg}
    if authority:
        setup["authority"] = authority
    if limits:
        setup["limits"] = limits
    f: dict[str, Any] = {
        "version": 1, "kind": "scenario", "id": id, "title": title, "gate": gate, "area": area,
        "status": status, "covers": covers, "requires": sorted(requires or []),
        "spec_dependencies": deps or [], "why": why, "source": source, "setup": setup, "ops": ops,
    }
    if reason:
        f["status_reason"] = reason
    return f
