"""The justification kernel (Lane B): enumeration kernel, rule engine, static exactness check.

Standard library only. See ``docs/KERNEL.md``.
"""

from palimem.kernel.derive import (
    DerivedJustification,
    JustificationProvider,
    Provider,
    base_attrs_closure,
    justify_derived,
    relevant_base_keys,
)
from palimem.kernel.evidence import (
    EPOCH,
    Ev,
    cross_key_corrections,
    day_of,
    dt_of_day,
    evidence_from_entries,
)
from palimem.kernel.exactness import (
    ExactnessViolation,
    RuleExceptionsReserved,
    check_schema,
    find_overlaps,
    reject_reserved_rule_features,
)
from palimem.kernel.justify import (
    Justification,
    ResourceLimitedResult,
    canonical_environment,
    classify,
    explain_at,
    justify_key,
    policy_of,
)
from palimem.kernel.provenance import (
    DEFAULT_ENV_CAP,
    EnvBudget,
    EnvEngine,
    SupportProvider,
    flatten,
    minimize,
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
    "DEFAULT_ENV_CAP",
    "EPOCH",
    "AttrSpec",
    "DerivedJustification",
    "EnvBudget",
    "EnvEngine",
    "Ev",
    "ExactnessViolation",
    "Justification",
    "JustificationProvider",
    "KernelSchema",
    "KernelUnsupported",
    "Provider",
    "ResourceLimitedResult",
    "RuleExceptionsReserved",
    "RuleSpec",
    "SupportProvider",
    "base_attrs_closure",
    "canonical_environment",
    "check_schema",
    "classify",
    "cross_key_corrections",
    "day_of",
    "dt_of_day",
    "evidence_from_entries",
    "explain_at",
    "find_overlaps",
    "flatten",
    "justify_derived",
    "justify_key",
    "minimize",
    "parse_rule_fn",
    "policy_of",
    "reject_reserved_rule_features",
    "relevant_base_keys",
    "rule_fn",
]
