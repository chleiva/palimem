"""Structural tests of the conformance fixtures: schema validity, reference hygiene, contract decoding.

These tests never run an implementation. They check that the fixtures are well formed, that they
use the real contract types (palimem.types), and that the suite covers what it claims to cover.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from palimem.types import AuthorityRule, Query, Report, Schema
from palimem.types.attr import Attr

from . import build_fixtures
from .matcher import RESERVED
from .runner import FIXTURE_DIR, load_fixtures, load_trust_boundary

ROOT = Path(__file__).parent
FIXTURES = load_fixtures()
SCENARIOS = [f for f in FIXTURES if f["kind"] == "scenario"]
ULID = "01JA00000000000000000000{:02d}"
FIXTURE_VALIDATOR = Draft202012Validator(json.loads((ROOT / "fixture.schema.json").read_text()))
REF = re.compile(r"\$([A-Za-z0-9_]+)(?:\.[A-Za-z0-9_]+)*")


def refs_in(s: str) -> list[str]:
    for prefix in ("$eq:", "$not:", "$gt:"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    m = REF.fullmatch(s)
    return [m.group(1)] if m else []


def ids(fx: dict[str, Any]) -> str:
    return fx["id"]


def test_generated_files_are_in_sync() -> None:
    assert build_fixtures.main(["--check"]) == 0


def test_schemas_are_valid_json_schema() -> None:
    Draft202012Validator.check_schema(json.loads((ROOT / "fixture.schema.json").read_text()))
    Draft202012Validator.check_schema(json.loads((ROOT / "index.schema.json").read_text()))


@pytest.mark.parametrize("fx", FIXTURES, ids=ids)
def test_fixture_validates_against_schema(fx: dict[str, Any]) -> None:
    errors = sorted(FIXTURE_VALIDATOR.iter_errors(fx), key=lambda e: list(e.path))
    assert not errors, "; ".join(f"{'/'.join(map(str, e.path))}: {e.message}" for e in errors[:5])


def test_ids_unique_and_match_files() -> None:
    seen: dict[str, Path] = {}
    for g in ("independent", "decisions", "security"):
        for p in (FIXTURE_DIR / g).glob("*.json"):
            if p.name == "index.json":
                continue
            fid = json.loads(p.read_text())["id"]
            assert fid == p.stem, f"{p}: id {fid} != file stem"
            assert fid not in seen, f"duplicate id {fid}"
            seen[fid] = p
    assert len(seen) == len(FIXTURES)


def test_all_twenty_two_independent_rows_are_covered() -> None:
    covered = {c for f in FIXTURES for c in f["covers"] if re.fullmatch(r"design-row-\d+", c)}
    assert covered == {f"design-row-{n}" for n in range(1, 23)}, "design v0.3 lists 22 independent acceptance rows"


def test_every_active_scenario_asserts_something() -> None:
    for fx in SCENARIOS:
        if "expect_load_error" in fx["setup"]:
            continue
        has = any(o.get("expect") for o in fx["ops"]) or fx.get("expect", {}).get("equal")
        assert has, f"{fx['id']}: no expectations"


def test_why_is_a_real_rationale() -> None:
    for fx in FIXTURES:
        assert "TODO" not in fx["why"] and len(fx["why"]) >= 120, f"{fx['id']}: why is too thin to check an expectation against"


# --- reference hygiene -------------------------------------------------------------------------------------


def _strings(v: Any):
    if isinstance(v, str):
        yield v
    elif isinstance(v, list):
        for x in v:
            yield from _strings(x)
    elif isinstance(v, dict):
        for x in v.values():
            yield from _strings(x)


@pytest.mark.parametrize("fx", SCENARIOS, ids=ids)
def test_references_are_bound_before_use(fx: dict[str, Any]) -> None:
    bound: set[str] = set()
    for op in fx["ops"]:
        used = [u for s in _strings({k: v for k, v in op.items() if k != "name"}) for u in refs_in(s)]
        for u in used:
            if "{i}" in json.dumps(op):
                continue
            assert u in bound or u in RESERVED, f"{fx['id']}: ${u} used before it is bound (op {op['op']}:{op.get('name') or op.get('ref')})"
        for key in ("ref", "name"):
            n = op.get(key)
            if n and "{" not in n:
                assert n not in bound, f"{fx['id']}: name {n} bound twice"
                assert n not in RESERVED
                bound.add(n)
    for eq in fx.get("expect", {}).get("equal", []):
        assert eq["a"] in bound and eq["b"] in bound, f"{fx['id']}: equal() names an unknown op"


# --- the fixtures speak the real contract ------------------------------------------------------------------


def expand_attr(name: str, a: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"name": name, "class": a["class"], "value_type": a.get("value_type", "string"), "inertia": a.get("inertia", False)}
    comp = a.get("completeness", "open")
    scope = {"source_classes": None, "interval": None}
    out["completeness"] = {"mode": comp, "scope": scope if comp == "declared" else None}
    if "rule" in a:
        out["rule"] = {"reads": a["rule"]["reads"], "fn": a["rule"]["fn"]}  # `exceptions` is not in palimem.types.Rule (see S-10 note)
    return out


@pytest.mark.parametrize("fx", SCENARIOS, ids=ids)
def test_schema_declarations_decode_as_contract_types(fx: dict[str, Any]) -> None:
    attrs = [expand_attr(n, a) for n, a in fx["setup"]["schema"].items()]
    Schema.from_dict({"version": 1, "attrs": attrs})
    for a in attrs:
        Attr.from_dict(a)
    for rule in fx["setup"].get("authority", []):
        AuthorityRule.from_dict(rule)


def _fake_ulid(ref: str, refs: dict[str, str]) -> str:
    return refs.setdefault(ref, ULID.format(len(refs) + 1))


def _as_contract(report: dict[str, Any], refs: dict[str, str]) -> dict[str, Any]:
    r = dict(report)
    t = r.get("target")
    if isinstance(t, str) and t.startswith("$"):
        r["target"] = _fake_ulid(t[1:], refs)
    return r


@pytest.mark.parametrize("fx", SCENARIOS, ids=ids)
def test_reports_and_queries_decode_as_contract_types(fx: dict[str, Any]) -> None:
    refs: dict[str, str] = {}
    for op in fx["ops"]:
        if op["op"] == "append":
            Report.from_dict(_as_contract(op["report"], refs))
        if op["op"] == "observe_text":
            for r in op["extractor_stub"]:
                Report.from_dict(_as_contract(r, refs))
        if op["op"] == "query":
            q = dict(op["query"])
            if isinstance(q.get("belief_as_of"), str) and q["belief_as_of"].startswith("$"):
                q["belief_as_of"] = 1
            Query.from_dict(q)


@pytest.mark.parametrize("fx", SCENARIOS, ids=ids)
def test_registered_sources_keep_their_class(fx: dict[str, Any]) -> None:
    reg = fx["setup"].get("sources", {})
    for op in fx["ops"]:
        if op["op"] == "append":
            s = op["report"]["source"]
            if s["id"] in reg:
                assert reg[s["id"]]["class"] == s["class"], f"{fx['id']}: {s['id']} class differs from the registry (class comes from connector metadata)"


def test_agent_actor_reports_are_agent_class_only() -> None:
    for fx in SCENARIOS:
        for op in fx["ops"]:
            if op["op"] == "append" and op["report"]["actor"].startswith("agent:"):
                assert op["report"]["origin"] in {"agent_hypothesis", "agent_statement", "plan"} or op["report"]["cue"] in {"dispute", "allege"}, fx["id"]


# --- SEC index and trust-boundary cross references ----------------------------------------------------------


def test_security_index_is_complete_and_consistent() -> None:
    index = json.loads((FIXTURE_DIR / "security" / "index.json").read_text())
    Draft202012Validator(json.loads((ROOT / "index.schema.json").read_text())).validate(index)
    items = {i["id"]: i for i in index["items"]}
    assert sorted(items) == [f"SEC-{n:02d}" for n in range(1, 45)], "every SEC-01..SEC-44 needs a disposition"
    known = {f["id"] for f in FIXTURES} | {t["id"] for t in load_trust_boundary()}
    for sid, item in items.items():
        for fid in item.get("fixtures", []):
            assert fid in known, f"{sid}: unknown fixture {fid}"
    for fx in FIXTURES:
        for c in fx["covers"]:
            if re.fullmatch(r"SEC-\d\d", c):
                assert fx["id"] in items[c].get("fixtures", []), f"{fx['id']} covers {c} but the index does not list it"


def test_trust_boundary_fixtures_are_loaded_not_duplicated() -> None:
    tbs = load_trust_boundary()
    assert len(tbs) == 20 and len({t["id"] for t in tbs}) == 20
    assert not any(f["id"].startswith("tb-") for f in FIXTURES)
    assert all(Path(t["path"]).is_file() for t in tbs)


def test_status_counts_are_what_the_report_says() -> None:
    by = {s: sum(1 for f in FIXTURES if f["status"] == s) for s in ("active", "pending-decision", "shell")}
    assert by["shell"] == 2 and by["active"] > by["pending-decision"] > 0
