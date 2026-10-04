"""The justification kernel (Lane B): enumeration kernel, rule engine, static exactness check.

Standard library only. See ``docs/KERNEL.md``.
"""

from palimem.kernel.derive import (
    DerivedJustification,
    JustificationProvider,
    Provider,
    base_attrs_closure,
    justify_derived,
)
from palimem.kernel.evidence import (
    EPOCH,
    Ev,
    cross_key_corrections,
    day_of,
    dt_of_day,
    evidence_from_entries,
)
from palimem.kernel.exactness import ExactnessViolation, check_schema, find_overlaps
from palimem.kernel.justify import (
    Justification,
    ResourceLimitedResult,
    classify,
    justify_key,
    policy_of,
)
from palimem.kernel.spec import (
    AttrSpec,
    KernelSchema,
    KernelUnsupported,
    RuleSpec,
    parse_rule_fn,
    rule_fn,
)

__all__ = [
    "EPOCH",
    "AttrSpec",
    "DerivedJustification",
    "Ev",
    "ExactnessViolation",
    "Justification",
    "JustificationProvider",
    "KernelSchema",
    "KernelUnsupported",
    "Provider",
    "ResourceLimitedResult",
    "RuleSpec",
    "base_attrs_closure",
    "check_schema",
    "classify",
    "cross_key_corrections",
    "day_of",
    "dt_of_day",
    "evidence_from_entries",
    "find_overlaps",
    "justify_derived",
    "justify_key",
    "parse_rule_fn",
    "policy_of",
    "rule_fn",
]
