"""JSON Schema of a conformance fixture (draft 2020-12). Emitted to ``fixture.schema.json``."""

from __future__ import annotations

from typing import Any

GATES = ["G0", "G1", "G2"]
AREAS = [
    "retraction", "evidence_states", "temporal", "authority", "admission", "crash_recovery",
    "resource_limits", "entity_resolution", "deletion", "segments", "negative_evidence",
    "attribution", "outbox", "extraction", "security", "budget", "compat", "trust_boundary",
    "rules", "collapsing", "provenance", "integrity", "poisoning", "privacy",
]
STATUSES = ["active", "pending-decision", "shell"]
CAPABILITIES = [
    "budget_control",       # configure(limits)
    "crash_injection",      # append(crash=...) and crash/recover ops
    "completion_jobs",      # complete_jobs op
    "outbox",               # subscribe/deliver/ack
    "merge",                # merge/unmerge
    "delete",               # delete (tombstones)
    "extractor",            # observe_text with an extractor stub
    "hash_chain",           # verify_log, export_head
    "tamper_hook",          # tamper op (test-only)
    "backup_restore",       # checkpoint/restore_backup
    "collapse_toggle",      # configure(collapse=...)
    "principal_scopes",     # `as` on read ops
    "quotas",               # per-source quotas
    "raw_ref_resolver",     # resolve_raw_ref
    "profile_revise_stream_v1",
    "source_exclusion",     # exclude_source / restore_source (admission operations, ruling 2 of 2026-10-05)
]
OPS = [
    "configure", "append", "observe_text", "delete", "merge", "unmerge", "subscribe", "deliver",
    "ack", "crash", "recover", "checkpoint", "restore_backup", "tamper", "complete_jobs", "query",
    "explain", "reports", "verify_log", "export_head", "find", "subscriber_effects",
    "resolve_raw_ref", "verify_beliefs", "exclude_source", "restore_source",
]
OP_REQUIRED: dict[str, list[str]] = {
    "configure": [],
    "append": ["ref", "report"],
    "observe_text": ["text", "connector", "extractor_stub"],
    "delete": ["target", "actor", "reason"],
    "merge": ["merge_id", "entities", "actor"],
    "unmerge": ["merge_id", "actor"],
    "subscribe": ["plan_id", "keys"],
    "deliver": ["name"],
    "ack": ["from"],
    "crash": ["point"],
    "recover": [],
    "checkpoint": ["checkpoint_name"],
    "restore_backup": ["checkpoint_name"],
    "tamper": ["tamper"],
    "complete_jobs": [],
    "query": ["name", "query"],
    "explain": ["name", "key"],
    "reports": ["name", "filter"],
    "verify_log": ["name"],
    "export_head": ["name"],
    "find": ["name", "text"],
    "subscriber_effects": ["name"],
    "resolve_raw_ref": ["name", "raw_ref"],
    "verify_beliefs": ["name"],
    "exclude_source": ["source", "from_lsn", "reason"],
    "restore_source": ["source", "from_lsn", "reason"],
}

TS = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$"}
REFSTR = {"type": "string", "pattern": r"^\$[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$"}
KEY = {
    "type": "object", "required": ["entity", "attr"], "additionalProperties": False,
    "properties": {"entity": {"type": "string", "minLength": 1}, "attr": {"type": "string", "minLength": 1}},
}
SOURCE = {
    "type": "object", "required": ["id", "class"], "additionalProperties": False,
    "properties": {"id": {"type": "string", "minLength": 1}, "class": {"type": "string", "minLength": 1}},
}


def _report() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["key", "proposition", "cue", "source", "origin", "origin_group", "actor"],
        "additionalProperties": False,
        "properties": {
            "key": KEY,
            "proposition": {"type": ["object", "null"]},
            "cue": {"enum": ["assert", "change", "correct", "withdraw", "dispute", "allege"]},
            "target": {"anyOf": [REFSTR, {"type": "null"}]},
            "source": SOURCE,
            "origin": {"enum": ["external_observation", "attributed", "agent_hypothesis", "agent_statement",
                                "plan", "simulation", "counterfactual"]},
            "origin_group": {"type": "string", "minLength": 1},
            "actor": {"type": "string", "pattern": r"^(agent|user|connector|system):.+$"},
            "valid_from": {"anyOf": [TS, {"type": "null"}]},
            "valid_to": {"anyOf": [TS, {"type": "null"}]},
            "precision": {"enum": ["day", "month", "year"]},
            "observed_at": {"anyOf": [TS, {"type": "null"}]},
            "raw_ref": {"type": ["string", "null"]},
            "extractor": {"type": ["object", "null"]},
        },
    }


def fixture_schema() -> dict[str, Any]:
    op_props = {
        "op": {"enum": OPS}, "name": {"type": "string", "pattern": r"^[A-Za-z0-9_{}]+$"},
        "ref": {"type": "string", "pattern": r"^[A-Za-z0-9_{}]+$"}, "at": TS,
        "expect": {"type": "object"}, "repeat": {"type": "integer", "minimum": 2},
        "report": _report(), "idempotency_key": {"type": "string"},
        "crash": {"type": "object", "required": ["point"], "additionalProperties": False,
                  "properties": {"point": {"type": "string"}}},
        "limits": {"type": "object"}, "collapse": {"type": "boolean"},
        "text": {"type": "string"}, "connector": {"type": "string"},
        "extractor_stub": {"type": "array", "items": _report()},
        "target": {"anyOf": [REFSTR, {"type": "string"}]}, "actor": {"type": "string"},
        "reason": {"type": "string"}, "merge_id": {"type": "string"},
        "entities": {"type": "array", "items": {"type": "string"}, "minItems": 2},
        "canonical": {"type": "string"}, "plan_id": {"type": "string"},
        "keys": {"type": "array", "items": KEY}, "point": {"type": "string"},
        "generation": {"anyOf": [REFSTR, {"type": "integer"}]},
        "query": {"type": "object"}, "filter": {"type": "object"},
        "from": {"type": "string"}, "to": {"type": "string"},
        "as": {"type": "object"}, "key": KEY, "mode": {"enum": ["all", "one"]},
        "depth": {"type": ["integer", "null"]}, "valid_at": {"anyOf": [TS, {"type": "null"}]},
        "belief_as_of": {"type": ["integer", "string", "null"]},
        "tamper": {"type": "object"}, "checkpoint_name": {"type": "string"},
        "from_lsn": {"anyOf": [REFSTR, {"type": "integer"}]},
        "to_lsn": {"anyOf": [REFSTR, {"type": "integer"}]},
        "raw_ref": {"type": "string"}, "expected_head": {"type": "string"}, "source": {"type": "string"},
    }
    ops = {
        "type": "array",
        "items": {
            "type": "object", "required": ["op"], "properties": op_props, "additionalProperties": False,
            "allOf": [
                {"if": {"properties": {"op": {"const": k}}, "required": ["op"]}, "then": {"required": v}}
                for k, v in OP_REQUIRED.items() if v
            ],
        },
    }
    common = {
        "id": {"type": "string", "pattern": r"^[a-z0-9]+(-[a-z0-9]+)*$"},
        "title": {"type": "string", "minLength": 8},
        "gate": {"enum": GATES}, "area": {"enum": AREAS}, "status": {"enum": STATUSES},
        "status_reason": {"type": "string", "minLength": 8},
        "covers": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "requires": {"type": "array", "items": {"enum": CAPABILITIES}, "uniqueItems": True},
        "spec_dependencies": {"type": "array", "items": {"type": "string"}},
        "why": {"type": "string", "minLength": 40},
        "source": {"type": "string", "minLength": 3},
    }
    scenario = {
        "type": "object", "additionalProperties": False,
        "required": ["version", "kind", "id", "title", "gate", "area", "status", "covers", "requires",
                     "spec_dependencies", "why", "source", "setup", "ops"],
        "properties": {
            **common, "version": {"const": 1}, "kind": {"const": "scenario"},
            "setup": {
                "type": "object", "additionalProperties": False, "required": ["profile", "schema"],
                "properties": {
                    "profile": {"enum": ["open-world", "revise-stream-v1"]},
                    "schema": {"type": "object", "minProperties": 1},
                    "sources": {"type": "object"},
                    "authority": {"type": "array", "items": {"type": "object"}},
                    "limits": {"type": "object"},
                    "connectors": {"type": "object"},
                    "scopes": {"type": "object"},
                    "expect_load_error": {"type": "object", "required": ["reason"], "properties": {"reason": {"type": "string"}}},
                },
            },
            "ops": ops,
            "expect": {
                "type": "object", "additionalProperties": False,
                "properties": {"equal": {"type": "array", "items": {
                    "type": "object", "required": ["a", "b", "fields"], "additionalProperties": False,
                    "properties": {"a": {"type": "string"}, "b": {"type": "string"},
                                   "fields": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                                   "negate": {"type": "boolean"}}}}},
            },
        },
        "allOf": [
            {"if": {"properties": {"status": {"enum": ["pending-decision", "shell"]}}}, "then": {"required": ["status_reason"]}},
            {"if": {"properties": {"setup": {"required": ["expect_load_error"]}}, "required": ["setup"]},
             "then": {"properties": {"ops": {"maxItems": 0}}}, "else": {"properties": {"ops": {"minItems": 1}}}},
        ],
    }
    harness_check = {
        "type": "object", "additionalProperties": False,
        "required": ["version", "kind", "id", "title", "gate", "area", "status", "covers", "requires",
                     "spec_dependencies", "why", "source", "check"],
        "properties": {
            **common, "version": {"const": 1}, "kind": {"const": "harness_check"},
            "check": {"type": "object", "required": ["runner", "description"],
                      "properties": {"runner": {"type": "string"}, "description": {"type": "string"}}},
        },
        "if": {"properties": {"status": {"enum": ["pending-decision", "shell"]}}},
        "then": {"required": ["status_reason"]},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://github.com/chleiva/palimem/tests/conformance/fixture.schema.json",
        "title": "palimem conformance fixture",
        "oneOf": [scenario, harness_check],
    }


def index_schema() -> dict[str, Any]:
    """Schema of ``fixtures/security/index.json`` (disposition of every SEC-xx item)."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object", "required": ["version", "items"], "additionalProperties": False,
        "properties": {
            "version": {"const": 1},
            "items": {
                "type": "array",
                "items": {
                    "type": "object", "required": ["id", "disposition"], "additionalProperties": False,
                    "properties": {
                        "id": {"type": "string", "pattern": r"^SEC-\d{2}$"},
                        "disposition": {"enum": ["fixture", "trust_boundary", "not_expressible"]},
                        "fixtures": {"type": "array", "items": {"type": "string"}},
                        "reason": {"type": "string"},
                    },
                    "allOf": [
                        {"if": {"properties": {"disposition": {"const": "not_expressible"}}, "required": ["disposition"]},
                         "then": {"required": ["reason"]}},
                        {"if": {"properties": {"disposition": {"enum": ["fixture", "trust_boundary"]}}, "required": ["disposition"]},
                         "then": {"required": ["fixtures"]}},
                    ],
                },
            },
        },
    }
