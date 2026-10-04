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
    identity_fields_seen: bool = False  # the model tried to set source/origin/actor/...: an injection signal
    stamp: ExtractorStamp | None = None
    usage: Usage | None = None
    calls: int = 0


@runtime_checkable
class Extractor(Protocol):
    """Text (plus host context) in, typed reports out.

    Implementations never decide identity: ``ctx`` carries source, origin, actor and
    authority, and the result's reports are built from it by :func:`palimem.extract.build_reports`.
    """

    def extract(self, text: str, ctx: ExtractionContext) -> ExtractionResult: ...
