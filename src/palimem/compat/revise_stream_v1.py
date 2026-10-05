"""The ``revise-stream-v1`` compatibility profile and adapter (T-E3).

The paper's contract (``status``, ``assertion``, ``alternatives``, ``provenance``) is output contract v1. This module
projects a v2 :class:`~palimem.types.Answer` onto it and defines the profile configuration under which the SDK must
reproduce the study's frozen numbers (gate G1). **Everything here that is not a pure projection is profile-specific
and is not part of the product contract.**

Profile configuration (S-02, S-03, S-04, S-08, S-12):

* semantics: ``SemanticConfig(profile=revise-stream-v1)``; ``self_update`` off is the paper's P0c, on is P0cSU;
* admission: origin-based authority (the paper's rules), ``blocked`` -> ``excluded``,
  ``acting_reports_must_be_live = false`` (a withdrawn correction still withdraws its target: 387 retracted
  corrections in Setting 1, 84 of 314 comparable streams differ otherwise);
* per-slot-type closed-world conventions (single/no evidence -> ``unknown``; multi/no evidence ->
  ``established_empty``) are applied by the kernel's classification under this profile;
* inertia on every attribute (the deposited code applies the law of inertia everywhere; this differs from the
  S-08 decision text and is flagged for the author).

Three paper features have no home in the v2 contract. Each is reproduced by a **compat-only convention carried in the
evidence log itself**, so the log stays replayable, exportable and verifiable:

1. **Source-level retraction.** The paper's ``retract(target = source id)`` removes every assertion of a source,
   including ones it makes later. The contract's ``withdraw`` targets a report id, so the compat driver appends a
   *marker report*: an assertion ``source_status(<source>) = "retracted"`` on the reserved attribute
   :data:`SOURCE_STATUS_ATTR`. :class:`CompatAdmitter` treats every report of a marked source as withdrawn from that
   log position on (and a report appended later by it, too), while leaving admission records and the actors' own
   effects (A-SELF) untouched, as the paper does.
2. **The ``from`` value of a ``change`` cue** (``Report`` has no field for it; contract gap 1): carried in
   ``Report.raw_ref`` as ``palimem:compat:change_from:<json>`` and read by :func:`change_from_of`.
3. **Attribute kinds the contract classes cannot express** (multi-valued *changeable* keys, derived cardinality,
   ``error_allowed`` / ``competing_values``): the compat driver passes a full :class:`KernelSchema` next to the
   contract :class:`Schema`.

Yes/no slots (``holds``, ``changed``, ``erroneous``) have no v2 query form: :func:`yesno_v1` projects the kernel's truth
sets (from ``Memory.justification``) onto ``possible`` / ``established``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from types import MappingProxyType
from typing import Any

from palimem.admission import AdmissionConfig, Admitter, Evaluation, Withdrawal
from palimem.kernel import AttrSpec, KernelSchema
from palimem.kernel.spec import rule_fn
from palimem.types import (
    AdmissionOutcome,
    Answer,
    Attr,
    AttrClass,
    Cue,
    EmptyForm,
    KernelStatus,
    Key,
    LogEntry,
    Origin,
    Profile,
    Report,
    Resolved,
    Rule,
    Schema,
    SemanticConfig,
    SetForm,
    Source,
    ValueForm,
    ValueProp,
    ValueType,
)
from palimem.types import Segment as PSegment
from palimem.types._codec import Value

PROFILE = Profile.REVISE_STREAM_V1
SOURCE_STATUS_ATTR = "__source_status__"
RETRACTED = "retracted"
CHANGE_FROM_PREFIX = "palimem:compat:change_from:"
HOST_SOURCE = Source(id="host", cls="trusted")


class CompatError(ValueError):
    """An answer cannot be projected onto the v1 contract."""


# --------------------------------------------------------------------------- profile configuration


def compat_semantic(*, self_update: bool = False) -> SemanticConfig:
    """The study's P0c (default) or P0cSU under the compat profile."""
    return SemanticConfig(semantics="v0.3", self_update=self_update, profile=PROFILE)


def compat_admission_config(*, admission_version: int = 1, acting_reports_must_be_live: bool = False) -> AdmissionConfig:
    """Admission under the compat profile (paper's authority rules; withdrawn actors keep acting)."""
    return AdmissionConfig(
        admission_version=admission_version, profile=PROFILE, acting_reports_must_be_live=acting_reports_must_be_live
    )


# --------------------------------------------------------------------------- compat-only conventions in the log


def change_from_of(report: Report) -> Value | None:
    """The ``from`` value of a ``change`` cue (compat convention, contract gap 1), or ``None``."""
    ref = report.raw_ref
    if report.cue is not Cue.CHANGE or ref is None or not ref.startswith(CHANGE_FROM_PREFIX):
        return None
    v = json.loads(ref[len(CHANGE_FROM_PREFIX):])
    if isinstance(v, bool | int | float | str):
        return v
    raise CompatError(f"change_from must be a scalar, got {v!r}")


def with_change_from(report: Report, value: Value) -> Report:
    """``report`` carrying the ``from`` value of its ``change`` cue."""
    from dataclasses import replace

    return replace(report, raw_ref=CHANGE_FROM_PREFIX + json.dumps(value))


def source_retraction_report(source_id: str, *, source: Source = HOST_SOURCE, actor: str = "system:compat") -> Report:
    """The marker report for the paper's source-level retraction (see the module docstring)."""
    from palimem.types import ValueProp as _VP

    return Report(
        key=Key(entity=source_id, attr=SOURCE_STATUS_ATTR), cue=Cue.ASSERT, proposition=_VP(value=RETRACTED),
        source=source, origin=Origin.EXTERNAL_OBSERVATION, origin_group=source.id, actor=actor,
    )


class CompatAdmitter(Admitter):
    """Admission plus the paper's source-level retraction.

    Every report whose source has been marked retracted (by an admitted marker report at or before the evaluated
    position) leaves the evidence set, whether it was reported before or after the marker. The reports' admission
    decisions and the actors' effects are those of the base admitter, which is what the paper does (a retracted
    source's correction still withdraws its same-origin target under ``acting_reports_must_be_live = false``).
    This subclass overrides the base class's internal ``_evaluate`` hook; it is profile-specific by design.
    """

    def _evaluate(self, entries: list[LogEntry], as_of: int | None) -> Evaluation:
        ev = super()._evaluate(entries, as_of)
        retracted: dict[str, str] = {}
        for e in ev.entries:
            r = e.report
            rid = r.id
            assert rid is not None
            if (
                r.key.attr == SOURCE_STATUS_ATTR
                and isinstance(r.proposition, ValueProp)
                and r.proposition.value == RETRACTED
                and ev.decisions[rid].record.outcome is AdmissionOutcome.ADMISSIBLE
                and rid not in ev.withdrawn
            ):
                retracted.setdefault(r.key.entity, rid)
        if not retracted:
            return ev
        withdrawn = dict(ev.withdrawn)
        for e in ev.entries:
            rid = e.report.id
            assert rid is not None
            by = retracted.get(e.report.source.id)
            if by is not None and e.report.key.attr != SOURCE_STATUS_ATTR:
                withdrawn.setdefault(rid, Withdrawal(by=by, kind="source_withdraw"))
        return Evaluation(
            admission_version=ev.admission_version, as_of_lsn=ev.as_of_lsn, entries=ev.entries,
            decisions=ev.decisions, withdrawn=MappingProxyType(withdrawn),
        )


# --------------------------------------------------------------------------- schema conversion


def kernel_schema_with_marker(ks: KernelSchema) -> KernelSchema:
    """``ks`` plus the reserved source-status attribute the retraction markers are asserted on."""
    attrs = dict(ks.attrs)
    attrs.setdefault(SOURCE_STATUS_ATTR, AttrSpec(name=SOURCE_STATUS_ATTR, cardinality="single", changeable=False))
    return KernelSchema(attrs=attrs, rules=ks.rules, entities=ks.entities)


def schema_from_kernel(ks: KernelSchema, *, version: int = 1) -> Schema:
    """The best contract :class:`Schema` for a kernel schema (the contract classes cannot express every paper
    attribute kind, so the kernel schema is passed alongside and wins for justification)."""
    attrs: list[Attr] = []
    for name, spec in ks.attrs.items():
        if spec.derived:
            rules = ks.rules_for(name)
            reads = sorted({a for r in rules for (a, _e, _v) in (*r.body, *r.exceptions)})
            attrs.append(
                Attr(
                    name=name, attr_class=AttrClass.DERIVED, value_type=ValueType.STRING, inertia=True,
                    rule=Rule(reads=tuple(reads), fn=rule_fn(spec.cardinality, rules)),
                )
            )
            continue
        if spec.cardinality == "multi":
            cls = AttrClass.MULTI_SET
        elif spec.changeable:
            cls = AttrClass.SINGLE_CHANGEABLE
        else:
            cls = AttrClass.SINGLE_STABLE
        attrs.append(Attr(name=name, attr_class=cls, value_type=ValueType.STRING, inertia=True))
    return Schema(version=version, attrs=tuple(attrs))


# --------------------------------------------------------------------------- v2 answer -> v1 contract


def _val(f: ValueForm | SetForm | EmptyForm | Any, multi: bool) -> Any:
    if isinstance(f, EmptyForm):
        return [] if multi else None
    if isinstance(f, ValueForm):
        return f.value
    return sorted(f.values, key=str)


def segment_v1(seg: PSegment, multi: bool) -> dict[str, Any]:
    """The v1 answer of one kernel segment (S-04 per-slot-type table). ``established_empty`` projects to
    ``established`` with the empty assertion, which is the paper's closed-world convention."""
    st = seg.kernel_status
    if st is KernelStatus.UNKNOWN:
        return {"status": "unknown", "assertion": None, "alternatives": []}
    if st is KernelStatus.UNRESOLVED:
        return {"status": "unresolved", "assertion": None, "alternatives": [_val(c.form, multi) for c in seg.alternatives]}
    assert seg.established is not None
    if st is KernelStatus.ESTABLISHED_FALSE:
        return {"status": "established", "assertion": False, "alternatives": []}
    return {"status": "established", "assertion": _val(seg.established.form, multi), "alternatives": []}


def answer_v1(answer: Answer, *, multi: bool) -> dict[str, Any]:
    """Project a v2 value answer onto the v1 contract, from the **kernel's** segment (the policy's decision is not
    part of the paper's contract). A ``ResourceLimited`` answer has no v1 form."""
    if not isinstance(answer, Resolved):
        raise CompatError(f"ResourceLimited({answer.reason.value}) has no v1 projection")
    return segment_v1(answer.justified.segment, multi)


def yesno_v1(truths: Sequence[bool]) -> dict[str, Any]:
    """Yes/no slots from the kernel's truth sets: true in every interpretation -> established true; false in every
    one -> established false; mixed -> the adapter-only status ``possible``."""
    if truths and all(truths):
        return {"status": "established", "assertion": True, "alternatives": []}
    if truths and not any(truths):
        return {"status": "established", "assertion": False, "alternatives": []}
    if not truths:
        return {"status": "unknown", "assertion": None, "alternatives": []}
    return {"status": "possible", "assertion": True, "alternatives": []}


def reported_v1(entries: Sequence[LogEntry], study_id_of: Callable[[str], str]) -> dict[str, Any]:
    """The ``reported`` slot: every admitted, non-retracted reported value with the (study) report ids."""
    rep: dict[str, list[str]] = {}
    for e in entries:
        p = e.report.proposition
        if p is None or not hasattr(p, "value"):
            continue
        assert e.report.id is not None
        rep.setdefault(str(p.value), []).append(study_id_of(e.report.id))
    return {"status": "established", "assertion": {k: sorted(v) for k, v in sorted(rep.items())}, "alternatives": []}
