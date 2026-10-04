"""Strict validation of model output into :class:`ExtractedClaim`.

Policy: **repair syntax, never semantics.** The only local repair is syntactic (strip a markdown
fence or prose around a single JSON object). A claim that is semantically wrong (unknown field,
identity field, bad proposition, unsupported span) is *rejected* with a reason, never coerced.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from palimem.extract.claims import (
    ExtractedClaim,
    Rejection,
    TargetHint,
    parse_stated_date,
)
from palimem.types import Cue, Precision, ValidationError
from palimem.types.values import proposition_from_dict

#: Fields that give a report identity, authority or provenance. They must come from the host.
#: Seeing one in model output is an injection signal: the claim is rejected and flagged.
IDENTITY_FIELDS = frozenset({
    "source", "source_id", "source_class", "class", "origin", "origin_group", "actor", "principal",
    "authority", "id", "report_id", "target", "target_id", "extractor", "recorded_at", "lsn",
    "raw_ref", "prev_hash", "entry_hash", "trusted", "admit", "admission",
})
CLAIM_FIELDS = frozenset({"cue", "entity", "attr", "proposition", "valid_from", "valid_to", "target_hint", "span"})


@dataclass(frozen=True)
class ParseResult:
    claims: tuple[ExtractedClaim, ...]
    rejections: tuple[Rejection, ...]
    identity_fields_seen: bool
    repaired: bool
    output_invalid: bool  # nothing usable could be parsed from the output at all


def norm_text(s: str) -> str:
    return " ".join(s.split()).casefold()


def _strip_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def load_output_json(raw: str) -> tuple[Any, bool]:
    """Parse model output as JSON. Returns (value, repaired). Raises ValidationError.

    Repair is syntactic only: a markdown fence, or prose around one ``{...}`` object.
    """
    try:
        return json.loads(raw), False
    except json.JSONDecodeError:
        pass
    cand = _strip_fence(raw)
    try:
        return json.loads(cand), True
    except json.JSONDecodeError:
        pass
    lo, hi = cand.find("{"), cand.rfind("}")
    if lo != -1 and hi > lo:
        try:
            return json.loads(cand[lo : hi + 1]), True
        except json.JSONDecodeError:
            pass
    raise ValidationError("output is not valid JSON and no single JSON object could be recovered")


def claim_from_dict(d: Mapping[str, Any], *, require_span: bool = True) -> ExtractedClaim:
    """Strict decode of one claim object. Raises ValidationError."""
    if not isinstance(d, Mapping):
        raise ValidationError("claim: expected an object")
    ident = sorted(IDENTITY_FIELDS & set(d))
    if ident:
        raise ValidationError(f"claim: identity field(s) {ident} are bound by the host, never by the model")
    unknown = sorted(set(d) - CLAIM_FIELDS)
    if unknown:
        raise ValidationError(f"claim: unknown field(s) {unknown}")
    for req in ("cue", "entity", "attr"):
        if req not in d:
            raise ValidationError(f"claim: missing '{req}'")
    try:
        cue = Cue(d["cue"])
    except ValueError as e:
        raise ValidationError(f"claim: unknown cue {d['cue']!r}") from e
    for k in ("entity", "attr"):
        if not isinstance(d[k], str):
            raise ValidationError(f"claim.{k}: expected a string")
    prop = None if d.get("proposition") is None else proposition_from_dict(d["proposition"])

    vf = vt = None
    prec: Precision | None = None
    for name in ("valid_from", "valid_to"):
        raw = d.get(name)
        if raw is None:
            continue
        if not isinstance(raw, str):
            raise ValidationError(f"claim.{name}: expected a date string")
        t, p = parse_stated_date(raw, f"claim.{name}")
        if prec is not None and p is not prec:
            raise ValidationError("claim: valid_from and valid_to must use the same granularity")
        prec = p
        if name == "valid_from":
            vf = t
        else:
            vt = t

    hint = None
    if d.get("target_hint") is not None:
        h = d["target_hint"]
        if not isinstance(h, Mapping):
            raise ValidationError("claim.target_hint: expected an object")
        extra = sorted(set(h) - {"entity", "attr", "value"})
        if extra:
            raise ValidationError(f"claim.target_hint: unknown field(s) {extra}")
        if not isinstance(h.get("entity"), str):
            raise ValidationError("claim.target_hint.entity: expected a string")
        hint = TargetHint(entity=h["entity"], attr=h.get("attr"), value=h.get("value"))

    span = d.get("span")
    if span is not None and not isinstance(span, str):
        raise ValidationError("claim.span: expected a string")
    if require_span and (span is None or not span.strip()):
        raise ValidationError("claim: missing 'span' (a verbatim quote from the text)")

    return ExtractedClaim(
        cue=cue, entity=d["entity"], attr=d["attr"], proposition=prop,
        valid_from=vf, valid_to=vt, precision=prec or Precision.DAY, target_hint=hint, span=span,
    )


def parse_claims(raw: str, *, text: str | None, require_span: bool = True) -> ParseResult:
    """Parse ``{"claims": [...]}``. If ``text`` is given, every span must occur in it
    (whitespace- and case-insensitively), which stops invented evidence."""
    try:
        doc, repaired = load_output_json(raw)
    except ValidationError as e:
        return ParseResult((), (Rejection(reason="invalid_json", detail=str(e)),), False, False, True)
    if isinstance(doc, list):  # tolerate a bare list: still the same strict per-claim validation
        items: Any = doc
    elif isinstance(doc, dict) and set(doc) == {"claims"} and isinstance(doc["claims"], list):
        items = doc["claims"]
    else:
        return ParseResult((), (Rejection(reason="invalid_shape", detail="expected {'claims': [...]}"),),
                           False, repaired, True)
    claims: list[ExtractedClaim] = []
    rej: list[Rejection] = []
    ident_seen = False
    hay = None if text is None else norm_text(text)
    for item in items:
        try:
            c = claim_from_dict(item, require_span=require_span)
            if hay is not None and c.span is not None and norm_text(c.span) not in hay:
                raise ValidationError("claim.span does not occur in the text")
        except ValidationError as e:
            msg = str(e)
            if isinstance(item, Mapping) and IDENTITY_FIELDS & set(item):
                ident_seen = True
                reason = "identity_field_in_output"
            elif "does not occur" in msg:
                reason = "unsupported_span"
            elif "missing 'span'" in msg:
                reason = "missing_span"
            else:
                reason = "invalid_claim"
            rej.append(Rejection(reason=reason, detail=msg, claim=dict(item) if isinstance(item, Mapping) else None))
            continue
        claims.append(c)
    return ParseResult(tuple(claims), tuple(rej), ident_seen, repaired, False)
