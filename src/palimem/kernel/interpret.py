"""The per-key enumeration kernel (SEMANTICS §6-7): the production kernel through G1 and the permanent
audit oracle (author decision, 2026-10-04).

Admissibility conditions are local to a key, so a key's interpretations are built by walking every
subset T of its admitted reports (labelled TRUE; the rest ERR) and keeping those that satisfy A-ERR,
A-CORR, A-CHG, A-CONS (and, under P0cSU, A-SU), then constructing timelines with an anchor sweep. This
is a line-for-line port of ``revise_stream.oracle_v1._key_interps`` over :class:`Ev` evidence, so its
agreement with the study's oracle can be tested directly (``tests/test_kernel_oracle_property.py``).

The enumeration is 2^n in the number of admitted reports on the key; above the environment budget the
caller answers ``ResourceLimited(environment_budget)`` (S-06) and never silently degrades.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from itertools import product

from palimem.kernel.evidence import Ev
from palimem.kernel.spec import AttrSpec
from palimem.kernel.timeline import INF, Interp, Run, Timeline
from palimem.types._codec import Value

P0C = "P0c"
P0CSU = "P0cSU"
P0 = "P0"


def _shielded(o: Ev, p: Ev, policy: str) -> bool:
    """Cue resolution (P0c / P0cSU): a report carrying a ``change`` cue is not disputed by competitors
    anchored strictly earlier than itself (the cue identifies the revision operator)."""
    cues = {P0C: ("change",), P0CSU: ("change",)}.get(policy, ())
    return o.op_cue in cues and p.anchor < o.anchor


def _self_superseded(spec: AttrSpec, o: Ev, T: Sequence[Ev]) -> bool:
    """A-SU (P0cSU): on a single-valued changeable key a report ``o`` may not be labelled ERR when every
    accepted competitor of ``o`` is a strictly-earlier report from ``o``'s own origin group and no
    accepted correction targets ``o``."""
    if spec.cardinality != "single" or not spec.changeable:
        return False
    if any(p.op_cue == "correction" and p.op_of == o.id for p in T):
        return False
    comp = [p for p in T if p.value != o.value]
    if not comp:
        return False
    return all(p.origin_group == o.origin_group and p.anchor < o.anchor for p in comp)


def _enumerate(spec: AttrSpec, obs: Sequence[Ev], policy: str, relax: int) -> set[Interp]:
    n = len(obs)
    out: set[Interp] = set()
    for mask in range(1 << n):
        T = [obs[i] for i in range(n) if mask >> i & 1]
        R = [obs[i] for i in range(n) if not mask >> i & 1]
        tv = {o.value for o in T}
        tid = {o.id for o in T}
        ok = True
        # errors must be allowed and explained by an accepted competitor or correction
        for o in R:
            if not spec.error_allowed:
                ok = False
                break
            comp = [p for p in T if p.value != o.value]
            comp = [p for p in comp if not _shielded(o, p, policy)]
            if not ((comp and spec.competing_values) or any(p.op_cue == "correction" and p.op_of == o.id for p in T)):
                ok = False
                break
            if policy == P0CSU and _self_superseded(spec, o, T):
                ok = False
                break
        if not ok:
            continue
        # an accepted correction rejects its target
        if any(p.op_cue == "correction" and p.op_of in tid for p in T):
            continue
        # an accepted change-from presupposes the previous value
        for p in T:
            if relax == 0 and p.op_cue == "change" and p.op_from is not None:
                prev = [q for q in obs if q.value == p.op_from and q.anchor < p.anchor and q.id != p.id]
                if prev and not any(q.id in tid for q in prev):
                    ok = False
                    break
        if not ok:
            continue
        # consistency among accepted reports
        if spec.cardinality == "single":
            if not spec.changeable and len(tv) > 1:
                continue
            anchors: dict[int, set[Value]] = defaultdict(set)
            for o in T:
                anchors[o.anchor].add(o.value)
            if any(len(vs) > 1 for vs in anchors.values()):
                continue
        err = frozenset(o.id for o in R)
        for tl in _sweep(spec, T):
            out.add((err, tl))
    return out


def _sweep(spec: AttrSpec, T: Sequence[Ev]) -> list[Timeline]:
    """Anchor sweep: build maximal same-value runs, then segments per SEMANTICS §7."""
    if not T:
        return [()]
    events = sorted(((o.anchor, str(o.value), o.value, o) for o in T), key=lambda x: (x[0], x[1]))
    runs: list[tuple[Value, int, int]] = []  # (value, first, last)
    for a, _, v, _o in events:
        if runs and runs[-1][0] == v:
            runs[-1] = (v, runs[-1][1], a)
        else:
            runs.append((v, a, a))
    if spec.cardinality == "single":
        segs: list[Run] = []
        for i, (v, first, last) in enumerate(runs):
            if i + 1 < len(runs):
                segs.append(Run(v, runs[i - 1][2] if i else first, first, last, runs[i + 1][1]))
            else:
                segs.append(Run(v, runs[i - 1][2] if i else first, first, INF, INF))
        return [tuple(sorted(segs))]
    # multi-valued: each run followed by another run chooses CONT or ENDED, unless a change-from cue
    # forces ENDED on the run of the previous value
    forced: set[int] = set()
    for o in T:
        if o.op_cue == "change" and o.op_from is not None:
            for i, (v, _first, last) in enumerate(runs[:-1]):
                if v == o.op_from and last < o.anchor:
                    forced.add(i)
    slots = [i for i in range(len(runs) - 1) if i not in forced]
    if not spec.changeable:
        slots, forced = [], set()
    tls: list[Timeline] = []
    for choice in product(["CONT", "ENDED"], repeat=len(slots)):
        lab = dict(zip(slots, choice, strict=True))
        lab.update({i: "ENDED" for i in forced})
        msegs: list[Run] = []
        for i, (v, first, last) in enumerate(runs):
            if lab.get(i) == "ENDED":
                msegs.append(Run(v, first, first, last, runs[i + 1][1]))
            else:
                msegs.append(Run(v, first, first, INF, INF))
        tls.append(tuple(sorted(msegs)))
    return tls


def key_interpretations(spec: AttrSpec, obs: Sequence[Ev], policy: str) -> tuple[frozenset[Interp], int]:
    """The key's interpretation set and the totality-ladder level used (0 = none).

    The ladder (SEMANTICS §6, "relaxation") applies only when the admissible set is empty: (1) drop A-CHG
    presuppositions; (2) additionally fall back to literal P0 dispute; (3) reject every report on the key.
    """
    if not obs:
        return frozenset({(frozenset(), ())}), 0
    res = _enumerate(spec, obs, policy, 0)
    level = 0
    if not res:
        for lv, (pol, rl) in enumerate(((policy, 1), (P0, 1)), start=1):
            res = _enumerate(spec, obs, pol, rl)
            if res:
                level = lv
                break
        else:
            level = 3
            res = {(frozenset(o.id for o in obs), ())}  # everything rejected: the key is unknown
    return frozenset(res), level
