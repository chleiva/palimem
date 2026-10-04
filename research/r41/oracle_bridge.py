"""Bridge between the research kernel and the deposited oracle (oracle_v1 in ~/palimpsest).

The study repo is located through $PALIMPSEST_STUDY_DIR (default ~/palimpsest) and is only
imported, never modified. `available()` is False when it is missing, so callers can skip.
"""
from __future__ import annotations

import os
import sys

from .kernel import Rep

STUDY_DIR = os.environ.get("PALIMPSEST_STUDY_DIR", os.path.expanduser("~/palimpsest"))
_loaded = False


def available() -> bool:
    return os.path.isdir(os.path.join(STUDY_DIR, "revise_stream"))


def _load():
    global _loaded
    if not _loaded:
        if STUDY_DIR not in sys.path:
            sys.path.insert(0, STUDY_DIR)
        _loaded = True


def build_stream(reps: list[Rep], cardinality: str = "single", changeable: bool = True,
                 error_allowed: bool = True, competing: bool = True):
    """A one-entity, one-attribute Stream whose admitted observations are exactly `reps`.
    Origins map to one source each (class 'standard'); anchors are given by 'since' cues; arrival
    order is the position in `reps`. Corrections must not share an origin with their target,
    otherwise A-SELF (an admission rule, outside the kernel) would withdraw the target."""
    _load()
    from revise_stream.model import stream_from_dict
    origins = sorted({r.origin for r in reps})
    d = {
        "stream_id": "r41",
        "domain": {"entities": ["e"], "attributes": {"a": {
            "type": "string", "cardinality": cardinality, "changeable": changeable,
            "error_allowed": error_allowed, "competing_values": competing}}, "rules": []},
        "sources": {f"s_{g}": {"class": "standard", "origin": g} for g in origins},
        "observations": [], "queries": [],
    }
    for i, r in enumerate(reps):
        o = {"id": f"o{r.id}", "t": i + 1, "source": f"s_{r.origin}", "kind": "assert",
             "entity": "e", "attr": "a", "value": r.value, "valid": {"cue": "since", "t": r.anchor}}
        if r.cue != "none":
            o["op_cue"] = r.cue
        if r.op_from is not None:
            o["op_from"] = r.op_from
        if r.op_of is not None:
            o["op_of"] = f"o{r.op_of}"
        d["observations"].append(o)
    return stream_from_dict(d)


KEY = ("e", "a", "")


def oracle_interps(reps: list[Rep], policy: str, **spec):
    """Per-key interpretations from oracle_v1.key_interpretations (with its totality ladder)."""
    _load()
    s = build_stream(reps, **spec)
    tau = len(reps) + 1
    adm = s.admitted_by_key(tau).get(KEY, [])
    assert sorted(o.id for o in adm) == sorted(f"o{r.id}" for r in reps), "admission changed the instance (A-SELF?)"
    return s, oracle_interps_for(s, tau, policy)


def oracle_interps_for(s, tau: int, policy: str):
    from revise_stream import oracle_v1
    return oracle_v1.key_interpretations(s, tau, KEY, policy)


def oracle_v1_interps(s, tau: int, key, policy: str):
    _load()
    from revise_stream import oracle_v1
    return oracle_v1.key_interpretations(s, tau, key, policy)


def oracle_cand(s, interps, t: int, attr: str = "a") -> set[frozenset]:
    from revise_stream.timeline import observed_candidates
    spec = s.attributes[attr]
    out: set[frozenset] = set()
    for it in interps:
        out |= observed_candidates(s, it[1], spec, t)
    return out


def oracle_erroneous(interps, rid: int) -> tuple[bool, bool]:
    key = f"o{rid}"
    truths = [key in it[0] for it in interps]          # True = ERR
    return (not all(truths) if truths else False), any(truths)


def oracle_changed(interps, v1, v2) -> tuple[bool, bool]:
    from revise_stream.gold import _changed
    truths = [_changed(it[1], v1, v2) for it in interps]
    return any(truths), (not all(truths)) if truths else False
