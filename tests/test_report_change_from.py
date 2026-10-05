"""``Report.change_from`` (author ruling 2026-10-05, additive): validation, canonical bytes, schema, the
hash chain of earlier logs, and the kernel reading the field."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from palimem.types import (
    Cue,
    Key,
    Origin,
    Report,
    Source,
    ValidationError,
    ValueProp,
    canonical_json,
)
from tests._pipeline_helpers import assertion

SCHEMAS = Path(__file__).resolve().parent.parent / "schemas"
SRC = Source(id="registry", cls="trusted")


def _report(cue: Cue = Cue.CHANGE, **kw: object) -> Report:
    base = {
        "key": Key(entity="alex", attr="employer"), "cue": cue, "proposition": ValueProp(value="globex"),
        "source": SRC, "origin": Origin.EXTERNAL_OBSERVATION, "origin_group": "registry", "actor": "connector:registry",
        **kw,
    }
    return Report(**base)  # type: ignore[arg-type]


def test_a_change_cue_may_state_the_previous_value() -> None:
    r = _report(change_from="acme")
    assert r.change_from == "acme"
    assert r.to_dict()["change_from"] == "acme"
    assert Report.from_json(r.to_json()) == r


@pytest.mark.parametrize("value", ["acme", 7, 2.5, True])
def test_every_scalar_value_type_round_trips_and_stays_distinct(value: object) -> None:
    r = _report(change_from=value)
    back = Report.from_json(r.to_json())
    assert back.change_from == value and type(back.change_from) is type(value)


@pytest.mark.parametrize("cue", [Cue.ASSERT, Cue.CORRECT])
def test_only_a_change_cue_may_carry_it(cue: Cue) -> None:
    kw: dict[str, object] = {"target": "01JZ0000000000000000000001"} if cue is Cue.CORRECT else {}
    with pytest.raises(ValidationError, match="change_from"):
        _report(cue, change_from="acme", **kw)


def test_withdraw_dispute_and_allege_cannot_carry_it_either() -> None:
    t = "01JZ0000000000000000000001"
    with pytest.raises(ValidationError):
        Report(key=Key(entity="a", attr="b"), cue=Cue.WITHDRAW, target=t, source=SRC, origin=Origin.EXTERNAL_OBSERVATION,
               origin_group="g", actor="connector:registry", change_from="x")
    with pytest.raises(ValidationError):
        _report(Cue.DISPUTE, target=t, change_from="x")
    with pytest.raises(ValidationError):
        _report(Cue.ALLEGE, target=t, change_from="x")


def test_a_value_must_be_a_scalar() -> None:
    with pytest.raises(ValidationError):
        _report(change_from=["acme"])  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        _report(change_from=float("nan"))


def test_absent_change_from_leaves_the_canonical_bytes_of_earlier_reports_unchanged() -> None:
    """The hash chain commits to the canonical Report JSON: the new field must not appear when absent."""
    plain = _report(Cue.ASSERT)
    assert "change_from" not in plain.to_dict()
    assert "change_from" not in json.loads(canonical_json(plain.to_dict()))
    no_from = _report()  # a change cue without a previous value
    assert "change_from" not in no_from.to_dict()
    # a record written before the field existed still decodes (the key is simply absent)
    d = plain.to_dict()
    assert Report.from_dict(d) == plain


def test_the_json_schema_accepts_the_field_only_for_a_change_cue() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    from referencing import Registry, Resource  # type: ignore[import-not-found]

    schema = json.loads((SCHEMAS / "report.schema.json").read_text())
    ok = _report(change_from="acme").to_dict()
    bad = _report(Cue.ASSERT).to_dict()
    bad["change_from"] = "acme"
    store = {p.name: Resource.from_contents(json.loads(p.read_text())) for p in SCHEMAS.glob("*.schema.json")}
    registry: Registry = Registry()
    for name, res in store.items():
        registry = registry.with_resource(name, res)
        sid = json.loads((SCHEMAS / name).read_text()).get("$id")
        if sid:
            registry = registry.with_resource(sid, res)
    validator = jsonschema.Draft202012Validator(schema, registry=registry)
    assert not list(validator.iter_errors(ok))
    assert list(validator.iter_errors(bad))
    example = json.loads((SCHEMAS / "examples" / "report_change.json").read_text())
    assert example["change_from"] == "Acme" and not list(validator.iter_errors(example))


def test_the_field_survives_the_pipeline_the_log_and_the_hash_chain() -> None:
    from dataclasses import replace

    from tests._pipeline_helpers import Clock, make_backend, toy_memory

    be = make_backend("memory", Clock())
    m = toy_memory(be)
    m.append(assertion("alex", "employer", "acme", source="press"))
    rep = replace(assertion("alex", "employer", "globex", source="registry", cue=Cue.CHANGE), change_from="acme")
    res = m.append(rep)
    assert res.entry is not None and res.entry.report.change_from == "acme"
    stored = [e.report for e in be.scan() if hasattr(e, "report")]
    assert stored[-1].change_from == "acme" and stored[0].change_from is None
    assert be.verify_log().ok  # the chain commits to the canonical bytes that now include the field
