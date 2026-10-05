"""The :class:`Extractor` protocol and its result type."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from palimem.extract.claims import ExtractedClaim, Rejection
from palimem.extract.context import ExtractionContext
from palimem.types import Report
from palimem.types.report import Extractor as ExtractorStamp


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass(frozen=True)
class ExtractionResult:
    reports: tuple[Report, ...]
    rejections: tuple[Rejection, ...] = ()
    notes: tuple[str, ...] = ()
    claims: tuple[ExtractedClaim, ...] = ()  # model-level output after strict parsing, before host policy
    #: Legacy name, kept so host code that reads it keeps working: True when the model emitted any key outside the
    #: claim grammar (the grammar has no field for source, origin, actor or authority, so any such key is the
    #: injection signal). ``unexpected_fields`` lists the offending key names (union over every reply of the call).
    identity_fields_seen: bool = False
    stamp: ExtractorStamp | None = None
    usage: Usage | None = None
    calls: int = 0
    unexpected_fields: tuple[str, ...] = ()
    repair_triggered: bool = False  # a second (repair) call was made
    repair_used: bool = False  # the repaired reply replaced the original (it had strictly fewer format errors)

    @property
    def identity_fields(self) -> tuple[str, ...]:
        return self.unexpected_fields


@runtime_checkable
class Extractor(Protocol):
    """Text (plus host context) in, typed reports out.

    Implementations never decide identity: ``ctx`` carries source, origin, actor and
    authority, and the result's reports are built from it by :func:`palimem.extract.build_reports`.
    """

    def extract(self, text: str, ctx: ExtractionContext) -> ExtractionResult: ...
