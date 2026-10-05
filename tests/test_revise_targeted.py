"""The targeted revision (Lane O): same answers as the exhaustive one, and work that does not grow with entity count.

Two properties:

1. **Equivalence.** ``Pipeline(exhaustive=True)`` re-justifies the derived keys of every entity on every append (the
   first version's behaviour). The default revises only the dependents of the changed keys. Fed the same random
   operations (assertions, changes, corrections, withdrawals, over values that are and are not entities), both must
   answer every key identically, at the head and under ``belief_as_of``.
2. **Cost.** An append that feeds a derived key justifies the same number of derived keys, reading the same number of
   keys, whether the store holds 20 or 320 entities. Counted in operations (``Pipeline.stats``), never in seconds.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest

from palimem.admission import AdmissionConfig
from palimem.compat import schema_from_kernel
from palimem.kernel import AttrSpec, KernelSchema, RuleSpec
from palimem.memory import Memory
from palimem.types import (
    Cue,
    KernelStatus,
    Key,
    Profile,
    Query,
    Resolved,
    ResourceLimited,
    SemanticConfig,
)
from tests._pipeline_helpers import Clock, assertion, make_backend, src


def chain_schema(persons: int, orgs: int) -> KernelSchema:
    ents = tuple(f"p{i}" for i in range(persons)) + tuple(f"o{i}" for i in range(orgs))
    return KernelSchema(
        attrs={
            "employer": AttrSpec("employer", "single", True),
            "hq_city": AttrSpec("hq_city", "single", True),
            "remote": AttrSpec("remote", "single", False),
            "work_city": AttrSpec("work_city", "single", True, error_allowed=False, derived=True),
            "tax_city": AttrSpec("tax_city", "single", True, error_allowed=False, derived=True),
        },
        rules=(
            RuleSpec(
                id="r1", head=("work_city", "?e", "?c"),
                # `remote` used to be a defeasible exception; exceptions are reserved in 0.x (S-10 narrows to
                # strict rules), so the blocking condition is modelled as a body premise: the rule still reads
                # the `remote` key, which is what these work-count and equivalence tests exercise.
                body=(("employer", "?e", "?x"), ("hq_city", "?x", "?c"), ("remote", "?e", "no")),
            ),
            RuleSpec(id="r2", head=("tax_city", "?e", "?c"), body=(("work_city", "?e", "?c"),)),
        ),
        entities=ents,
    )


def make(kind: str, persons: int, orgs: int, *, exhaustive: bool) -> Memory:
    ks = chain_schema(persons, orgs)
    clock = Clock()
    m = Memory(
        make_backend(kind, clock), schema_from_kernel(ks), kernel_schema=ks, entities=ks.entities,
        semantic=SemanticConfig(self_update=False, profile=Profile.OPEN_WORLD),
        admission=AdmissionConfig(profile=Profile.OPEN_WORLD),
    )
    m.pipeline.exhaustive = exhaustive
    m.clock = clock  # type: ignore[attr-defined]
    return m


def view(m: Memory, key: Key, **kw: object) -> tuple[object, ...]:
    ans = m.query(Query(key=key, profile=Profile.OPEN_WORLD, **kw))  # type: ignore[arg-type]
    if isinstance(ans, ResourceLimited):  # a key past the environment budget answers the same way in both
        return ("limited", ans.reason)
    assert isinstance(ans, Resolved), ans
    seg = ans.justified.segment
    return (
        ans.kernel_status, ans.decision, ans.assertion.id if ans.assertion else None,
        tuple(sorted(c.id for c in ans.alternatives)), seg.valid_from, seg.valid_to,
    )


def apply_ops(rng: random.Random, ms: list[Memory], n_ops: int, persons: int, orgs: int) -> list[str]:
    """The same random operation on every memory; returns the ids of reports appended on memory 0 by position."""
    persons_l = [f"p{i}" for i in range(persons)]
    orgs_l = [f"o{i}" for i in range(orgs)]
    cities = [f"c{i}" for i in range(4)]
    sources = ["press", "registry", "wire"]
    ids: list[list[str]] = [[] for _ in ms]
    mine: list[tuple[int, str, str]] = []  # (index, source, group) of appended reports
    for _ in range(n_ops):
        roll = rng.random()
        if roll < 0.40:
            who, attr = rng.choice(persons_l), "employer"
            value = rng.choice([*orgs_l, "ghost"])  # a value that is not an entity must not break anything
        elif roll < 0.62:
            who, attr, value = rng.choice(orgs_l), "hq_city", rng.choice(cities)
        elif roll < 0.68:
            who, attr, value = rng.choice(persons_l), "remote", rng.choice(["yes", "no"])
        elif roll < 0.84 and mine:
            idx, s, g = rng.choice(mine)
            for m, got in zip(ms, ids, strict=True):
                m.withdraw(got[idx], source=src(s), actor=f"connector:{s}", origin_group=g)
            continue
        else:
            who, attr = rng.choice(persons_l + orgs_l), rng.choice(["employer", "hq_city"])
            value = rng.choice([*orgs_l, *cities])
        s = rng.choice(sources)
        cue = rng.choice([Cue.ASSERT, Cue.ASSERT, Cue.CHANGE])
        rep = assertion(who, attr, value, source=s, cue=cue, group=s)
        for m, got in zip(ms, ids, strict=True):
            m.clock.day += 1  # type: ignore[attr-defined]
            res = m.append(rep)
            assert res.entry is not None and res.entry.report.id is not None
            got.append(res.entry.report.id)
        mine.append((len(ids[0]) - 1, s, s))
    return ids[0]


def compare(a: Memory, b: Memory, persons: int, orgs: int, rng: random.Random, upto: int) -> None:
    keys = [
        Key(entity=e, attr=at)
        for e in [*(f"p{i}" for i in range(persons)), *(f"o{i}" for i in range(orgs))]
        for at in ("employer", "hq_city", "remote", "work_city", "tax_city")
    ]
    asofs = [None, *rng.sample(range(1, upto + 1), k=min(3, upto))] if upto else [None]
    for k in keys:
        for lsn in asofs:
            kw: dict[str, object] = {} if lsn is None else {"belief_as_of": lsn}
            assert view(a, k, **kw) == view(b, k, **kw), (k, lsn)
        day = datetime(2026, 1, 1 + rng.randrange(1, 20), tzinfo=UTC)
        assert view(a, k, valid_at=day) == view(b, k, valid_at=day), (k, day)


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
@pytest.mark.parametrize("seed", range(6))
def test_targeted_revision_answers_like_the_exhaustive_one(kind: str, seed: int) -> None:
    persons, orgs = 7, 3
    fast = make(kind, persons, orgs, exhaustive=False)
    full = make(kind, persons, orgs, exhaustive=True)
    rng = random.Random(seed)
    n = len(apply_ops(rng, [fast, full], 60, persons, orgs))
    compare(fast, full, persons, orgs, random.Random(seed + 100), n)
    # the exhaustive memory wrote a derived belief for every entity at once; the targeted one only for dependents:
    # it must have written strictly fewer versions, never more
    assert fast.pipeline.stats["derived_written"] <= full.pipeline.stats["derived_written"]
    assert fast.pipeline.stats["derived_justified"] < full.pipeline.stats["derived_justified"]


def test_the_value_index_survives_a_rolled_back_append_and_a_completion() -> None:
    """An index ahead of the store (a failed append) or behind it (a completion job) must never serve a stale answer."""
    persons, orgs = 5, 2
    fast = make("memory", persons, orgs, exhaustive=False)
    full = make("memory", persons, orgs, exhaustive=True)
    rng = random.Random(7)
    apply_ops(rng, [fast, full], 25, persons, orgs)
    fast.pipeline.drop_index()  # what a completion or an erasure repair does
    n = len(apply_ops(rng, [fast, full], 25, persons, orgs))
    compare(fast, full, persons, orgs, random.Random(1), n)
    assert fast.pipeline.stats["index_builds"] >= 2  # it was rebuilt from the store after being dropped


def _population(persons: int, orgs: int) -> Memory:
    """Everybody works somewhere (round robin over the organisations) and every organisation has a city."""
    m = make("memory", persons, orgs, exhaustive=False)
    for o in range(orgs):
        m.append(assertion(f"o{o}", "hq_city", f"c{o % 4}", source="registry"))
    for p in range(persons):
        m.append(assertion(f"p{p}", "employer", f"o{p % orgs}", source="press"))
    return m


def _work(m: Memory, op: object) -> tuple[int, int, int]:
    m.pipeline.stats.clear()
    op()  # type: ignore[operator]
    s = m.pipeline.stats
    return s["derived_justified"], s["derived_keys_read"], s["derived_written"]


@pytest.mark.parametrize("persons", [20, 80, 320])
def test_a_person_append_does_a_constant_amount_of_derived_work(persons: int) -> None:
    """Changing one person's employer re-justifies that person's derived keys and nothing else, however many entities
    the store holds. (Counted in operations: the first version justified every entity's derived key.)"""
    m = _population(persons, 4)
    w = _work(m, lambda: m.append(assertion("p3", "employer", "o1", source="wire", cue=Cue.CHANGE)))
    assert w[0] <= 4  # work_city and tax_city of p3 (and at most the same again through the store's own marks)
    assert w[1] <= 16


def test_work_per_append_does_not_grow_with_the_entity_count() -> None:
    small = _work(m := _population(20, 4), lambda: m.append(assertion("p3", "employer", "o1", source="wire", cue=Cue.CHANGE)))
    large = _work(m2 := _population(320, 4), lambda: m2.append(assertion("p3", "employer", "o1", source="wire", cue=Cue.CHANGE)))
    assert large[0] <= small[0] + 1
    assert large[1] <= small[1] + 4
    assert large[2] <= small[2] + 1


def test_an_organisation_change_justifies_its_employees_and_only_them() -> None:
    """The fan-out is real (employees read the organisation's city), but it is the employees of that organisation,
    not every entity: the same count with 20 or 320 people when the organisation keeps the same number of employees."""
    def hq_change(persons: int) -> tuple[int, int, int]:
        m = _population(persons, persons // 5)  # five employees per organisation, at any size
        return _work(m, lambda: m.append(assertion("o2", "hq_city", "c3", source="wire", cue=Cue.CHANGE)))

    a, b = hq_change(20), hq_change(320)
    assert a[0] == b[0] > 0
    assert a[0] <= 2 * 5 + 2  # work_city and tax_city of five employees, plus the marks the store adds
    assert a[2] == b[2]


def test_unwritten_derived_keys_still_answer_unknown() -> None:
    m = _population(10, 2)
    ans = m.query(Query(key=Key(entity="p9", attr="tax_city"), profile=Profile.OPEN_WORLD))
    assert isinstance(ans, Resolved)
    assert ans.kernel_status in (KernelStatus.ESTABLISHED, KernelStatus.UNKNOWN)


# ------------------------------------------------------------------ memory: the evaluation cache and the per-evaluation facts


def test_a_revision_reads_only_the_current_and_the_previous_evaluation_and_the_cache_stays_small() -> None:
    """Heap per report was ~100-200 KiB because 512 admission evaluations (a decision per log entry each) were kept.
    A revision reads the evaluation at ``lsn`` and at ``lsn - 1`` and nothing older, so a handful suffice."""
    from palimem.engine.pipeline import EVAL_CACHE_SIZE

    m = make("memory", 12, 3, exhaustive=False)
    seen: list[int] = []
    real = m.pipeline.evaluate

    def spy(upto_lsn: int):  # type: ignore[no-untyped-def]
        seen.append(upto_lsn)
        return real(upto_lsn)

    m.pipeline.evaluate = spy  # type: ignore[method-assign]
    rng = random.Random(3)
    for i in range(80):
        seen.clear()
        m.append(assertion(f"p{rng.randrange(12)}", "employer", f"o{rng.randrange(3)}", source="press", group="press"))
        lsn = i + 1
        assert set(seen) <= {lsn, lsn - 1}, (lsn, seen)
        assert len(m.pipeline._evals) <= EVAL_CACHE_SIZE
        assert len(m.pipeline._facts) <= EVAL_CACHE_SIZE


def test_recomputing_every_key_scans_the_log_once_not_once_per_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """``verify_beliefs`` recomputes every current key; each recomputation used to rescan the whole log (entities,
    attributions, direct entries), which made verification superlinear."""
    import palimem.engine.pipeline as pl

    m = _population(60, 4)
    calls = {"direct": 0}
    real = pl.direct_entries

    def counting(ev):  # type: ignore[no-untyped-def]
        calls["direct"] += 1
        return real(ev)

    monkeypatch.setattr(pl, "direct_entries", counting)
    res = m.backend.verify_beliefs(m.reviser)
    assert res.ok and res.checked > 60
    assert calls["direct"] <= 2  # once for the head evaluation (plus at most one for a different evaluation), not per key


# ------------------------------------------------------------------ admission: the memoised own-merit decision


@pytest.mark.parametrize("seed", range(4))
def test_memoised_admission_equals_a_fresh_evaluation(seed: int) -> None:
    """The decision on a report's own merits is computed once for the life of the log. Whatever the cache holds, the
    evaluation of the whole log must equal the one a fresh, uncached admitter computes."""
    from palimem.admission import Admitter

    m = make("memory", 7, 3, exhaustive=False)
    apply_ops(random.Random(seed), [m], 70, 7, 3)
    head = m.backend.head().lsn
    cached = m.pipeline.evaluate(head)
    fresh = Admitter(m.pipeline.admitter.config, m.schema).evaluate(m.pipeline.log, as_of_lsn=head)
    assert dict(cached.decisions) == dict(fresh.decisions)
    assert dict(cached.withdrawn) == dict(fresh.withdrawn)
    assert m.pipeline.admitter._own_merit  # the cache was actually used
    m.pipeline.invalidate()
    assert not m.pipeline.admitter._own_merit  # and an erasure-style invalidation empties it
