"""Property-style tests: random valid instances of every type are accepted, round-trip losslessly
through canonical JSON, and validate against the committed JSON Schemas (T-A3 / T-A4)."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from palimem import schemas
from palimem.types import (
    AdmissionRecord,
    Attr,
    AuthorityRule,
    AuthorityTable,
    Belief,
    BeliefView,
    Candidate,
    ExplainQuery,
    Explanation,
    LogEntry,
    MergeMarker,
    MergeRecord,
    Query,
    Report,
    Schema,
    Segment,
    Support,
    answer_from_json,
    canonical_json,
    proposition_from_json,
)
from tests._typegen import GENERATORS

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"
N = 250

FROM_JSON: dict[str, Any] = {
    "report": Report.from_json,
    "proposition": proposition_from_json,
    "attr": Attr.from_json,
    "schema": Schema.from_json,
    "candidate": Candidate.from_json,
    "support": Support.from_json,
    "segment": Segment.from_json,
    "belief": Belief.from_json,
    "belief_view": BeliefView.from_json,
    "query": Query.from_json,
    "answer": answer_from_json,
    "log_entry": LogEntry.from_json,
    "admission_record": AdmissionRecord.from_json,
    "merge_marker": MergeMarker.from_json,
    "merge_record": MergeRecord.from_json,
    "authority_rule": AuthorityRule.from_json,
    "authority_table": AuthorityTable.from_json,
    "explain_query": ExplainQuery.from_json,
    "explanation": Explanation.from_json,
}


def _validator(stem: str) -> jsonschema.Draft202012Validator:
    schema = json.loads((SCHEMA_DIR / f"{stem}.schema.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def test_every_root_has_a_generator_and_decoder():
    assert set(GENERATORS) == set(FROM_JSON) == set(schemas.ROOTS)


def test_committed_schemas_match_the_generator():
    assert schemas.check(SCHEMA_DIR) == []


@pytest.mark.parametrize("stem", sorted(GENERATORS))
def test_random_instances_roundtrip_and_validate(stem: str):
    rng = random.Random(f"roundtrip-{stem}")
    v = _validator(stem)
    decode = FROM_JSON[stem]
    for i in range(N):
        obj = GENERATORS[stem](rng)
        text = obj.to_json()
        # canonical: sorted keys, compact, stable
        assert text == canonical_json(json.loads(text)), (stem, i)
        # lossless round trip, and decode(encode(x)) re-encodes to the same bytes
        back = decode(text)
        assert back == obj, (stem, i, text)
        assert back.to_json() == text, (stem, i)
        # the emitted JSON satisfies the committed schema
        errors = sorted(v.iter_errors(json.loads(text)), key=lambda e: list(e.path))
        assert not errors, (stem, i, text, errors[0].message)


@pytest.mark.parametrize("stem", sorted(schemas.ROOTS))
def test_examples_validate_and_roundtrip(stem: str):
    v = _validator(stem)
    ex_dir = SCHEMA_DIR / "examples"
    files = [p for p in sorted(ex_dir.glob("*.json")) if any(
        s == stem and p.stem == name for name, s, _ in schemas.build_examples())]
    assert files, f"no example for {stem}"
    for p in files:
        data = json.loads(p.read_text())
        assert not list(v.iter_errors(data)), p.name
        obj = FROM_JSON[stem](p.read_text())
        assert json.loads(obj.to_json()) == obj.to_dict()
        assert FROM_JSON[stem](obj.to_json()) == obj


def test_every_example_file_is_registered():
    on_disk = {p.stem for p in (SCHEMA_DIR / "examples").glob("*.json")}
    assert on_disk == {name for name, _s, _o in schemas.build_examples()}


def test_schemas_are_self_contained_and_closed():
    for p in SCHEMA_DIR.glob("*.schema.json"):
        s = json.loads(p.read_text())
        assert s["$schema"].endswith("2020-12/schema")
        text = p.read_text()
        for ref in {r for r in __import__("re").findall(r'"\$ref": "([^"]+)"', text)}:
            assert ref.startswith("#/$defs/"), (p.name, ref)
            assert ref.rsplit("/", 1)[-1] in s["$defs"], (p.name, ref)


def test_hash_of_segment_and_belief_is_stable():
    rng = random.Random(7)
    b = GENERATORS["belief"](rng)
    assert hash(b) == hash(Belief.from_json(b.to_json()))
    s = GENERATORS["segment"](rng)
    assert hash(s) == hash(Segment.from_json(s.to_json()))
