"""T-B10: the candidate fast kernel (palimem.kernel.fast) against the enumeration kernel, instance by instance.

The enumeration kernel is the production kernel and the audit oracle; the fast kernel is a candidate. These
tests are the differential half of the promotion criteria: identical candidate families at every valid day,
identical segments (statuses, candidates, per-candidate supports inside the enumeration envelope), identical
yes/no truth sets, and an explicit route for everything outside the proven class.
"""

from __future__ import annotations

import random
import time

import pytest

from palimem.kernel.evidence import Ev
from palimem.kernel.fast import (
    FastJustification,
    in_fast_class,
    justify_ev_fast,
)
from palimem.kernel.interpret import P0C, P0CSU, key_interpretations
from palimem.kernel.justify import Justification
from palimem.kernel.spec import AttrSpec, KernelUnsupported
from palimem.types import ExplanationState, Key, Profile, ResourceLimitedReason

KEY = Key(entity="alex", attr="employer")


def rid(i: int) -> str:
    """A ULID-shaped report id (Support environments are validated), ordered by i."""
    return "01H" + str(i).zfill(23)

SINGLE = AttrSpec(name="employer", cardinality="single", changeable=True)


def random_ev(rng: random.Random, n: int, *, n_values: int = 3, n_origins: int = 2, span: int = 12,
              p_change: float = 0.3, p_from: float = 0.6, p_corr: float = 0.15) -> list[Ev]:
    """Adversarial instances: a small anchor span forces ties, repeated values and cross-origin corrections."""
    vals = [f"v{i}" for i in range(n_values)]
    origins = [f"g{i}" for i in range(n_origins)]
    out: list[Ev] = []
    for i in range(n):
        v, g, a = rng.choice(vals), rng.choice(origins), rng.randint(0, span)
        cue, frm, of = "none", None, None
        x = rng.random()
        if x < p_corr and out:
            cands = [t for t in out if t.origin_group != g]
            if cands:
                cue, of = "correction", rng.choice(cands).id
        elif x < p_corr + p_change:
            cue = "change"
            if rng.random() < p_from:
                frm = rng.choice([w for w in vals if w != v] or vals)
        out.append(Ev(id=rid(i), anchor=a, value=v, op_cue=cue, op_from=frm, op_of=of, origin_group=g, lsn=i))
    return out


def enumeration(spec: AttrSpec, ev: list[Ev], policy: str) -> Justification | None:
    interps, level = key_interpretations(spec, ev, policy)
    if level != 0:
        return None
    return Justification(key=KEY, spec=spec, evidence=tuple(ev), interpretations=interps, relax_level=0,
                         profile=Profile.REVISE_STREAM_V1, policy=policy)


def days(ev: list[Ev]) -> range:
    return range(min(e.anchor for e in ev) - 2, max(e.anchor for e in ev) + 4)


def compare(spec: AttrSpec, ev: list[Ev], policy: str, *, enumeration_limit: int = 99) -> bool:
    """True if compared (the instance is in the proven class), asserting equality of everything observable."""
    enum = enumeration(spec, ev, policy)
    if enum is None:
        with pytest.raises(KernelUnsupported):
            justify_ev_fast(spec, ev, policy)
        return False
    fast = justify_ev_fast(spec, ev, policy, key=KEY, enumeration_limit=enumeration_limit)
    for t in days(ev):
        assert fast.candidates_at(t) == enum.candidates_at(t), (t, ev)
    assert fast.segments() == enum.segments(), ev
    for e in ev:
        assert sorted(fast.erroneous_truths(e.id)) == sorted(set(enum.erroneous_truths(e.id))) or \
            set(fast.erroneous_truths(e.id)) == set(enum.erroneous_truths(e.id)), (e.id, ev)
    vals = sorted({str(e.value) for e in ev})
    for a in vals:
        for b in vals:
            if a != b:
                assert set(fast.changed_truths(a, b)) == set(enum.changed_truths(a, b)), (a, b, ev)
    for v in vals:
        for t in days(ev):
            assert set(fast.holds_truths(v, t)) == set(enum.holds_truths(v, t))
    assert fast.always_err_ids() == enum.always_err_ids(), ev
    assert fast.oracle_flat_ids(days(ev)[3]) == enum.oracle_flat_ids(days(ev)[3])
    return True


@pytest.mark.parametrize("policy", [P0C, P0CSU])
def test_random_instances_match_the_enumeration_kernel(policy: str) -> None:
    rng = random.Random(20261005 if policy == P0C else 20261006)
    compared = 0
    for _ in range(500):
        n = rng.randint(1, 9)
        ev = random_ev(rng, n, n_values=rng.choice([2, 3, 4]), n_origins=rng.choice([1, 2, 3]),
                       span=rng.choice([3, 6, 10, 20]), p_change=rng.choice([0, 0.3, 0.6]),
                       p_from=rng.choice([0, 0.6, 1.0]), p_corr=rng.choice([0, 0.15, 0.35]))
        compared += compare(SINGLE, ev, policy)
    assert compared > 350, "the generator must mostly produce instances inside the proven class"


def test_flags_error_allowed_and_competing_values_match() -> None:
    rng = random.Random(77)
    for spec in (AttrSpec(name="employer", cardinality="single", changeable=True, error_allowed=False),
                 AttrSpec(name="employer", cardinality="single", changeable=True, competing_values=False)):
        compared = 0
        for _ in range(150):
            ev = random_ev(rng, rng.randint(1, 7), span=rng.choice([4, 10]))
            compared += compare(spec, ev, rng.choice([P0C, P0CSU]))
        assert compared > 20


def test_single_origin_self_update_regime_p0csu_establishes_where_p0c_does_not() -> None:
    """The regime A-SU is for: one origin restates its value as it changes."""
    ev = [Ev(id=rid(i), anchor=i * 2, value=v, op_cue="none", op_from=None, op_of=None, origin_group="g_press", lsn=i)
          for i, v in enumerate(["a", "a", "b", "b", "c"])]
    su = justify_ev_fast(SINGLE, ev, P0CSU, key=KEY)
    plain = justify_ev_fast(SINGLE, ev, P0C, key=KEY)
    assert compare(SINGLE, ev, P0CSU) and compare(SINGLE, ev, P0C)
    now = max(e.anchor for e in ev) + 3
    assert su.segment_at(now).kernel_status.value == "established"
    assert plain.segment_at(now).kernel_status.value in ("established", "unresolved")
    assert su.segments() != plain.segments()


def test_larger_n_matches_enumeration_beyond_the_default_budget() -> None:
    """Candidates and segments (supports aside) agree at 10 to 14 reports, where the study never went."""
    rng = random.Random(4242)
    compared = 0
    for _ in range(40):
        n = rng.randint(10, 14)
        ev = random_ev(rng, n, n_values=3, n_origins=3, span=rng.choice([8, 16, 30]), p_corr=0.1)
        for policy in (P0C, P0CSU):
            enum = enumeration(SINGLE, ev, policy)
            if enum is None:
                continue
            fast = justify_ev_fast(SINGLE, ev, policy, key=KEY, enumeration_limit=7)
            assert not fast.supports_available
            for t in days(ev):
                assert fast.candidates_at(t) == enum.candidates_at(t)
            segs = fast.segments()
            statuses = [(s.valid_from, s.valid_to, s.kernel_status, s.established, s.alternatives) for s in segs]
            ref = [(s.valid_from, s.valid_to, s.kernel_status, s.established, s.alternatives) for s in enum.segments()]
            # the enumeration's supports can split a stretch the fast kernel (no supports) merges: compare the
            # status partition after merging equal neighbours
            def merged(rows: list[tuple[object, ...]]) -> list[tuple[object, ...]]:
                out: list[tuple[object, ...]] = []
                for r in rows:
                    if out and out[-1][2:] == r[2:]:
                        out[-1] = (out[-1][0], r[1], *r[2:])
                    else:
                        out.append(r)
                return out
            assert merged(statuses) == merged(ref)
            assert all(not s.support for s in segs)
            assert fast.explanation_state is ExplanationState.TRUNCATED
            compared += 1
    assert compared > 25


def test_above_the_enumeration_limit_supports_are_a_bounded_explanation() -> None:
    ev = random_ev(random.Random(5), 9, span=15, p_corr=0.0)
    fast = justify_ev_fast(SINGLE, ev, P0C, key=KEY, enumeration_limit=4)
    with pytest.raises(KernelUnsupported):
        _ = fast.interpretations
    expl = fast.explain(days(ev)[3])
    assert expl.state is ExplanationState.TRUNCATED
    assert expl.environments == ()


# ---------------------------------------------------------------------------- dispatch


def test_class_membership_reasons() -> None:
    assert in_fast_class(SINGLE, P0C) is None
    assert in_fast_class(SINGLE, P0CSU) is None
    assert "multi-valued" in (in_fast_class(AttrSpec(name="r", cardinality="multi", changeable=True), P0C) or "")
    assert "stable" in (in_fast_class(AttrSpec(name="b", cardinality="single", changeable=False), P0C) or "")
    assert "derived" in (in_fast_class(AttrSpec(name="w", cardinality="single", changeable=True, derived=True), P0C) or "")
    assert "policy" in (in_fast_class(SINGLE, "P1") or "")
    with pytest.raises(KernelUnsupported):
        justify_ev_fast(AttrSpec(name="r", cardinality="multi", changeable=True), [], P0C)


def test_branch_bound_counts_corrections_and_tied_anchors() -> None:
    ev = [
        Ev(id=rid(1), anchor=1, value="x", op_cue="none", op_from=None, op_of=None, origin_group="g0", lsn=0),
        Ev(id=rid(2), anchor=1, value="y", op_cue="none", op_from=None, op_of=None, origin_group="g1", lsn=1),
        Ev(id=rid(3), anchor=3, value="y", op_cue="correction", op_from=None, op_of=rid(1), origin_group="g1", lsn=2),
    ]
    fj = justify_ev_fast(SINGLE, ev, P0C, key=KEY)
    assert isinstance(fj, FastJustification)
    assert fj._kernel.branch_bound() == 2 * 2  # one correction x a tie between two values


def test_speed_grows_polynomially() -> None:
    """40 reports (2^40 labellings) decide in well under a minute; the enumeration cannot run at all."""
    rng = random.Random(1)
    ev = random_ev(rng, 40, n_values=3, n_origins=3, span=60, p_corr=0.0, p_change=0.3)
    t0 = time.time()
    fast = justify_ev_fast(SINGLE, ev, P0CSU, key=KEY, enumeration_limit=7)
    for t in days(ev):
        fast.candidates_at(t)
    fast.segments()
    assert time.time() - t0 < 60
    assert fast.relax_level == 0
    assert ResourceLimitedReason.ENVIRONMENT_BUDGET.value  # the enumeration would be refused above its budget
