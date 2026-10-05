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
    failed_correction_is_allege: bool | None = None
    """What a ``correct`` that fails the authority check becomes (S-02 implementation note, default pending the
    author's confirmation). ``True`` (the ``open-world`` default, design v0.3): an ``allege`` with no effect: it neither
    withdraws its target nor counts as a rival report. ``False`` (the paper's A-CORR, and the only behaviour of
    ``revise-stream-v1``): it stays an admissible report carrying a correction cue, a competing assertion. ``None``
    selects the profile default. The switch lets the author reverse the default in one line."""

    dispute_is_denial: bool | None = None
    """What an *authorised dispute* does to the kernel's evidence (author ruling 3 of 2026-10-05). ``True`` (the
    ``open-world`` default): the dispute is read as a denial of its target, so the target's candidate becomes unresolved
    against "disputed" until another origin group confirms it or the dispute is withdrawn (:mod:`.disputes`). ``False``:
    a dispute has no kernel effect (it stays audit-visible). ``revise-stream-v1`` ignores the flag: the paper's oracle has no
    disputes. ``None`` selects the profile default."""

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

    @property
    def failed_correction_allege(self) -> bool:
        if self.profile is Profile.REVISE_STREAM_V1:
            return False  # the paper's behaviour, byte for byte, whatever the flag says
        if self.failed_correction_is_allege is not None:
            return self.failed_correction_is_allege
        return True

    @property
    def dispute_denial(self) -> bool:
        if self.profile is Profile.REVISE_STREAM_V1:
            return False  # the paper's oracle has no disputes
        return True if self.dispute_is_denial is None else self.dispute_is_denial

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
