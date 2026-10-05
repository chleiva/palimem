"""``NotReconstructable``: a third ``Answer`` variant (author ruling 2026-10-05, additive).

A snapshot whose belief version was erased answers with the variant instead of raising: no segment, no kernel_status,
no content, and the number and log position of what was redacted."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from palimem.agent.render import answer_json, answer_text
from palimem.compat import answer_v1
from palimem.compat.revise_stream_v1 import CompatError
from palimem.memory import Memory, NotReconstructableError
from palimem.types import (
    Cue,
    ExplainQuery,
    Key,
    NotReconstructable,
    NotReconstructableReason,
    Query,
    Resolved,
    ResourceLimited,
    ValidationError,
    answer_from_json,
)
from tests._pipeline_helpers import Clock, assertion, current, make_backend, toy_memory

SCHEMAS = Path(__file__).resolve().parent.parent / "schemas"
KEY = Key(entity="alex", attr="employer")
SECRET = "veltran-secret-employer"


def erased(backend: str) -> tuple[Memory, str]:
    clock = Clock()
    m = toy_memory(make_backend(backend, clock))
    r1 = m.append(assertion("alex", "employer", SECRET, source="press"))
    clock.day = 1  # the change is logged a day later, so a timestamp between them selects the first version
    m.append(assertion("alex", "employer", "acme", source="press", cue=Cue.CHANGE))
    assert r1.entry is not None and r1.entry.report.id is not None
    m.delete(r1.entry.report.id)
    return m, r1.entry.report.id


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_an_erased_snapshot_answers_with_the_variant_not_an_exception(backend: str) -> None:
    m, _ = erased(backend)
    ans = current(m, "alex", "employer", belief_as_of=1)
    assert isinstance(ans, NotReconstructable)
    assert ans.reason is NotReconstructableReason.ERASED and ans.key == KEY
    assert ans.belief_as_of == 1 and ans.version >= 1 and ans.lsn == 1 and ans.current_available is True
    assert ans.decision == "not_reconstructable"
    # no segment, no kernel_status, nothing about the erased content
    d = ans.to_dict()
    assert "kernel_status" not in d and "segment" not in d and "assertion" not in d
    assert SECRET not in json.dumps(d)
    # the repaired current belief is readable
    now = current(m, "alex", "employer")
    assert isinstance(now, Resolved)
    assert not isinstance(now, ResourceLimited)


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_a_timestamp_snapshot_is_echoed_as_requested(backend: str) -> None:
    m, _ = erased(backend)
    ts = datetime(2026, 1, 1, 12, tzinfo=UTC)
    ans = m.query(Query(key=KEY, belief_as_of=ts))
    assert isinstance(ans, NotReconstructable) and ans.belief_as_of == ts


def test_explain_still_raises_because_it_cannot_return_an_answer() -> None:
    m, _ = erased("memory")
    with pytest.raises(NotReconstructableError) as ei:
        m.explain(ExplainQuery(key=KEY, belief_as_of=1))
    assert isinstance(ei.value.info, NotReconstructable)


def test_the_variant_round_trips_and_validates() -> None:
    ans = NotReconstructable(reason=NotReconstructableReason.ERASED, key=KEY, belief_as_of=3, version=2, lsn=3, current_available=False)
    back = answer_from_json(ans.to_json())
    assert back == ans and isinstance(back, NotReconstructable)
    ts = NotReconstructable(reason=NotReconstructableReason.ERASED, key=KEY, belief_as_of=datetime(2026, 2, 1, tzinfo=UTC), version=1, lsn=1)
    assert answer_from_json(ts.to_json()) == ts
    with pytest.raises(ValidationError):
        replace(ans, version=0)
    with pytest.raises(ValidationError):
        replace(ans, lsn=0)
    with pytest.raises(ValidationError):
        answer_from_json('{"decision":"not_reconstructable","reason":"erased","key":{"entity":"a","attr":"b"},"version":1,"lsn":1}')
    with pytest.raises(ValidationError):
        answer_from_json('{"decision":"not_reconstructable","reason":"vanished","key":{"entity":"a","attr":"b"},"belief_as_of":1,"version":1,"lsn":1}')
    with pytest.raises(ValidationError):  # a stray member is rejected like everywhere else
        answer_from_json('{"decision":"not_reconstructable","reason":"erased","key":{"entity":"a","attr":"b"},"belief_as_of":1,"version":1,"lsn":1,"x":1}')


def test_the_json_schema_describes_the_variant() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((SCHEMAS / "answer.schema.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    v = jsonschema.Draft202012Validator(schema)
    ans = NotReconstructable(reason=NotReconstructableReason.ERASED, key=KEY, belief_as_of=3, version=2, lsn=3)
    assert not list(v.iter_errors(ans.to_dict()))
    bad = ans.to_dict()
    bad["version"] = 0
    assert list(v.iter_errors(bad))
    example = json.loads((SCHEMAS / "examples" / "answer_not_reconstructable.json").read_text())
    assert not list(v.iter_errors(example)) and example["decision"] == "not_reconstructable"


def test_the_agent_renderer_says_so_without_leaking_or_guessing() -> None:
    m, _ = erased("memory")
    ans = current(m, "alex", "employer", belief_as_of=1)
    j = answer_json(ans, host=None, key=KEY, as_of=1, policy_label="justified", max_alternatives=5)  # type: ignore[arg-type]
    assert j["kind"] == "not_reconstructable" and j["decision"] == "not_reconstructable" and j["reason"] == "erased"
    assert j["current_available"] is True and "kernel_status" not in j and SECRET not in json.dumps(j)
    text = answer_text(j)
    assert "CANNOT BE RECONSTRUCTED" in text and "Do not guess the old value" in text and SECRET not in text
    gone = dict(j, current_available=False)
    assert "nothing readable remains" in answer_text(gone)


def test_the_v1_compat_projection_refuses_the_variant() -> None:
    ans = NotReconstructable(reason=NotReconstructableReason.ERASED, key=KEY, belief_as_of=3, version=2, lsn=3)
    with pytest.raises(CompatError, match="NotReconstructable"):
        answer_v1(ans, multi=False)
