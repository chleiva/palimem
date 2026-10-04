"""AdmissionRecord: the recorded decision of the admission stage (S-01, S-03, decided).

Outcomes are ``admissible | quarantined | excluded``. Every record carries a reason code and the
``admission_version`` it was decided under, exclusions especially (design v0.3: "Admission
decisions, including exclusions, are recorded with a reason and an admission version").

"Confirmed" is an admission record, not a report cue (S-01): a quarantined report becomes
admissible when an already admissible report of a *different origin group* with the same key and an
equivalent proposition exists, and the record names those confirming reports in ``confirmed_by``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Self

from palimem.types._codec import (
    Codec,
    ValidationError,
    as_enum,
    as_int,
    as_obj,
    as_str,
    check_nat,
    check_ulid,
    set_field,
    tuple_of,
)
from palimem.types.enums import AdmissionOutcome, AdmissionReason

_REASONS_FOR: dict[AdmissionOutcome, frozenset[AdmissionReason]] = {
    AdmissionOutcome.ADMISSIBLE: frozenset({AdmissionReason.ADMITTED, AdmissionReason.CONFIRMED}),
    AdmissionOutcome.QUARANTINED: frozenset({AdmissionReason.SOURCE_QUARANTINED}),
    AdmissionOutcome.EXCLUDED: frozenset(
        {
            AdmissionReason.ORIGIN_NOT_ADMISSIBLE,
            AdmissionReason.SOURCE_BLOCKED,
            AdmissionReason.AUTHORITY_FAILED,
            AdmissionReason.TARGET_MISSING,
        }
    ),
}


@dataclass(frozen=True, kw_only=True)
class AdmissionRecord(Codec):
    id: str  # ULID of this decision
    report_id: str
    outcome: AdmissionOutcome
    reason: AdmissionReason
    admission_version: int
    confirmed_by: tuple[str, ...] = ()  # present exactly when reason = confirmed

    def __post_init__(self) -> None:
        check_ulid(self.id, "admission.id")
        check_ulid(self.report_id, "admission.report_id")
        if not isinstance(self.outcome, AdmissionOutcome):
            raise ValidationError("admission.outcome: not an AdmissionOutcome")
        if not isinstance(self.reason, AdmissionReason):
            raise ValidationError("admission.reason: not an AdmissionReason")
        check_nat(self.admission_version, "admission.admission_version", minimum=1)
        if self.reason not in _REASONS_FOR[self.outcome]:
            raise ValidationError(f"admission: reason '{self.reason.value}' is not valid for outcome '{self.outcome.value}'")
        for i, r in enumerate(self.confirmed_by):
            check_ulid(r, f"admission.confirmed_by[{i}]")
        if (self.reason is AdmissionReason.CONFIRMED) != bool(self.confirmed_by):
            raise ValidationError("admission: confirmed_by is present exactly when reason = confirmed")
        if self.report_id in self.confirmed_by:
            raise ValidationError("admission: a report cannot confirm itself")
        set_field(self, "confirmed_by", tuple(sorted(set(self.confirmed_by))))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "report_id": self.report_id,
            "outcome": self.outcome.value,
            "reason": self.reason.value,
            "admission_version": self.admission_version,
            "confirmed_by": list(self.confirmed_by),
        }

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "admission record", ["id", "report_id", "outcome", "reason", "admission_version"], ["confirmed_by"])
        return cls(
            id=as_str(o["id"], "admission.id"),
            report_id=as_str(o["report_id"], "admission.report_id"),
            outcome=as_enum(AdmissionOutcome, o["outcome"], "admission.outcome"),
            reason=as_enum(AdmissionReason, o["reason"], "admission.reason"),
            admission_version=as_int(o["admission_version"], "admission.admission_version"),
            confirmed_by=tuple_of(o.get("confirmed_by", []), "admission.confirmed_by", as_str),
        )
