"""Report (the unit of evidence) and LogEntry (a report as stored in the evidence log).

Design v0.3 lists ``id``, ``recorded_at`` and ``origin_group`` on Report. Two decisions change
that placement:

* S-05 (author, 2026-10-04): the *log sequence number* is the belief axis, and the lsn and the
  hash-chain fields (``prev_hash``, ``entry_hash``) are properties of the log row, not of the
  Report. ``recorded_at`` is likewise assigned by the log, so it lives on :class:`LogEntry` too;
  a Report is exactly what a caller hands to ``append``.
* ``Report.id`` is "assigned by the log": it is ``None`` on a report that has not been appended
  and a ULID afterwards. :class:`LogEntry` requires it.

Cue/target/proposition rules enforced here (design v0.3 §Write API, Admission model):

=========  ==========================  =========================
cue        target                      proposition
=========  ==========================  =========================
assert     must be absent              required
change     must be absent              required
correct    required                    required (the corrected value is asserted in the same notice)
withdraw   required                    must be absent
dispute    required                    optional (the competing claim, if any)
allege     required                    optional
=========  ==========================  =========================

A ``change`` cue may carry ``change_from``, the value the source says the attribute had before the
change (author ruling 2026-10-05, additive). It is optional, allowed only for cue ``change`` and omitted
from the canonical JSON when absent, so the bytes (and hash-chain commitments) of every earlier report
are unchanged.

There is no ``confirm`` cue (S-01): confirmation is derived and recorded as an AdmissionRecord.
``actor`` is a typed principal id ``<kind>:<name>`` (S-07).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Self

from palimem.types._codec import (
    Codec,
    ValidationError,
    Value,
    as_enum,
    as_int,
    as_obj,
    as_str,
    check_hex64,
    check_nat,
    check_nonempty,
    check_order,
    check_ulid,
    check_value,
    norm_ts,
    opt,
    opt_ts_str,
    req_ts,
    set_field,
    ts_from_str,
    ts_to_str,
)
from palimem.types.authority import check_principal
from palimem.types.enums import Cue, Origin, Precision
from palimem.types.values import (
    PROPOSITION_TYPES,
    BeliefOfProp,
    Key,
    Proposition,
    proposition_from_dict,
)

_TARGET_REQUIRED = {Cue.CORRECT, Cue.WITHDRAW, Cue.DISPUTE, Cue.ALLEGE}
_TARGET_FORBIDDEN = {Cue.ASSERT, Cue.CHANGE}
_PROPOSITION_REQUIRED = {Cue.ASSERT, Cue.CHANGE, Cue.CORRECT}
_PROPOSITION_FORBIDDEN = {Cue.WITHDRAW}


@dataclass(frozen=True, kw_only=True)
class Source(Codec):
    """Source identity. Comes from connector metadata, never from ingested text."""

    id: str
    cls: str  # source class, e.g. "trusted", "standard", "low", "quarantined"; JSON name "class"

    def __post_init__(self) -> None:
        check_nonempty(self.id, "source.id")
        check_nonempty(self.cls, "source.class")

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "class": self.cls}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "source", ["id", "class"])
        return cls(id=as_str(o["id"], "source.id"), cls=as_str(o["class"], "source.class"))


@dataclass(frozen=True, kw_only=True)
class Extractor(Codec):
    model: str
    version: str
    prompt_hash: str

    def __post_init__(self) -> None:
        check_nonempty(self.model, "extractor.model")
        check_nonempty(self.version, "extractor.version")
        check_nonempty(self.prompt_hash, "extractor.prompt_hash")

    def to_dict(self) -> dict[str, Any]:
        return {"model": self.model, "version": self.version, "prompt_hash": self.prompt_hash}

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "extractor", ["model", "version", "prompt_hash"])
        return cls(
            model=as_str(o["model"], "extractor.model"),
            version=as_str(o["version"], "extractor.version"),
            prompt_hash=as_str(o["prompt_hash"], "extractor.prompt_hash"),
        )


_REPORT_REQUIRED = ["key", "cue", "source", "origin", "origin_group", "actor"]
_REPORT_OPTIONAL = [
    "id", "proposition", "target", "observed_at", "valid_from", "valid_to", "precision", "raw_ref", "extractor",
    "change_from",
]


@dataclass(frozen=True, kw_only=True)
class Report(Codec):
    """Immutable evidence. A correction is a new report that points at the old one."""

    key: Key
    cue: Cue
    source: Source
    origin: Origin
    origin_group: str  # shared upstream origin; counts once for corroboration; never authority (S-02)
    actor: str  # typed principal id '<kind>:<name>' (S-07); set by the host, never by the LLM
    proposition: Proposition | None = None
    target: str | None = None
    observed_at: datetime | None = None  # the source's own time; audit only
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    precision: Precision = Precision.DAY
    raw_ref: str | None = None
    extractor: Extractor | None = None
    id: str | None = None  # ULID assigned by the log; None before append
    change_from: Value | None = None  # previous value stated by a `change` cue (author ruling 2026-10-05)

    def __post_init__(self) -> None:
        if not isinstance(self.key, Key):
            raise ValidationError("report.key: not a Key")
        if not isinstance(self.cue, Cue):
            raise ValidationError("report.cue: not a Cue")
        if not isinstance(self.source, Source):
            raise ValidationError("report.source: not a Source")
        if not isinstance(self.origin, Origin):
            raise ValidationError("report.origin: not an Origin")
        if not isinstance(self.precision, Precision):
            raise ValidationError("report.precision: not a Precision")
        check_nonempty(self.origin_group, "report.origin_group")
        check_principal(self.actor, "report.actor")
        if self.id is not None:
            check_ulid(self.id, "report.id")
        if self.target is not None:
            check_ulid(self.target, "report.target")
        if self.raw_ref is not None:
            check_nonempty(self.raw_ref, "report.raw_ref")
        if self.extractor is not None and not isinstance(self.extractor, Extractor):
            raise ValidationError("report.extractor: not an Extractor")
        if self.change_from is not None:
            check_value(self.change_from, "report.change_from")
            if self.cue is not Cue.CHANGE:
                raise ValidationError(f"report.change_from: only a 'change' cue may state a previous value, not '{self.cue.value}'")
        for name in ("observed_at", "valid_from", "valid_to"):
            set_field(self, name, norm_ts(getattr(self, name), f"report.{name}"))
        check_order(self.valid_from, self.valid_to, "report.valid_from/valid_to")

        if self.cue in _TARGET_REQUIRED and self.target is None:
            raise ValidationError(f"report: cue '{self.cue.value}' requires a target report id")
        if self.cue in _TARGET_FORBIDDEN and self.target is not None:
            raise ValidationError(f"report: cue '{self.cue.value}' must not carry a target")
        if self.id is not None and self.target == self.id:
            raise ValidationError("report: a report cannot target itself")

        if self.proposition is not None and not isinstance(self.proposition, PROPOSITION_TYPES):
            raise ValidationError("report.proposition: not a Proposition")
        if self.cue in _PROPOSITION_REQUIRED and self.proposition is None:
            raise ValidationError(f"report: cue '{self.cue.value}' requires a proposition")
        if self.cue in _PROPOSITION_FORBIDDEN and self.proposition is not None:
            raise ValidationError(f"report: cue '{self.cue.value}' must not carry a proposition")
        # Attributed reports establish only the attribution (design v0.3 Admission model).
        if self.origin is Origin.ATTRIBUTED and self.proposition is not None and not isinstance(self.proposition, BeliefOfProp):
            raise ValidationError("report: origin 'attributed' requires a belief_of proposition")

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "key": self.key.to_dict(),
            "proposition": None if self.proposition is None else self.proposition.to_dict(),
            "cue": self.cue.value,
            "target": self.target,
            "source": self.source.to_dict(),
            "origin": self.origin.value,
            "origin_group": self.origin_group,
            "actor": self.actor,
            "observed_at": opt_ts_str(self.observed_at),
            "valid_from": opt_ts_str(self.valid_from),
            "valid_to": opt_ts_str(self.valid_to),
            "precision": self.precision.value,
            "raw_ref": self.raw_ref,
            "extractor": None if self.extractor is None else self.extractor.to_dict(),
        }
        if self.change_from is not None:  # omitted when absent: earlier reports keep their exact bytes
            d["change_from"] = self.change_from
        return d

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "report", _REPORT_REQUIRED, _REPORT_OPTIONAL)
        return cls(
            id=opt(o.get("id"), lambda x: as_str(x, "report.id")),
            key=Key.from_dict(o["key"]),
            proposition=opt(o.get("proposition"), proposition_from_dict),
            cue=as_enum(Cue, o["cue"], "report.cue"),
            target=opt(o.get("target"), lambda x: as_str(x, "report.target")),
            source=Source.from_dict(o["source"]),
            origin=as_enum(Origin, o["origin"], "report.origin"),
            origin_group=as_str(o["origin_group"], "report.origin_group"),
            actor=as_str(o["actor"], "report.actor"),
            observed_at=opt(o.get("observed_at"), lambda x: ts_from_str(x, "report.observed_at")),
            valid_from=opt(o.get("valid_from"), lambda x: ts_from_str(x, "report.valid_from")),
            valid_to=opt(o.get("valid_to"), lambda x: ts_from_str(x, "report.valid_to")),
            precision=as_enum(Precision, o.get("precision", "day"), "report.precision"),
            raw_ref=opt(o.get("raw_ref"), lambda x: as_str(x, "report.raw_ref")),
            extractor=opt(o.get("extractor"), Extractor.from_dict),
            change_from=o.get("change_from"),
        )


@dataclass(frozen=True, kw_only=True)
class LogEntry(Codec):
    """A report as stored: lsn (the belief axis, S-05), recorded_at (log-set metadata) and the
    optional hash-chain columns (a storage-layer property; a backend without the chain is still
    contract-conformant). Hash computation is the backend's business, not this type's.
    """

    lsn: int
    recorded_at: datetime
    report: Report
    prev_hash: str | None = None
    entry_hash: str | None = None

    def __post_init__(self) -> None:
        check_nat(self.lsn, "log_entry.lsn", minimum=1)
        set_field(self, "recorded_at", req_ts(self.recorded_at, "log_entry.recorded_at"))
        if not isinstance(self.report, Report):
            raise ValidationError("log_entry.report: not a Report")
        if self.report.id is None:
            raise ValidationError("log_entry.report.id: a stored report must have its log-assigned id")
        if self.prev_hash is not None:
            check_hex64(self.prev_hash, "log_entry.prev_hash")
        if self.entry_hash is not None:
            check_hex64(self.entry_hash, "log_entry.entry_hash")

    def to_dict(self) -> dict[str, Any]:
        return {
            "lsn": self.lsn,
            "recorded_at": ts_to_str(self.recorded_at),
            "report": self.report.to_dict(),
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
        }

    @classmethod
    def from_dict(cls, d: Any) -> Self:
        o = as_obj(d, "log_entry", ["lsn", "recorded_at", "report"], ["prev_hash", "entry_hash"])
        return cls(
            lsn=as_int(o["lsn"], "log_entry.lsn"),
            recorded_at=ts_from_str(o["recorded_at"], "log_entry.recorded_at"),
            report=Report.from_dict(o["report"]),
            prev_hash=opt(o.get("prev_hash"), lambda x: as_str(x, "log_entry.prev_hash")),
            entry_hash=opt(o.get("entry_hash"), lambda x: as_str(x, "log_entry.entry_hash")),
        )
