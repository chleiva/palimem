"""Bounded views of a stored belief: the segment an answer or an event embeds, and opaque handles.

``BeliefView`` is what an Answer and a notification carry: one segment, never the whole record (design v0.3,
``BeliefView``). The full record is retrieved by ``ref`` (``Engine.get_belief_by_ref``).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from urllib.parse import quote, unquote

from palimem.types import Belief, BeliefView, Key, Segment


def select_segment(segments: Sequence[Segment], valid_at: datetime | None) -> Segment | None:
    """The segment containing ``valid_at`` (``valid_from <= t < valid_to``, ``None`` bounds are open).

    With ``valid_at=None`` the most recent segment is returned: callers that care about a particular
    moment pass it. ``None`` when no segment contains the moment."""
    if not segments:
        return None
    if valid_at is None:
        return segments[-1]
    for seg in segments:
        lo_ok = seg.valid_from is None or seg.valid_from <= valid_at
        hi_ok = seg.valid_to is None or valid_at < seg.valid_to
        if lo_ok and hi_ok:
            return seg
    return None


def belief_ref(key: Key, version: int) -> str:
    """Opaque handle for ``get_belief(key, version)``."""
    return f"belief:{version}:{quote(key.entity, safe='')}:{quote(key.attr, safe='')}"


def parse_belief_ref(ref: str) -> tuple[Key, int]:
    parts = ref.split(":")
    if len(parts) != 4 or parts[0] != "belief" or not parts[1].isdigit():
        raise ValueError(f"not a belief ref: {ref!r}")
    return Key(entity=unquote(parts[2]), attr=unquote(parts[3])), int(parts[1])


def belief_view(belief: Belief, valid_at: datetime | None = None) -> BeliefView | None:
    """A ``BeliefView`` of ``belief`` for the segment at ``valid_at`` (``None`` if no segment contains it)."""
    seg = select_segment(belief.segments, valid_at)
    if seg is None:
        return None
    return BeliefView(
        key=belief.key,
        version=belief.version,
        required_generation=belief.required_generation,
        completed_generation=belief.completed_generation,
        inference=belief.inference,
        segment=seg,
        ref=belief_ref(belief.key, belief.version),
    )
