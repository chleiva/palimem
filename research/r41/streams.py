"""Helpers to run the fast kernel on keys of generated REVISE-STREAM streams."""
from __future__ import annotations

from . import oracle_bridge as ob
from .diff import fast_answers
from .kernel import Rep


def reps_from_stream(s, tau: int, key) -> list[Rep]:
    """Admitted observations of one key at belief time tau, as kernel reports."""
    obs = s.admitted_by_key(tau).get(key, [])
    ids = {o.id: i for i, o in enumerate(obs)}
    return [Rep(i, o.value, o.anchor, s.sources[o.source].origin, o.op_cue, o.op_from, ids.get(o.op_of))
            for i, o in enumerate(obs)]


def compare_stream_key(s, tau: int, key, policy: str) -> list[str]:
    """Fast kernel vs oracle_v1.key_interpretations on a generated stream's key (single, changeable)."""
    from revise_stream.timeline import observed_candidates
    spec = s.attributes[key[1]]
    reps = reps_from_stream(s, tau, key)
    if not reps:
        return []
    interps = ob.oracle_v1_interps(s, tau, key, policy)
    ts = list(range(min(r.anchor for r in reps) - 1, max(r.anchor for r in reps) + 3))
    f = fast_answers(reps, policy, ts, {"error_allowed": spec.error_allowed, "competing": spec.competing_values})
    bad = []
    adm = s.admitted_by_key(tau)[key]
    for t in ts:
        o = set()
        for it in interps:
            o |= observed_candidates(s, it[1], spec, t)
        if f["cand"][t] != o:
            bad.append(f"cand t={t}")
    for r in reps:
        truths = [adm[r.id].id in it[0] for it in interps]
        want = ((not all(truths)) if truths else False, any(truths))
        if f["err"].get(r.id) != want:
            bad.append(f"err {r.id}")
    return bad
