"""Host-supplied extraction context: the only place identity and authority enter."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from palimem.extract.claims import TargetHint
from palimem.types import Cue, Origin, Schema, ValidationError
from palimem.types.authority import check_principal
from palimem.types.report import Source

#: Authority-bearing cues are *not* accepted from extracted text by default (T-06): the host
#: opts in per connector, e.g. a registry connector registered as authoritative.
DEFAULT_ALLOWED_CUES = frozenset({Cue.ASSERT, Cue.CHANGE})


@dataclass(frozen=True, kw_only=True)
class ExtractionContext:
    source: Source  # from connector metadata, never from the text
    origin_group: str
    actor: str  # typed principal id; set by the host
    origin: Origin = Origin.EXTERNAL_OBSERVATION
    observed_at: datetime | None = None
    raw_ref: str | None = None
    subject_entity: str | None = None  # the speaker's entity name, for first person ("I", "my")
    schema: Schema | None = None
    allowed_cues: frozenset[Cue] = DEFAULT_ALLOWED_CUES
    valid_time_bounds: tuple[datetime | None, datetime | None] = (None, None)  # T-09 plausibility
    resolve_target: Callable[[TargetHint], str | None] | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.source, Source):
            raise ValidationError("context.source: not a Source")
        check_principal(self.actor, "context.actor")
        if not self.origin_group.strip():
            raise ValidationError("context.origin_group: expected a non-empty string")
        if not isinstance(self.origin, Origin):
            raise ValidationError("context.origin: not an Origin")
