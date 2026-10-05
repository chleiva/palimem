"""Reopening a store: rebuild the versioned inputs (admission, policy, semantic) from what the backend stored.

``palimem.memory.Memory`` adopts inputs that are already stored and refuses a different one without a higher version.
The facade and the CLI therefore read the stored inputs back instead of guessing defaults. Only configurations the
public API can produce are supported (the ``revise-stream-v1`` compat profile with its kernel schema is not reopened).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from palimem.admission import AdmissionConfig, SourceStatus
from palimem.policy import PolicyObject, Selector
from palimem.store import Backend, InputKind
from palimem.types import AuthorityRule, Profile, Schema, SemanticConfig


def admission_from_payload(p: Mapping[str, Any]) -> AdmissionConfig:
    return AdmissionConfig(
        admission_version=int(p["admission_version"]),
        profile=Profile(p["profile"]),
        rules=tuple(AuthorityRule.from_dict(r) for r in p.get("rules", [])),
        class_status={k: SourceStatus(v) for k, v in dict(p.get("class_status", {})).items()},
        source_status={k: SourceStatus(v) for k, v in dict(p.get("source_status", {})).items()},
        acting_reports_must_be_live=p.get("acting_reports_must_be_live"),
        failed_correction_is_allege=p.get("failed_correction_is_allege"),
        dispute_is_denial=p.get("dispute_is_denial"),
    )


def policy_from_payload(p: Mapping[str, Any]) -> PolicyObject:
    return PolicyObject(
        version=int(p["version"]), name=str(p.get("name", "")), priors=dict(p["priors"]),
        abstain_threshold=float(p["abstain_threshold"]), ask_threshold=float(p["ask_threshold"]),
        utility=dict(p.get("utility", {})), selector=Selector(p["selector"]),
    )


def stored_inputs(
    backend: Backend,
) -> tuple[Schema | None, SemanticConfig | None, AdmissionConfig | None, PolicyObject | None]:
    """Whatever the store already holds, ``None`` for what it does not."""
    schema = backend.schema()
    sem = backend.input_at(InputKind.SEMANTIC)
    adm = backend.input_at(InputKind.ADMISSION)
    pol = backend.input_at(InputKind.POLICY)
    return (
        schema,
        None if sem is None else SemanticConfig.from_dict(dict(sem[1])),
        None if adm is None else admission_from_payload(adm[1]),
        None if pol is None else policy_from_payload(pol[1]),
    )
