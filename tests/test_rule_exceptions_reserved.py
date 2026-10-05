"""Rule exceptions are reserved in 0.x (author ruling 10 of 2026-10-05; S-10 narrows rules to strict rules).

The product profile refuses a rule that declares exceptions, at schema load and when a pipeline is opened on an explicit kernel
schema, with an explanation naming the rule. The compat profile keeps the paper's closed-world exception handling (the
differential gates depend on it). The contract ``Rule`` type rejects an ``exceptions`` member with a 'reserved' message and the
JSON Schema records the reservation.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from palimem.admission import AdmissionConfig
from palimem.compat import schema_from_kernel
from palimem.kernel import (
    AttrSpec,
    KernelSchema,
    KernelUnsupported,
    RuleExceptionsReserved,
    RuleSpec,
    reject_reserved_rule_features,
    rule_fn,
)
from palimem.memory import Memory
from palimem.policy import JUSTIFIED
from palimem.store import InMemoryBackend
from palimem.types import Profile, Rule, SemanticConfig, ValidationError

WITH_EXC = KernelSchema(
    attrs={
        "employer": AttrSpec("employer", "single", True),
        "hq_city": AttrSpec("hq_city", "single", False),
        "remote": AttrSpec("remote", "single", True),
        "work_city": AttrSpec("work_city", "single", True, error_allowed=False, derived=True),
    },
    rules=(
        RuleSpec(
            id="r1", head=("work_city", "?e", "?c"), body=(("employer", "?e", "?x"), ("hq_city", "?x", "?c")),
            exceptions=(("remote", "?e", True),),
        ),
    ),
    entities=("alice", "veltran"),
)
STRICT = KernelSchema(
    attrs=WITH_EXC.attrs,
    rules=(RuleSpec(id="r1", head=WITH_EXC.rules[0].head, body=WITH_EXC.rules[0].body),),
    entities=WITH_EXC.entities,
)


def open_memory(ks: KernelSchema, profile: Profile) -> Memory:
    return Memory(
        InMemoryBackend(), schema_from_kernel(ks), kernel_schema=ks, entities=None,
        semantic=SemanticConfig(self_update=False, profile=profile), admission=AdmissionConfig(profile=profile), policy=JUSTIFIED,
    )


def test_the_product_profile_refuses_a_rule_with_exceptions_and_names_the_rule() -> None:
    with pytest.raises(RuleExceptionsReserved) as e:
        reject_reserved_rule_features(WITH_EXC, Profile.OPEN_WORLD)
    assert "r1" in str(e.value) and "reserved" in str(e.value) and "strict" in str(e.value)
    assert isinstance(e.value, KernelUnsupported)  # callers that handle an unsupported schema see it as one


def test_the_compat_profile_keeps_the_papers_exception_handling() -> None:
    reject_reserved_rule_features(WITH_EXC, Profile.REVISE_STREAM_V1)  # does not raise


def test_a_strict_rule_is_accepted_in_both_profiles() -> None:
    for p in (Profile.OPEN_WORLD, Profile.REVISE_STREAM_V1):
        reject_reserved_rule_features(STRICT, p)


def test_opening_a_pipeline_on_a_schema_with_exceptions_is_refused_in_the_product_only() -> None:
    with pytest.raises(RuleExceptionsReserved):
        open_memory(WITH_EXC, Profile.OPEN_WORLD)
    open_memory(WITH_EXC, Profile.REVISE_STREAM_V1).close()  # compat opens it
    open_memory(STRICT, Profile.OPEN_WORLD).close()


def test_from_schema_refuses_exceptions_in_the_product_profile() -> None:
    contract = schema_from_kernel(STRICT)
    derived = next(a for a in contract.attrs if a.name == "work_city")
    assert derived.rule is not None
    bad = replace(derived, rule=Rule(reads=derived.rule.reads, fn=rule_fn("single", (WITH_EXC.rules[0],))))
    schema = replace(contract, attrs=tuple(bad if a.name == "work_city" else a for a in contract.attrs))
    with pytest.raises(RuleExceptionsReserved):
        KernelSchema.from_schema(schema, profile=Profile.OPEN_WORLD)
    KernelSchema.from_schema(schema, profile=Profile.REVISE_STREAM_V1)  # compat accepts


def test_the_contract_rule_type_rejects_an_exceptions_member_as_reserved() -> None:
    with pytest.raises(ValidationError, match="reserved"):
        Rule.from_dict({"reads": ["employer"], "fn": "f", "exceptions": []})
    assert Rule.from_dict({"reads": ["employer"], "fn": "f"}).reads == ("employer",)


def test_the_json_schema_records_the_reservation() -> None:
    d = json.loads((Path(__file__).resolve().parents[1] / "schemas" / "attr.schema.json").read_text())
    rule = d["$defs"]["Rule"] if "$defs" in d else d["definitions"]["Rule"]
    assert "exceptions" in rule["x-palimem-reserved"] and "exceptions" not in rule["properties"]
    assert rule.get("additionalProperties") is False  # so an instance carrying `exceptions` is invalid
