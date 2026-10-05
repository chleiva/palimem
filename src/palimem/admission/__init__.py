"""The admission stage: ``evidence log -> admission -> kernel`` (T-D1, T-D2, T-D5).

See :mod:`palimem.admission.admitter` for the rules and :mod:`palimem.admission.authz` for authority.
"""

from palimem.admission.admitter import (
    EVIDENCE_CUES,
    AdmissionDecision,
    Admitter,
    Attribution,
    Evaluation,
    EvidenceSet,
    Withdrawal,
    kernel_view,
    origin_group_count,
)
from palimem.admission.authz import AuthDecision, Authorizer
from palimem.admission.config import DEFAULT_CLASS_STATUS, AdmissionConfig, SourceStatus
from palimem.admission.disputes import apply_disputes, dispute_view
from palimem.admission.equivalence import (
    equivalent,
    normalise_value,
    proposition_signature,
)
from palimem.admission.exclusion import (
    SOURCE_EXCLUSION_ATTR,
    Exclusion,
    ExclusionAdmitter,
    ExclusionNotEnabled,
    ExclusionNotHonoured,
    enable_source_exclusions,
    exclude_source,
    exclusion_attr_spec,
    exclusion_report,
    marker_text,
    restore_source,
    source_exclusions,
)
from palimem.admission.ids import derive_ulid
from palimem.admission.incremental import (
    AdmissionDelta,
    IncrementalAdmission,
    supports_incremental,
)
from palimem.admission.log import ListLog, LogView

__all__ = [
    "DEFAULT_CLASS_STATUS",
    "EVIDENCE_CUES",
    "SOURCE_EXCLUSION_ATTR",
    "AdmissionConfig",
    "AdmissionDecision",
    "AdmissionDelta",
    "Admitter",
    "Attribution",
    "AuthDecision",
    "Authorizer",
    "Evaluation",
    "EvidenceSet",
    "Exclusion",
    "ExclusionAdmitter",
    "ExclusionNotEnabled",
    "ExclusionNotHonoured",
    "IncrementalAdmission",
    "ListLog",
    "LogView",
    "SourceStatus",
    "Withdrawal",
    "apply_disputes",
    "derive_ulid",
    "dispute_view",
    "enable_source_exclusions",
    "equivalent",
    "exclude_source",
    "exclusion_attr_spec",
    "exclusion_report",
    "kernel_view",
    "marker_text",
    "normalise_value",
    "origin_group_count",
    "proposition_signature",
    "restore_source",
    "source_exclusions",
    "supports_incremental",
]
