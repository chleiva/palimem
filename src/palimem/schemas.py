"""JSON Schemas (draft 2020-12) for the palimem contract types, plus canonical examples (T-A4).

The schemas are *generated* here and committed under ``schemas/``; ``python -m palimem.schemas
--check`` fails if the committed files drift from this module, and the tests validate random valid
instances of every type, and the examples, against them. The schemas describe the JSON the types
emit and accept (nullable fields may be omitted or null; timestamps accept any zone offset and are
normalised to UTC ``Z`` on decode).

Cross-field rules that matter to the contract are encoded with ``if``/``then`` (cue/target/
proposition, kernel_status/candidates, decision/assertion/inquiry, admission outcome/reason).

Reserved extension point (S-11): ``belief_of`` nests at most once in contract v2. The inner
proposition is the *plain* union; ``x-palimem-reserved`` marks where a ``belief_of`` alternative
would be added if the limit is ever lifted, which is then a non-breaking widening.

Standard library only (``jsonschema`` is a dev dependency used by the tests).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from palimem.types import (
    AdmissionOutcome,
    AdmissionReason,
    AdmissionRecord,
    Attr,
    AttrClass,
    AuthorityRule,
    AuthorityTable,
    Belief,
    BeliefOfProp,
    BeliefView,
    Candidate,
    Completeness,
    CompletenessMode,
    CompletenessScope,
    Cue,
    Decision,
    Dependency,
    EmptyForm,
    EnumerationProp,
    ExplainMode,
    ExplainQuery,
    Explanation,
    ExplanationState,
    Extractor,
    Inference,
    Inquiry,
    KernelStatus,
    Key,
    KeyScope,
    LogEntry,
    MergeMarker,
    MergeOp,
    MergeRecord,
    NotMemberForm,
    NotReconstructable,
    NotReconstructableReason,
    NotValueForm,
    Origin,
    PolicyInfo,
    Power,
    Precision,
    Profile,
    Query,
    Report,
    Resolved,
    ResolverInfo,
    ResourceLimited,
    ResourceLimitedReason,
    Rule,
    RuleFired,
    Schema,
    Segment,
    SegmentBounds,
    SetForm,
    Source,
    Support,
    Targets,
    ValueForm,
    ValueProp,
    ValueType,
    Versions,
    Who,
    WhoKind,
)
from palimem.types.authority import DEFAULT_RULES
from palimem.types.belief import InvalidatedBy, Pin
from palimem.types.enums import InvalidatedKind
from palimem.types.limits import MAX_BELIEF_NESTING

DRAFT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"

TS_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$"
ULID_PATTERN = r"^[0-9A-HJKMNP-TV-Z]{26}$"
HEX64_PATTERN = r"^[0-9a-f]{64}$"
PRINCIPAL_PATTERN = r"^(agent|user|connector|system):[A-Za-z0-9_.@/\-]+$"


def _ref(name: str) -> dict[str, Any]:
    return {"$ref": f"#/$defs/{name}"}


def _nullable(s: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [s, {"type": "null"}]}


def _enum(values: list[str]) -> dict[str, Any]:
    return {"type": "string", "enum": values}


def _vals(e: type[Any]) -> list[str]:
    return [m.value for m in e]


def _obj(props: dict[str, Any], required: list[str], **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "object", "properties": props, "required": required, "additionalProperties": False}
    out.update(extra)
    return out


def _arr(items: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"type": "array", "items": items, **extra}


def _when(prop: str, values: list[str], then: dict[str, Any]) -> dict[str, Any]:
    return {"if": {"properties": {prop: {"enum": values}}, "required": [prop]}, "then": then}


def _when_empty(prop: str, then: dict[str, Any]) -> dict[str, Any]:
    return {"if": {"properties": {prop: {"maxItems": 0}}, "required": [prop]}, "then": then}


def _absent_or_null(prop: str) -> dict[str, Any]:
    return {"properties": {prop: {"type": "null"}}}


def _present(prop: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {"required": [prop], "properties": {prop: schema}}


def _with_established(form_names: dict[str, Any]) -> dict[str, Any]:
    """An established candidate whose form is one of ``form_names``, and no alternatives."""
    cand = {"type": "object", "properties": {"form": {"type": "object", "properties": {"form": form_names}}}}
    return {"required": ["established"], "properties": {"established": cand, "alternatives": {"maxItems": 0}}}


def build_defs() -> dict[str, dict[str, Any]]:
    string = {"type": "string"}
    nonempty = {"type": "string", "minLength": 1}
    d: dict[str, dict[str, Any]] = {}

    d["Timestamp"] = {"type": "string", "pattern": TS_PATTERN}
    d["Ulid"] = {"type": "string", "pattern": ULID_PATTERN}
    d["Hex64"] = {"type": "string", "pattern": HEX64_PATTERN}
    d["Nat0"] = {"type": "integer", "minimum": 0}
    d["Nat1"] = {"type": "integer", "minimum": 1}
    d["Value"] = {"type": ["string", "integer", "number", "boolean"]}
    d["Principal"] = {"type": "string", "pattern": PRINCIPAL_PATTERN}
    d["BeliefAsOf"] = {
        "description": "An LSN (integer, S-05) or a timestamp the log maps to the last LSN at or before it.",
        "anyOf": [{"type": "integer", "minimum": 0}, _ref("Timestamp")],
    }

    d["Key"] = _obj({"entity": nonempty, "attr": nonempty}, ["entity", "attr"])

    # ---- propositions (S-11: belief_of nests at most once; deeper nesting is reserved)
    def value_like(form: str) -> dict[str, Any]:
        return _obj({"form": {"const": form}, "v": _ref("Value")}, ["form", "v"])

    d["ValueProp"] = value_like("value")
    d["MemberProp"] = value_like("member")
    d["NotMemberProp"] = value_like("not_member")
    d["NotValueProp"] = value_like("not_value")
    d["EnumerationProp"] = _obj({"form": {"const": "enumeration"}, "values": _arr(_ref("Value"))}, ["form", "values"])
    d["PlainProposition"] = {
        "oneOf": [_ref(n) for n in ("ValueProp", "MemberProp", "NotMemberProp", "EnumerationProp", "NotValueProp")]
    }
    d["BeliefOfProp"] = _obj(
        {"form": {"const": "belief_of"}, "holder": nonempty, "proposition": _ref("PlainProposition")},
        ["form", "holder", "proposition"],
        **{
            "x-palimem-reserved": {
                "nested_belief_of": f"reserved: depth > {MAX_BELIEF_NESTING} is not part of contract v2; lifting the "
                "limit adds a BeliefOfProp alternative to the inner proposition and is non-breaking"
            }
        },
    )
    d["Proposition"] = {"oneOf": [_ref("PlainProposition"), _ref("BeliefOfProp")]}

    # ---- candidates
    d["ValueForm"] = value_like("value")
    d["NotValueForm"] = value_like("not_value")
    d["NotMemberForm"] = value_like("not_member")
    d["SetForm"] = _obj({"form": {"const": "set"}, "values": _arr(_ref("Value"), minItems=1)}, ["form", "values"])
    d["EmptyForm"] = _obj({"form": {"const": "empty"}}, ["form"])
    d["BeliefOfForm"] = _obj(
        {"form": {"const": "belief_of"}, "holder": nonempty, "proposition": _ref("PlainProposition")},
        ["form", "holder", "proposition"],
    )
    d["CandidateForm"] = {
        "oneOf": [_ref(n) for n in ("ValueForm", "SetForm", "EmptyForm", "NotValueForm", "NotMemberForm", "BeliefOfForm")]
    }
    d["Candidate"] = _obj({"id": _ref("Hex64"), "key": _ref("Key"), "form": _ref("CandidateForm")}, ["key", "form"])

    d["Support"] = _obj(
        {
            "environment": _arr(_ref("Ulid"), minItems=1),
            "valid_from": _nullable(_ref("Timestamp")),
            "valid_to": _nullable(_ref("Timestamp")),
            "precision": _enum(_vals(Precision)),
        },
        ["environment"],
    )

    cand_arr = _arr(_ref("Candidate"))
    positive = {"enum": ["value", "set", "belief_of"]}
    negative = {"enum": ["not_value", "not_member"]}
    d["Segment"] = _obj(
        {
            "valid_from": _nullable(_ref("Timestamp")),
            "valid_to": _nullable(_ref("Timestamp")),
            "kernel_status": _enum(_vals(KernelStatus)),
            "established": _nullable(_ref("Candidate")),
            "alternatives": cand_arr,
            "support": {
                "type": "object",
                "propertyNames": {"pattern": HEX64_PATTERN},
                "additionalProperties": _arr(_ref("Support"), minItems=1),
            },
        },
        ["kernel_status"],
        allOf=[
            _when("kernel_status", ["established"], _with_established(positive)),
            _when("kernel_status", ["established_empty"], _with_established({"const": "empty"})),
            _when("kernel_status", ["established_false"], _with_established(negative)),
            _when("kernel_status", ["unresolved"], {
                "properties": {"established": {"type": "null"}, "alternatives": {"minItems": 2}},
                "required": ["alternatives"],
            }),
            _when("kernel_status", ["unknown"], {
                # alternatives are allowed only as constraints: negative candidates (not_value / not_member)
                "properties": {
                    "established": {"type": "null"},
                    "alternatives": {"items": {"type": "object", "properties": {"form": {"type": "object", "properties": {"form": negative}}}}},
                },
            }),
        ],
    )

    # ---- schema entries and authority
    d["Interval"] = _obj({"start": _nullable(_ref("Timestamp")), "end": _nullable(_ref("Timestamp"))}, [])
    d["CompletenessScope"] = _obj(
        {"source_classes": _nullable(_arr(nonempty)), "interval": _nullable(_ref("Interval"))}, []
    )
    d["Completeness"] = _obj(
        {"mode": _enum(_vals(CompletenessMode)), "scope": _nullable(_ref("CompletenessScope"))},
        ["mode"],
        allOf=[
            _when("mode", ["declared"], _present("scope", _ref("CompletenessScope"))),
            _when("mode", ["open", "by_enumeration"], _absent_or_null("scope")),
        ],
    )
    d["Rule"] = _obj(
        {"reads": _arr(nonempty, minItems=1, uniqueItems=True), "fn": nonempty},
        ["reads", "fn"],
        **{
            "x-palimem-reserved": {
                "exceptions": "reserved: rule exceptions are not part of contract v2 or of 0.x (S-10 narrows rules to strict "
                "rules); a rule carrying an 'exceptions' member is refused, and adding the field later is non-breaking"
            }
        },
    )
    d["KeyScope"] = _obj({"attr": nonempty, "entity": nonempty}, [])
    d["Who"] = _obj(
        {"kind": _enum(_vals(WhoKind)), "value": _nullable(string)},
        ["kind"],
        allOf=[
            _when("kind", ["principal"], _present("value", _ref("Principal"))),
            _when("kind", ["origin_group"], _present("value", nonempty)),
            _when("kind", ["any", "target_source", "target_actor", "target_origin_group"], _absent_or_null("value")),
        ],
    )
    d["AuthorityRule"] = _obj(
        {
            "who": _ref("Who"),
            "may": _arr(_enum(_vals(Power)), minItems=1, uniqueItems=True),
            "on": _ref("KeyScope"),
            "targets": _enum(_vals(Targets)),
            "over_origins": _nullable(_arr(_enum(_vals(Origin)), minItems=1, uniqueItems=True)),
        },
        ["who", "may"],
        description=(
            "S-07 invariant (enforced by the types and by admission, not expressible here): no rule grants an "
            "agent principal withdraw/correct over an external_observation, and none ever grants merge to an agent."
        ),
        allOf=[
            {
                # merge is granted on its own, by identity, and has no target reports (author ruling 2026-10-05)
                "if": {"properties": {"may": {"contains": {"const": "merge"}}}, "required": ["may"]},
                "then": {
                    "properties": {
                        "may": {"maxItems": 1},
                        "who": {"properties": {"kind": {"enum": ["principal", "any"]}}},
                        "over_origins": {"type": "null"},
                        "targets": {"const": "report"},
                    }
                },
            }
        ],
    )
    d["AuthorityTable"] = _obj(
        {"admission_version": _ref("Nat1"), "rules": _arr(_ref("AuthorityRule"))}, ["admission_version", "rules"]
    )
    d["Attr"] = _obj(
        {
            "name": nonempty,
            "class": _enum(_vals(AttrClass)),
            "value_type": _enum(_vals(ValueType)),
            "inertia": {"type": "boolean"},
            "rule": _nullable(_ref("Rule")),
            "completeness": _ref("Completeness"),
            "authority": _arr(_ref("AuthorityRule")),
        },
        ["name", "class", "value_type"],
        allOf=[
            _when("class", ["derived"], _present("rule", _ref("Rule"))),
            _when("class", ["single_stable", "single_changeable", "multi_set"], _absent_or_null("rule")),
        ],
    )
    d["Schema"] = _obj({"version": _ref("Nat1"), "attrs": _arr(_ref("Attr"))}, ["version", "attrs"])

    # ---- report and log entry
    d["Source"] = _obj({"id": nonempty, "class": nonempty}, ["id", "class"])
    d["Extractor"] = _obj({"model": nonempty, "version": nonempty, "prompt_hash": nonempty}, ["model", "version", "prompt_hash"])
    d["Report"] = _obj(
        {
            "id": _nullable(_ref("Ulid")),
            "key": _ref("Key"),
            "proposition": _nullable(_ref("Proposition")),
            "cue": _enum(_vals(Cue)),
            "target": _nullable(_ref("Ulid")),
            "source": _ref("Source"),
            "origin": _enum(_vals(Origin)),
            "origin_group": nonempty,
            "actor": _ref("Principal"),
            "observed_at": _nullable(_ref("Timestamp")),
            "valid_from": _nullable(_ref("Timestamp")),
            "valid_to": _nullable(_ref("Timestamp")),
            "precision": _enum(_vals(Precision)),
            "raw_ref": _nullable(nonempty),
            "extractor": _nullable(_ref("Extractor")),
            "change_from": _ref("Value"),  # optional; only for cue 'change' (author ruling 2026-10-05)
        },
        ["key", "cue", "source", "origin", "origin_group", "actor"],
        allOf=[
            _when("cue", ["correct", "withdraw", "dispute", "allege"], _present("target", _ref("Ulid"))),
            _when("cue", ["assert", "change"], _absent_or_null("target")),
            _when("cue", ["assert", "change", "correct"], _present("proposition", _ref("Proposition"))),
            _when("cue", ["withdraw"], _absent_or_null("proposition")),
            _when("cue", ["assert", "correct", "withdraw", "dispute", "allege"], _absent_or_null("change_from")),
        ],
    )
    d["LogEntry"] = _obj(
        {
            "lsn": _ref("Nat1"),
            "recorded_at": _ref("Timestamp"),
            "report": _ref("Report"),
            "prev_hash": _nullable(_ref("Hex64")),
            "entry_hash": _nullable(_ref("Hex64")),
        },
        ["lsn", "recorded_at", "report"],
    )
    reasons: dict[str, list[str]] = {
        "admissible": ["admitted", "confirmed"],
        "quarantined": ["source_quarantined"],
        "excluded": ["origin_not_admissible", "source_blocked", "authority_failed", "target_missing"],
    }
    d["AdmissionRecord"] = _obj(
        {
            "id": _ref("Ulid"),
            "report_id": _ref("Ulid"),
            "outcome": _enum(_vals(AdmissionOutcome)),
            "reason": _enum(_vals(AdmissionReason)),
            "admission_version": _ref("Nat1"),
            "confirmed_by": _arr(_ref("Ulid"), uniqueItems=True),
        },
        ["id", "report_id", "outcome", "reason", "admission_version"],
        allOf=[
            *[_when("outcome", [o], {"properties": {"reason": {"enum": rs}}}) for o, rs in reasons.items()],
            _when("reason", ["confirmed"], {"required": ["confirmed_by"], "properties": {"confirmed_by": {"minItems": 1}}}),
            _when("reason", [r for r in _vals(AdmissionReason) if r != "confirmed"], {"properties": {"confirmed_by": {"maxItems": 0}}}),
        ],
    )

    # ---- entity merges (author ruling 2026-10-05): the typed marker payload and the typed record
    d["ResolverInfo"] = _obj({"method": nonempty, "score": {"type": "number"}, "version": nonempty}, ["method"])
    d["MergeMarker"] = _obj(
        {
            "v": {"const": 2},
            "op": _enum(_vals(MergeOp)),
            "reason": nonempty,
            "into": nonempty,
            "target": _ref("Ulid"),
            "resolver": _ref("ResolverInfo"),
        },
        ["v", "op", "reason", "resolver"],
        description="The payload of a merge marker report. Payload version 1 (flat method/score) is read by the types, never written.",
        allOf=[
            _when("op", ["merge"], {"required": ["into"], "not": {"required": ["target"]}}),
            _when("op", ["unmerge"], {"required": ["target"], "not": {"required": ["into"]}}),
        ],
    )
    d["MergeRecord"] = _obj(
        {
            "id": _ref("Ulid"),
            "members": _arr(nonempty, minItems=2, uniqueItems=True),
            "representative": nonempty,
            "reason": nonempty,
            "resolver": _ref("ResolverInfo"),
            "admission_version": _ref("Nat1"),
            "reversed_by": _nullable(_ref("Ulid")),
        },
        ["id", "members", "representative", "reason", "resolver", "admission_version"],
    )

    # ---- beliefs
    d["Inference"] = _obj(
        {"complete": {"type": "boolean"}, "reason": _nullable(nonempty)},
        ["complete"],
        allOf=[
            {"if": {"properties": {"complete": {"const": True}}, "required": ["complete"]}, "then": _absent_or_null("reason")},
            {"if": {"properties": {"complete": {"const": False}}, "required": ["complete"]}, "then": _present("reason", nonempty)},
        ],
    )
    d["Pin"] = _obj({"report_id": _ref("Ulid"), "admission_version": _ref("Nat1")}, ["report_id", "admission_version"])
    d["Dependency"] = _obj({"key": _ref("Key"), "version": _ref("Nat1")}, ["key", "version"])
    d["InvalidatedBy"] = _obj(
        {"kind": _enum(_vals(InvalidatedKind)), "id": nonempty},
        ["kind", "id"],
        allOf=[_when("kind", ["report", "admission"], {"properties": {"id": _ref("Ulid")}})],
    )
    d["Versions"] = _obj(
        {"schema": _ref("Nat1"), "semantic": _ref("Nat1"), "admission": _ref("Nat1")}, ["schema", "semantic", "admission"]
    )
    d["Belief"] = _obj(
        {
            "key": _ref("Key"),
            "version": _ref("Nat1"),
            "lsn": _ref("Nat1"),
            "required_generation": _ref("Nat0"),
            "completed_generation": _ref("Nat0"),
            "segments": _arr(_ref("Segment")),
            "pinned": _arr(_ref("Pin")),
            "depends_on": _arr(_ref("Dependency")),
            "invalidated_by": _nullable(_ref("InvalidatedBy")),
            "versions": _ref("Versions"),
            "inference": _ref("Inference"),
            "recorded_at": _ref("Timestamp"),
        },
        ["key", "version", "lsn", "required_generation", "completed_generation", "segments", "versions", "inference", "recorded_at"],
    )
    d["BeliefView"] = _obj(
        {
            "key": _ref("Key"),
            "version": _ref("Nat1"),
            "required_generation": _ref("Nat0"),
            "completed_generation": _ref("Nat0"),
            "inference": _ref("Inference"),
            "segment": _ref("Segment"),
            "ref": nonempty,
        },
        ["key", "version", "required_generation", "completed_generation", "inference", "segment", "ref"],
    )

    # ---- query, explain, answer
    d["Query"] = _obj(
        {
            "key": _ref("Key"),
            "valid_at": _nullable(_ref("Timestamp")),
            "belief_as_of": _nullable(_ref("BeliefAsOf")),
            "profile": _enum(_vals(Profile)),
            "explanation_budget": _nullable(_ref("Nat0")),
        },
        ["key"],
    )
    d["ExplainQuery"] = _obj(
        {
            "key": _ref("Key"),
            "valid_at": _nullable(_ref("Timestamp")),
            "belief_as_of": _nullable(_ref("BeliefAsOf")),
            "mode": _enum(_vals(ExplainMode)),
            "depth": _nullable(_ref("Nat1")),
        },
        ["key"],
    )
    d["SegmentBounds"] = _obj({"valid_from": _nullable(_ref("Timestamp")), "valid_to": _nullable(_ref("Timestamp"))}, [])
    d["Explanation"] = _obj(
        {
            "key": _ref("Key"),
            "segment": _ref("SegmentBounds"),
            "mode": _enum(_vals(ExplainMode)),
            "depth": _nullable(_ref("Nat1")),
            "state": _enum(_vals(ExplanationState)),
            "environments": _arr(_ref("Support")),
        },
        ["key", "segment", "mode"],
        allOf=[_when("mode", ["one"], {"properties": {"environments": {"maxItems": 1}}})],
    )
    d["PolicyInfo"] = _obj({"version": _ref("Nat1"), "rule_fired": _enum(_vals(RuleFired))}, ["version"])
    d["Inquiry"] = _obj(
        {"competing": _arr(_ref("Candidate")), "missing": _arr(_ref("Key")), "resolvers": _arr(nonempty)},
        ["competing"],
        # ruling 16: a key with no evidence has no competing candidate; the inquiry must then name a missing key
        allOf=[_when_empty("competing", _present("missing", _arr(_ref("Key"), minItems=1)))],
    )
    d["Resolved"] = _obj(
        {
            "segment": _ref("SegmentBounds"),
            "kernel_status": _enum(_vals(KernelStatus)),
            "decision": _enum(_vals(Decision)),
            "assertion": _nullable(_ref("Candidate")),
            "alternatives": cand_arr,
            "provenance": _arr(_ref("Support")),
            "explanation": _enum(_vals(ExplanationState)),
            "policy": _ref("PolicyInfo"),
            "confidence": _nullable({"type": "number", "minimum": 0, "maximum": 1}),
            "inquiry": _nullable(_ref("Inquiry")),
            "justified": _ref("BeliefView"),
        },
        ["segment", "kernel_status", "decision", "policy", "justified"],
        allOf=[
            _when("decision", ["commit"], _present("assertion", _ref("Candidate"))),
            _when("decision", ["abstain", "ask"], _absent_or_null("assertion")),
            _when("decision", ["ask"], _present("inquiry", _ref("Inquiry"))),
            _when("decision", ["commit"], _absent_or_null("inquiry")),
        ],
    )
    d["LastComplete"] = _obj({"belief_as_of": _ref("BeliefAsOf"), "view": _ref("BeliefView")}, ["belief_as_of", "view"])
    d["ResourceLimited"] = _obj(
        {
            "decision": {"const": "resource_limited"},
            "reason": _enum(_vals(ResourceLimitedReason)),
            "reason_key": _nullable(_ref("Key")),
            "required_generation": _ref("Nat0"),
            "completed_generation": _ref("Nat0"),
            "last_complete": _nullable(_ref("LastComplete")),
        },
        ["decision", "reason", "required_generation", "completed_generation"],
        description="Carries no segment and no kernel_status: neither could truthfully describe the snapshot.",
        allOf=[
            _when("reason", ["stale_dependency", "environment_budget"], _present("reason_key", _ref("Key"))),
            _when("reason", ["inference_incomplete", "store_dirty"], _absent_or_null("reason_key")),
        ],
    )
    d["NotReconstructable"] = _obj(
        {
            "decision": {"const": "not_reconstructable"},
            "reason": _enum(_vals(NotReconstructableReason)),
            "key": _ref("Key"),
            "belief_as_of": _ref("BeliefAsOf"),
            "version": _ref("Nat1"),
            "lsn": _ref("Nat1"),
            "current_available": {"type": "boolean"},
        },
        ["decision", "reason", "key", "belief_as_of", "version", "lsn"],
        description=(
            "The belief in force at the requested snapshot was redacted by an erasure (author ruling 2026-10-05, additive). "
            "Carries no segment and no kernel_status; says what was redacted (version, log position) without any content."
        ),
    )
    d["Answer"] = {"oneOf": [_ref("Resolved"), _ref("ResourceLimited"), _ref("NotReconstructable")]}
    return d


ROOTS: dict[str, str] = {
    "report": "Report",
    "proposition": "Proposition",
    "attr": "Attr",
    "schema": "Schema",
    "candidate": "Candidate",
    "support": "Support",
    "segment": "Segment",
    "belief": "Belief",
    "belief_view": "BeliefView",
    "query": "Query",
    "answer": "Answer",
    "log_entry": "LogEntry",
    "admission_record": "AdmissionRecord",
    "merge_marker": "MergeMarker",
    "merge_record": "MergeRecord",
    "authority_rule": "AuthorityRule",
    "authority_table": "AuthorityTable",
    "explain_query": "ExplainQuery",
    "explanation": "Explanation",
}


def _closure(defs: dict[str, dict[str, Any]], root: str) -> dict[str, dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    stack = [root]

    def refs(node: Any) -> list[str]:
        out: list[str] = []
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "$ref" and isinstance(v, str):
                    out.append(v.rsplit("/", 1)[-1])
                else:
                    out.extend(refs(v))
        elif isinstance(node, list):
            for v in node:
                out.extend(refs(v))
        return out

    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen[name] = defs[name]
        stack.extend(refs(defs[name]))
    return {k: seen[k] for k in sorted(seen)}


def build_schemas() -> dict[str, dict[str, Any]]:
    """``{file stem: schema}``; each schema is self-contained (its ``$defs`` are inlined)."""
    defs = build_defs()
    out: dict[str, dict[str, Any]] = {}
    for stem, root in ROOTS.items():
        out[stem] = {
            "$schema": DRAFT,
            "$id": f"urn:palimem:schema:{stem}:v2",
            "title": root,
            "$ref": f"#/$defs/{root}",
            "$defs": _closure(defs, root),
        }
    return out


# ------------------------------------------------------------------ examples

_T0 = datetime(2026, 3, 1, tzinfo=UTC)
_T1 = datetime(2026, 6, 1, tzinfo=UTC)
R1 = "01JA0000000000000000000001"
R2 = "01JA0000000000000000000002"
R3 = "01JA0000000000000000000003"
ADM1 = "01JA00000000000000000000A1"


def build_examples() -> list[tuple[str, str, Any]]:
    """``(file stem, schema stem, typed object)``; every example validates against its schema."""
    key = Key(entity="alice", attr="employer")
    acme = Candidate(key=key, form=ValueForm(value="Acme"))
    globex = Candidate(key=key, form=ValueForm(value="Globex"))
    report = Report(
        id=R1, key=key, proposition=ValueProp(value="Acme"), cue=Cue.ASSERT,
        source=Source(id="registry", cls="trusted"), origin=Origin.EXTERNAL_OBSERVATION,
        origin_group="registry-group", actor="connector:registry", valid_from=_T0,
        extractor=Extractor(model="openai.gpt-oss-20b-1:0", version="1", prompt_hash="sha256:0000"),
    )
    withdraw = Report(
        id=R3, key=key, cue=Cue.WITHDRAW, target=R1, source=Source(id="registry", cls="trusted"),
        origin=Origin.EXTERNAL_OBSERVATION, origin_group="registry-group", actor="connector:registry",
    )
    est_seg = Segment(
        valid_from=_T0, valid_to=None, kernel_status=KernelStatus.ESTABLISHED, established=acme,
        support={acme.id: (Support(environment=(R1,), valid_from=_T0),)},
    )
    unres_seg = Segment(
        valid_from=None, valid_to=_T0, kernel_status=KernelStatus.UNRESOLVED, alternatives=(acme, globex),
        support={acme.id: (Support(environment=(R1,)),), globex.id: (Support(environment=(R2,)),)},
    )
    belief = Belief(
        key=key, version=2, lsn=3, required_generation=3, completed_generation=3,
        segments=(unres_seg, est_seg), pinned=(Pin(report_id=R1, admission_version=1), Pin(report_id=R2, admission_version=1)),
        depends_on=(Dependency(key=Key(entity="acme", attr="hq_city"), version=1),),
        invalidated_by=InvalidatedBy(kind=InvalidatedKind.REPORT, id=R3),
        versions=Versions(schema=1, semantic=1, admission=1), inference=Inference(complete=True), recorded_at=_T1,
    )
    view = BeliefView(
        key=key, version=2, required_generation=3, completed_generation=3, inference=Inference(complete=True),
        segment=est_seg, ref="belief:alice/employer@2",
    )
    resolved = Resolved(
        segment=SegmentBounds(valid_from=_T0), kernel_status=KernelStatus.ESTABLISHED, decision=Decision.COMMIT,
        assertion=acme, provenance=(Support(environment=(R1,), valid_from=_T0),),
        policy=PolicyInfo(version=1, rule_fired=RuleFired.NONE), justified=view,
    )
    asking = Resolved(
        segment=SegmentBounds(valid_to=_T0), kernel_status=KernelStatus.UNRESOLVED, decision=Decision.ASK,
        alternatives=(acme, globex),
        policy=PolicyInfo(version=1, rule_fired=RuleFired.ASK),
        inquiry=Inquiry(competing=(acme, globex), missing=(Key(entity="alice", attr="payroll_entity"),), resolvers=("trusted",)),
        justified=BeliefView(
            key=key, version=2, required_generation=3, completed_generation=3, inference=Inference(complete=True),
            segment=unres_seg, ref="belief:alice/employer@2",
        ),
    )
    limited = ResourceLimited(
        reason=ResourceLimitedReason.ENVIRONMENT_BUDGET, reason_key=key, required_generation=4, completed_generation=3,
    )
    attr = Attr(
        name="employer", attr_class=AttrClass.SINGLE_CHANGEABLE, value_type=ValueType.ENTITY, inertia=True,
        completeness=Completeness(mode=CompletenessMode.OPEN),
        authority=(AuthorityRule(who=Who(kind=WhoKind.PRINCIPAL, value="user:alice"), may=(Power.CORRECT,), on=KeyScope(attr="employer*")),),
    )
    derived = Attr(
        name="work_city", attr_class=AttrClass.DERIVED, value_type=ValueType.ENTITY,
        rule=Rule(reads=("employer", "hq_city"), fn="work_city(e,c) <- employer(e,x), hq_city(x,c)"),
    )
    schema = Schema(version=1, attrs=(
        attr, Attr(name="hq_city", attr_class=AttrClass.SINGLE_STABLE, value_type=ValueType.ENTITY,
                   completeness=Completeness(mode=CompletenessMode.DECLARED, scope=CompletenessScope())),
        derived,
    ))
    return [
        ("report", "report", report),
        ("report_withdraw", "report", withdraw),
        ("report_attributed", "report", Report(
            key=key, cue=Cue.ASSERT, proposition=BeliefOfProp(holder="bob", proposition=ValueProp(value="Acme")),
            source=Source(id="chat", cls="standard"), origin=Origin.ATTRIBUTED, origin_group="chat", actor="connector:chat")),
        ("report_change", "report", Report(
            key=key, cue=Cue.CHANGE, proposition=ValueProp(value="Globex"), change_from="Acme",
            source=Source(id="registry", cls="trusted"), origin=Origin.EXTERNAL_OBSERVATION,
            origin_group="registry-group", actor="connector:registry", valid_from=_T1)),
        ("proposition", "proposition", EnumerationProp(values=("a", "b"))),
        ("attr", "attr", attr),
        ("schema", "schema", schema),
        ("candidate", "candidate", acme),
        ("candidate_empty", "candidate", Candidate(key=Key(entity="alice", attr="affiliations"), form=EmptyForm())),
        ("candidate_set", "candidate", Candidate(key=Key(entity="alice", attr="affiliations"), form=SetForm(values=("mit", "acme")))),
        ("candidate_negative", "candidate", Candidate(key=key, form=NotValueForm(value="Acme"))),
        ("candidate_not_member", "candidate", Candidate(key=Key(entity="alice", attr="affiliations"), form=NotMemberForm(value="mit"))),
        ("support", "support", Support(environment=(R1, R2), valid_from=_T0, valid_to=_T1)),
        ("segment", "segment", unres_seg),
        ("belief", "belief", belief),
        ("belief_view", "belief_view", view),
        ("query", "query", Query(key=key, valid_at=_T0, belief_as_of=3, explanation_budget=5)),
        ("query_timestamp", "query", Query(key=key, belief_as_of=_T1, profile=Profile.REVISE_STREAM_V1)),
        ("answer", "answer", resolved),
        ("answer_ask", "answer", asking),
        ("answer_resource_limited", "answer", limited),
        ("answer_not_reconstructable", "answer", NotReconstructable(
            reason=NotReconstructableReason.ERASED, key=key, belief_as_of=3, version=2, lsn=3, current_available=True)),
        ("log_entry", "log_entry", LogEntry(lsn=1, recorded_at=_T0, report=report, prev_hash=None, entry_hash="a" * 64)),
        ("admission_record", "admission_record", AdmissionRecord(
            id=ADM1, report_id=R1, outcome=AdmissionOutcome.ADMISSIBLE, reason=AdmissionReason.CONFIRMED,
            admission_version=1, confirmed_by=(R2,))),
        ("authority_rule", "authority_rule", DEFAULT_RULES[1]),
        ("authority_rule_merge", "authority_rule", AuthorityRule(
            who=Who(kind=WhoKind.PRINCIPAL, value="connector:registry"), may=(Power.MERGE,),
            on=KeyScope(attr="__entity_merge__", entity="acme*"))),
        ("merge_marker", "merge_marker", MergeMarker(
            op=MergeOp.MERGE, into="acme", reason="same registry id", resolver=ResolverInfo(method="lexical", score=0.91, version="1"))),
        ("merge_marker_unmerge", "merge_marker", MergeMarker(op=MergeOp.UNMERGE, target=R1, reason="false merge: two Acmes")),
        ("merge_record", "merge_record", MergeRecord(
            id=R2, members=("acme", "acme inc"), representative="acme", reason="same registry id",
            resolver=ResolverInfo(method="lexical", score=0.91, version="1"), admission_version=1, reversed_by=R3)),
        ("authority_table", "authority_table", AuthorityTable(admission_version=1, rules=DEFAULT_RULES)),
        ("explain_query", "explain_query", ExplainQuery(key=key, valid_at=_T0, mode=ExplainMode.ONE, depth=2)),
        ("explanation", "explanation", Explanation(
            key=key, segment=SegmentBounds(valid_from=_T0), mode=ExplainMode.ALL, depth=None,
            environments=(Support(environment=(R1,)),))),
    ]


# ------------------------------------------------------------------ files

def _dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def expected_files() -> dict[str, str]:
    files = {f"{stem}.schema.json": _dump(s) for stem, s in build_schemas().items()}
    for stem, _root, obj in build_examples():
        files[f"examples/{stem}.json"] = _dump(obj.to_dict())
    return files


def write_all(directory: Path = SCHEMA_DIR) -> int:
    (directory / "examples").mkdir(parents=True, exist_ok=True)
    files = expected_files()
    for name, text in files.items():
        (directory / name).write_text(text, encoding="utf-8")
    return len(files)


def check(directory: Path = SCHEMA_DIR) -> list[str]:
    problems: list[str] = []
    files = expected_files()
    for name, text in files.items():
        p = directory / name
        if not p.exists():
            problems.append(f"missing: {name}")
        elif p.read_text(encoding="utf-8") != text:
            problems.append(f"out of date: {name}")
    on_disk = {str(p.relative_to(directory)) for p in directory.rglob("*.json")} if directory.exists() else set()
    problems.extend(f"unexpected: {n}" for n in sorted(on_disk - set(files)))
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m palimem.schemas", description=__doc__.splitlines()[0] if __doc__ else "")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true", help="(re)generate schemas/ from the types")
    g.add_argument("--check", action="store_true", help="fail if schemas/ drifted from the types")
    ap.add_argument("--dir", type=Path, default=SCHEMA_DIR)
    a = ap.parse_args(argv)
    if a.write:
        print(f"wrote {write_all(a.dir)} files to {a.dir}")
        return 0
    problems = check(a.dir)
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
