"""Admission configuration: everything admission reasons over, versioned as one unit.

Per the design, an admission decision depends on the admission version: the source-class table, the
per-source overrides (``set_source_class``), the authority grant table (S-07) and the profile all
change what the kernel reasons over, so changing any of them is a new ``admission_version``
(:meth:`AdmissionConfig.successor`); old reports are never silently re-evaluated.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import Any

from palimem.types import (
    AuthorityRule,
    AuthorityTable,
    Profile,
    Source,
    ValidationError,
    default_authority_rules,
    validate_rules_for_profile,
)
from palimem.types._codec import check_nat


class SourceStatus(str, Enum):
    NORMAL = "normal"
    QUARANTINED = "quarantined"  # logged, admissible only once confirmed (S-01)
    BLOCKED = "blocked"  # the paper's `blocked`: excluded, never confirmable (S-03)


DEFAULT_CLASS_STATUS: Mapping[str, SourceStatus] = MappingProxyType(
    {"quarantined": SourceStatus.QUARANTINED, "blocked": SourceStatus.BLOCKED}
)


def _frozen(m: Mapping[str, SourceStatus]) -> Mapping[str, SourceStatus]:
    for k, v in m.items():
        if not isinstance(k, str) or not isinstance(v, SourceStatus):
            raise ValidationError("admission config: status tables map str -> SourceStatus")
    return MappingProxyType(dict(m))


def _no_overrides() -> Mapping[str, SourceStatus]:
    return MappingProxyType({})


@dataclass(frozen=True, kw_only=True)
class AdmissionConfig:
    admission_version: int = 1
    profile: Profile = Profile.OPEN_WORLD
    rules: tuple[AuthorityRule, ...] = ()
    """The global grant table below any per-attribute rules; empty means the profile default
    (``default_authority_rules``): own source only in ``open-world`` (S-02), the paper's rules in
    ``revise-stream-v1``."""
    class_status: Mapping[str, SourceStatus] = field(default_factory=lambda: DEFAULT_CLASS_STATUS)
    source_status: Mapping[str, SourceStatus] = field(default_factory=_no_overrides)
    """Per-source overrides by source id (the host's ``set_source_class``); they win over the class."""
    acting_reports_must_be_live: bool | None = None
    """S-02's retracted-correction quirk. ``True`` (the product default): a withdraw or correction
    acts only if it has not itself been withdrawn, so withdrawing a correction restores its
    target. ``False`` (the paper's behaviour, the default of ``revise-stream-v1``): withdrawn
    actors keep acting. ``None`` selects the profile default."""

    def __post_init__(self) -> None:
        check_nat(self.admission_version, "admission.admission_version", minimum=1)
        if not isinstance(self.profile, Profile):
            raise ValidationError("admission config: profile is not a Profile")
        for r in self.rules:
            if not isinstance(r, AuthorityRule):
                raise ValidationError("admission config: rules must be AuthorityRule objects")
        validate_rules_for_profile(self.rules, self.profile, "admission config")
        object.__setattr__(self, "class_status", _frozen(self.class_status))
        object.__setattr__(self, "source_status", _frozen(self.source_status))

    @property
    def must_be_live(self) -> bool:
        if self.acting_reports_must_be_live is not None:
            return self.acting_reports_must_be_live
        return self.profile is Profile.OPEN_WORLD

    def effective_rules(self) -> tuple[AuthorityRule, ...]:
        return self.rules or default_authority_rules(self.profile)

    def table(self) -> AuthorityTable:
        return AuthorityTable(admission_version=self.admission_version, rules=self.effective_rules())

    def status_of(self, source: Source) -> SourceStatus:
        if source.id in self.source_status:
            return self.source_status[source.id]
        return self.class_status.get(source.cls, SourceStatus.NORMAL)

    def successor(self, **changes: Any) -> AdmissionConfig:
        """A changed configuration is a new admission version; the old one stays answerable."""
        if "admission_version" in changes:
            raise ValueError("admission_version is assigned by successor()")
        return replace(self, admission_version=self.admission_version + 1, **changes)
