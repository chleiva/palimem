"""What an extractor may *propose*: claims, never identity.

An :class:`ExtractedClaim` carries only what the text can legitimately say: who/what it is about
(``entity``/``attr``), the proposition, an operator cue, stated valid time, and (for
correct/withdraw/dispute) a *hint* at the target. Everything that gives a report authority or
weight (``source``, ``origin``, ``origin_group``, ``actor``, the target *id*) is bound by the host
in :mod:`palimem.extract.build`; none of it is ever read from model output
(docs/THREAT_MODEL.md T-06/T-07/T-09, docs/API_TRUST_BOUNDARY.md).

Dates use the granularity the text states: ``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD`` (the
period's first instant). One precision per claim, so ``valid_from`` and ``valid_to`` must agree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from palimem.types import Cue, Precision, Proposition, ValidationError
from palimem.types._codec import Value, check_nonempty, check_value
from palimem.types.values import PROPOSITION_TYPES

#: Cues an extractor may emit. ``allege`` is a host-side downgrade, never an extractor output.
EXTRACTABLE_CUES = frozenset({Cue.ASSERT, Cue.CHANGE, Cue.CORRECT, Cue.WITHDRAW, Cue.DISPUTE})
_NEEDS_PROPOSITION = frozenset({Cue.ASSERT, Cue.CHANGE, Cue.CORRECT})
_NEEDS_TARGET = frozenset({Cue.CORRECT, Cue.WITHDRAW, Cue.DISPUTE})

_DATE_RES = (
    (re.compile(r"^\d{4}$"), Precision.YEAR, "%Y"),
    (re.compile(r"^\d{4}-\d{2}$"), Precision.MONTH, "%Y-%m"),
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), Precision.DAY, "%Y-%m-%d"),
)
_FORMAT = {Precision.YEAR: "%Y", Precision.MONTH: "%Y-%m", Precision.DAY: "%Y-%m-%d"}


def parse_stated_date(s: str, ctx: str) -> tuple[datetime, Precision]:
    """``'2024'``, ``'2024-03'`` or ``'2024-03-09'`` -> (first instant, precision)."""
    for rx, prec, fmt in _DATE_RES:
        if rx.match(s):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=UTC), prec
            except ValueError as e:
                raise ValidationError(f"{ctx}: not a real date: {s!r}") from e
    raise ValidationError(f"{ctx}: expected YYYY, YYYY-MM or YYYY-MM-DD, got {s!r}")


def format_stated_date(t: datetime, precision: Precision) -> str:
    return t.strftime(_FORMAT[precision])


@dataclass(frozen=True, kw_only=True)
class TargetHint:
    """Which earlier claim a correct/withdraw/dispute refers to. The *host* resolves it to a
    report id; the extractor can never name an id (it cannot know one)."""

    entity: str
    attr: str | None = None
    value: Value | None = None

    def __post_init__(self) -> None:
        check_nonempty(self.entity, "target_hint.entity")
        if self.attr is not None:
            check_nonempty(self.attr, "target_hint.attr")
        if self.value is not None:
            check_value(self.value, "target_hint.value")

    def to_dict(self) -> dict[str, Any]:
        return {"entity": self.entity, "attr": self.attr, "value": self.value}


@dataclass(frozen=True, kw_only=True)
class ExtractedClaim:
    cue: Cue
    entity: str
    attr: str
    proposition: Proposition | None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    precision: Precision = Precision.DAY
    target_hint: TargetHint | None = None
    span: str | None = None  # verbatim quote from the text that supports the claim

    def __post_init__(self) -> None:
        if self.cue not in EXTRACTABLE_CUES:
            raise ValidationError(f"claim: cue {self.cue.value!r} cannot be emitted by an extractor")
        check_nonempty(self.entity, "claim.entity")
        check_nonempty(self.attr, "claim.attr")
        if self.proposition is not None and not isinstance(self.proposition, PROPOSITION_TYPES):
            raise ValidationError("claim.proposition: not a Proposition")
        if self.cue in _NEEDS_PROPOSITION and self.proposition is None:
            raise ValidationError(f"claim: cue {self.cue.value!r} requires a proposition")
        if self.cue is Cue.WITHDRAW and self.proposition is not None:
            raise ValidationError("claim: a withdraw carries no proposition")
        if self.cue in _NEEDS_TARGET and self.target_hint is None:
            raise ValidationError(f"claim: cue {self.cue.value!r} requires a target_hint")
        if self.cue not in _NEEDS_TARGET and self.target_hint is not None:
            raise ValidationError(f"claim: cue {self.cue.value!r} must not carry a target_hint")
        if self.valid_from is not None and self.valid_to is not None and self.valid_from > self.valid_to:
            raise ValidationError("claim: valid_from is after valid_to")
        if self.span is not None:
            check_nonempty(self.span, "claim.span")

    def to_dict(self) -> dict[str, Any]:
        def fmt(t: datetime | None) -> str | None:
            return None if t is None else format_stated_date(t, self.precision)

        return {
            "cue": self.cue.value,
            "entity": self.entity,
            "attr": self.attr,
            "proposition": None if self.proposition is None else self.proposition.to_dict(),
            "valid_from": fmt(self.valid_from),
            "valid_to": fmt(self.valid_to),
            "target_hint": None if self.target_hint is None else self.target_hint.to_dict(),
            "span": self.span,
        }


@dataclass(frozen=True, kw_only=True)
class Rejection:
    """A claim (or a whole model output) that was refused, with the reason. Nothing is coerced."""

    reason: str
    detail: str = ""
    claim: dict[str, Any] | None = None
