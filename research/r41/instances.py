"""Instance generators for the R4.1 spike: random adversarial keys and an 'agent re-assertion' log."""
from __future__ import annotations

import random

from .kernel import Rep


def random_instance(rng: random.Random, n: int, n_values: int = 3, n_origins: int = 2, anchor_span: int = 12,
                    p_change: float = 0.3, p_from: float = 0.6, p_corr: float = 0.15, p_correction_cue_overlap: float = 0.0) -> list[Rep]:
    """Adversarial: small anchor span forces same-anchor ties, repeated values and cross-origin corrections."""
    vals = [f"v{i}" for i in range(n_values)]
    origins = [f"g{i}" for i in range(n_origins)]
    reps: list[Rep] = []
    for i in range(n):
        v = rng.choice(vals)
        g = rng.choice(origins)
        a = rng.randint(0, anchor_span)
        cue, frm, of = "none", None, None
        x = rng.random()
        if x < p_corr and reps:
            tgt = rng.choice(reps)
            cands = [t for t in reps if t.origin != g]          # keep the instance free of A-SELF
            if cands:
                tgt = rng.choice(cands)
                cue, of = "correction", tgt.id
        elif x < p_corr + p_change:
            cue = "change"
            if rng.random() < p_from:
                frm = rng.choice([w for w in vals if w != v] or vals)
        reps.append(Rep(i, v, a, g, cue, frm, of))
    return reps


def agent_log(rng: random.Random, sessions: int, n_changes: int = 2, p_report: float = 0.8, p_error: float = 0.05,
              origins=("user", "crm"), p_dup_origin_burst: float = 0.35) -> list[Rep]:
    """A 'repeated assertion' log: the same key is restated in many sessions, from one or two origins,
    occasionally mis-stated, with a change cue on the first report of a new value."""
    vals = [f"v{i}" for i in range(n_changes + 3)]
    change_at = sorted(rng.sample(range(2, max(3, sessions)), min(n_changes, max(0, sessions - 2))))
    cur_i = 0
    reps: list[Rep] = []
    last_value: str | None = None
    for s in range(sessions):
        if s in change_at:
            cur_i += 1
        truth = vals[cur_i]
        for g in origins:
            if rng.random() > p_report * (1.0 if g == origins[0] else 0.5):
                continue
            burst = 2 if rng.random() < p_dup_origin_burst else 1
            for _ in range(burst):
                v = truth if rng.random() > p_error else rng.choice([w for w in vals if w != truth])
                cue, frm = "none", None
                if last_value is not None and v != last_value and v == truth and rng.random() < 0.5:
                    cue, frm = "change", last_value
                reps.append(Rep(len(reps), v, s, g, cue, frm))
                last_value = v
    return reps
