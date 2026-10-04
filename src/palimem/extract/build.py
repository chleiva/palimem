"""Host-side binding: turn proposed claims into :class:`Report`s.

This is where identity enters. ``source``, ``origin``, ``origin_group``, ``actor`` and
``raw_ref`` come from the :class:`ExtractionContext`; the target *id* of a correct/withdraw/
dispute comes from the host's resolver; the extractor stamp is attached. Nothing here reads
identity from model output, and nothing is silently coerced: a claim that cannot be bound
legitimately is rejected with a reason.
"""

from __future__ import annotations

from palimem.extract.claims import ExtractedClaim, Rejection
from palimem.extract.context import ExtractionContext
from palimem.types import (
    Cue,
    Key,
    Origin,
    Precision,
    Report,
    ValidationError,
    check_proposition_for_attr,
)
from palimem.types.report import Extractor as ExtractorStamp
from palimem.types.values import BeliefOfProp

_FIRST_PERSON = frozenset({"i", "me", "my", "myself", "we", "us", "our", "the speaker", "speaker", "the user", "user"})
_NEEDS_TARGET = frozenset({Cue.CORRECT, Cue.WITHDRAW, Cue.DISPUTE})


def _reject(c: ExtractedClaim, reason: str, detail: str) -> Rejection:
    return Rejection(reason=reason, detail=detail, claim=c.to_dict())


def build_reports(
    claims: tuple[ExtractedClaim, ...], ctx: ExtractionContext, stamp: ExtractorStamp | None
) -> tuple[tuple[Report, ...], tuple[Rejection, ...], tuple[str, ...]]:
    """Returns (reports, rejections, notes). Notes record non-fatal downgrades (e.g. dropped valid time)."""
    reports: list[Report] = []
    rejections: list[Rejection] = []
    notes: list[str] = []
    for c in claims:
        # 1. authority-bearing cues are accepted only where the host registered the connector for them
        if c.cue not in ctx.allowed_cues:
            rejections.append(_reject(c, "cue_not_permitted",
                                      f"cue '{c.cue.value}' is not allowed from this connector's extracted text"))
            continue

        # 2. first person resolves to the host-provided speaker entity, or the claim is refused
        entity = c.entity
        if entity.strip().casefold() in _FIRST_PERSON:
            if ctx.subject_entity is None:
                rejections.append(_reject(c, "unresolved_pronoun", "first person with no subject_entity in context"))
                continue
            entity = ctx.subject_entity

        # 3. schema conformance
        if ctx.schema is not None:
            try:
                attr = ctx.schema.attr(c.attr)
            except KeyError:
                rejections.append(_reject(c, "undeclared_attr", f"attribute '{c.attr}' is not declared in the schema"))
                continue
            if c.proposition is not None:
                try:
                    check_proposition_for_attr(attr, c.proposition)
                except ValidationError as e:
                    rejections.append(_reject(c, "form_mismatch", str(e)))
                    continue

        # 4. origin is host-bound; an attributed claim can only carry an attribution
        origin = ctx.origin
        if isinstance(c.proposition, BeliefOfProp) and origin is Origin.EXTERNAL_OBSERVATION:
            origin = Origin.ATTRIBUTED
        if origin is Origin.ATTRIBUTED and not isinstance(c.proposition, BeliefOfProp):
            rejections.append(_reject(c, "origin_requires_attribution",
                                      "context origin is 'attributed' but the claim is not a belief_of"))
            continue

        # 5. valid time must be plausible (T-09); out-of-bounds hints are dropped, not trusted
        vf, vt, prec = c.valid_from, c.valid_to, c.precision
        lo, hi = ctx.valid_time_bounds
        if any(t is not None and ((lo is not None and t < lo) or (hi is not None and t > hi)) for t in (vf, vt)):
            notes.append(f"valid time dropped for {entity}/{c.attr}: outside the connector's plausibility bounds")
            vf = vt = None
            prec = Precision.DAY

        # 6. targets are resolved by the host, never named by the text
        target: str | None = None
        if c.cue in _NEEDS_TARGET:
            assert c.target_hint is not None  # guaranteed by ExtractedClaim
            target = ctx.resolve_target(c.target_hint) if ctx.resolve_target is not None else None
            if target is None:
                rejections.append(_reject(c, "target_unresolved",
                                          "the host could not resolve the target hint to a report id"))
                continue

        try:
            reports.append(Report(
                key=Key(entity=entity, attr=c.attr), cue=c.cue, proposition=c.proposition, target=target,
                source=ctx.source, origin=origin, origin_group=ctx.origin_group, actor=ctx.actor,
                observed_at=ctx.observed_at, valid_from=vf, valid_to=vt, precision=prec,
                raw_ref=ctx.raw_ref, extractor=stamp,
            ))
        except ValidationError as e:
            rejections.append(_reject(c, "invalid_report", str(e)))
    return tuple(reports), tuple(rejections), tuple(notes)
