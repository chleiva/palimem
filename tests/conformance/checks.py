"""Checks that are not scenarios: they read the frozen study data directly (``kind: harness_check``).

A check returns ``(True, msg)`` on success, ``(False, msg)`` on failure and ``(None, reason)`` when
it cannot run (for example the frozen set or the study checkout is absent).
"""

from __future__ import annotations

import glob
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]

UNAVAILABLE_PREFIX = "inputs unavailable: "
"""Every ``(None, reason)`` a check returns because the frozen data or the study checkout is absent starts with this.
The ratchet (``impl_memory.current_status``) classifies such a skip as ``needs_data``, a state distinct from a
fixture that was skipped for any other reason, so the recorded baseline is the same with and without the data."""


def _frozen_dir() -> Path | None:
    cache = REPO / ".cache" / "frozen" / "setting1"
    if cache.is_dir() and any(cache.glob("s1_[0-9][0-9][0-9][0-9].json")):
        return cache
    try:
        sys.path.insert(0, str(REPO))
        from harness import frozen

        try:
            return Path(frozen.locate_frozen())
        except frozen.FrozenError:  # no frozen data on this machine (CI's plain test job): the check cannot run
            return None
    except (ImportError, OSError, FileNotFoundError):
        return None


def compat_authority_coincide() -> tuple[bool | None, str]:
    """No correction on the frozen Setting 1 streams is made by a different source that shares its
    target's origin, so origin-based and source-based authority give identical A-SELF results (S-02)."""
    d = _frozen_dir()
    if d is None:
        return None, UNAVAILABLE_PREFIX + "frozen Setting 1 data not available (python -m harness.frozen verify --fetch)"
    try:
        from harness import study

        ns = study.load()
    except FileNotFoundError as e:
        return None, UNAVAILABLE_PREFIX + str(e)
    files = sorted(glob.glob(str(d / "s1_[0-9][0-9][0-9][0-9].json")))
    if not files:
        return None, UNAVAILABLE_PREFIX + f"no s1_NNNN.json under {d}"
    corrections = crossings = streams_with_shared_origin = 0
    first: str | None = None
    for f in files:
        s = ns.load_stream(f)
        by_origin: dict[Any, set[str]] = {}
        for sid, src in s.sources.items():
            by_origin.setdefault(src.origin, set()).add(sid)
        streams_with_shared_origin += any(len(v) > 1 for v in by_origin.values())
        obs = {o.id: o for o in s.observations}
        for o in s.observations:
            if o.kind == "assert" and o.op_cue == "correction" and o.op_of in obs:
                corrections += 1
                t = obs[o.op_of]
                if t.source != o.source and s.sources[t.source].origin == s.sources[o.source].origin:
                    crossings += 1
                    first = first or f"{Path(f).name}: {o.id} (source {o.source}) corrects {t.id} (source {t.source})"
    if crossings:
        return False, f"{crossings} of {corrections} corrections cross sources within an origin, e.g. {first}"
    return True, f"{len(files)} streams, {streams_with_shared_origin} with sources sharing an origin, {corrections} corrections, 0 crossings"


REGISTRY: dict[str, Callable[[], tuple[bool | None, str]]] = {
    "compat-01-authority-coincide": compat_authority_coincide,
}
