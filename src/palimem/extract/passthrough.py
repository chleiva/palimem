"""The no-LLM path: already-typed input, same strict grammar, same host-side binding."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from palimem.extract.base import ExtractionResult
from palimem.extract.build import build_reports
from palimem.extract.claims import ExtractedClaim, Rejection
from palimem.extract.context import ExtractionContext
from palimem.extract.parse import claim_from_dict, parse_claims
from palimem.types import ValidationError
from palimem.types.report import Extractor as ExtractorStamp

PASSTHROUGH_STAMP = ExtractorStamp(
    model="typed-passthrough", version="1",
    prompt_hash=hashlib.sha256(b"typed-passthrough/1").hexdigest(),
)


class TypedPassthrough:
    """For users who already produce typed propositions.

    Accepts ``ExtractedClaim`` objects, claim dicts, or a JSON text ``{"claims": [...]}``. It is the
    same strict grammar as the LLM path (a key outside the grammar is rejected; no coercion), only without
    the model and without a required ``span``. Identity is bound by the host from ``ctx``.
    """

    def extract(self, text: str, ctx: ExtractionContext) -> ExtractionResult:
        pr = parse_claims(text, text=None, require_span=False)
        return self._finish(pr.claims, pr.rejections, pr.identity_fields_seen, ctx, pr.unexpected_fields)

    def extract_claims(
        self, claims: Sequence[ExtractedClaim | Mapping[str, Any]], ctx: ExtractionContext
    ) -> ExtractionResult:
        ok: list[ExtractedClaim] = []
        rej: list[Rejection] = []
        for c in claims:
            if isinstance(c, ExtractedClaim):
                ok.append(c)
                continue
            try:
                ok.append(claim_from_dict(c, require_span=False))
            except ValidationError as e:
                rej.append(Rejection(reason="invalid_claim", detail=str(e), claim=dict(c)))
        return self._finish(tuple(ok), tuple(rej), False, ctx)

    @staticmethod
    def _finish(
        claims: tuple[ExtractedClaim, ...], rejections: tuple[Rejection, ...], ident: bool, ctx: ExtractionContext,
        unexpected: tuple[str, ...] = (),
    ) -> ExtractionResult:
        reports, rej2, notes = build_reports(claims, ctx, PASSTHROUGH_STAMP)
        return ExtractionResult(
            reports=reports, rejections=rejections + rej2, notes=notes, claims=claims,
            identity_fields_seen=ident, stamp=PASSTHROUGH_STAMP, usage=None, calls=0, unexpected_fields=unexpected,
        )
