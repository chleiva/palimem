"""Seeded, deterministic workload generators for the performance suite (T-E5, docs/PERFORMANCE.md §4).

A workload is a stream of operations over the toy agent schema (people, organisations, ``employer`` / ``residence`` /
``hq_city`` / ``affiliations`` plus the derived ``work_city``):

* :class:`AppendOp`: one report (an assertion, a change, a correction of the source's own earlier report, or a withdrawal
  of it). Withdrawals and corrections refer to an earlier append by its **index**; the runner maps indexes to the report ids
  the log assigns.
* :class:`QueryOp`: one ``Memory.query`` (current, or ``belief_as_of`` an earlier LSN; direct or derived key).

Workloads (the names of PERFORMANCE.md §4):

``w1``  read-heavy agent: heavy-tailed reports per key, mostly re-assertions, a few changes/corrections/withdrawals,
        queries per appended report ``r`` (default 3).
``w2``  long-history keys: a few hot keys are driven to the environment budget (7) with competing values, then pruned by
        withdrawals; W1 traffic as background.
``w3``  cascade-heavy: many employer keys depend on a few organisation ``hq_city`` keys that change and are withdrawn.

Every generator takes only plain numbers and a seed, so a run is reproducible and the realised mix is recorded
(:func:`digest`, :func:`mix`). No third-party imports; the only palimem imports are the types and the schema builder.
"""

from __future__ import annotations

import hashlib
import random
from bisect import bisect_left
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import accumulate

from palimem.compat import schema_from_kernel
from palimem.kernel import AttrSpec, KernelSchema, RuleSpec
from palimem.types import Cue, Key, Origin, Report, Schema, Source, ValueProp

CITIES = tuple(f"city{i:02d}" for i in range(48))
SOURCES = (("press", "standard"), ("registry", "trusted"), ("wire", "standard"), ("blog", "low"), ("agency", "standard"))
ENV_BUDGET = 7  # the default environment budget (S-06): keys are kept at or below it
WORKLOADS = ("w1", "w2", "w3")


@dataclass(frozen=True)
class AppendOp:
    index: int  # position among appends (0-based); the LSN of the resulting log entry is index + 1
    entity: str
    attr: str
    value: str | None  # ``None`` for a withdrawal
    cue: Cue
    source: str
    group: str
    target: int | None = None  # index of the earlier append this withdraws or corrects


@dataclass(frozen=True)
class QueryOp:
    entity: str
    attr: str
    as_of_lsn: int | None  # ``None`` = the head


Op = AppendOp | QueryOp


def entity_counts(n_reports: int, persons: int | None = None) -> tuple[int, int]:
    """(persons, organisations) for a run of ``n_reports`` appends."""
    persons = persons if persons is not None else max(20, n_reports // 4)
    orgs = max(5, persons // 20)
    return persons, orgs


def perf_kernel_schema(n_reports: int, persons: int | None = None) -> KernelSchema:
    persons, orgs = entity_counts(n_reports, persons)
    ents = tuple(f"p{i}" for i in range(persons)) + tuple(f"o{i}" for i in range(orgs))
    return KernelSchema(
        attrs={
            "employer": AttrSpec("employer", "single", True),
            "residence": AttrSpec("residence", "single", True),
            "hq_city": AttrSpec("hq_city", "single", True),  # organisations move
            "work_city": AttrSpec("work_city", "single", True, error_allowed=False, derived=True),
            "affiliations": AttrSpec("affiliations", "multi", False, competing_values=False),
        },
        rules=(RuleSpec(id="r1", head=("work_city", "?e", "?c"), body=(("employer", "?e", "?x"), ("hq_city", "?x", "?c"))),),
        entities=ents,
    )


def perf_schema(n_reports: int, persons: int | None = None) -> tuple[Schema, KernelSchema]:
    ks = perf_kernel_schema(n_reports, persons)
    return schema_from_kernel(ks), ks


def to_report(op: AppendOp, id_of: dict[int, str]) -> Report:
    """The contract ``Report`` of an append op; ``id_of`` maps earlier append indexes to log-assigned ids."""
    return Report(
        key=Key(entity=op.entity, attr=op.attr),
        cue=op.cue,
        proposition=None if op.cue is Cue.WITHDRAW else ValueProp(value=op.value),
        source=Source(id=op.source, cls=dict(SOURCES)[op.source]),
        origin=Origin.EXTERNAL_OBSERVATION,
        origin_group=op.group,
        actor=f"connector:{op.source}",
        target=None if op.target is None else id_of[op.target],
    )


class _Zipf:
    """Heavy-tailed choice over ``n`` items (rank r has weight 1/r^s), O(log n) per draw."""

    def __init__(self, n: int, s: float, rng: random.Random) -> None:
        self.cum = list(accumulate(1.0 / (r**s) for r in range(1, n + 1)))
        self.rng = rng

    def draw(self) -> int:
        return min(bisect_left(self.cum, self.rng.random() * self.cum[-1]), len(self.cum) - 1)


class _State:
    """What a generator knows about the stream so far (live reports per key, recently written keys)."""

    def __init__(self, n_reports: int, rng: random.Random, r: float, persons: int | None = None) -> None:
        self.rng = rng
        self.persons, self.orgs = entity_counts(n_reports, persons)
        self.person_pick = _Zipf(self.persons, 0.6, rng)  # heavy-tailed but flat enough that few keys sit at the budget
        self.org_pick = _Zipf(self.orgs, 1.0, rng)
        self.n_appends = 0
        # key -> live reports [(index, source, group, value)], oldest first
        self.live: dict[tuple[str, str], list[tuple[int, str, str, str]]] = {}
        self.written: list[tuple[str, str]] = []
        self.recent: list[tuple[str, str]] = []
        self.r = r
        self.credit = 0.0

    def emit(self, entity: str, attr: str, value: str | None, cue: Cue, source: str, group: str, target: int | None = None) -> AppendOp:
        op = AppendOp(self.n_appends, entity, attr, value, cue, source, group, target)
        self.n_appends += 1
        key = (entity, attr)
        if cue is Cue.WITHDRAW:
            self.live[key] = [x for x in self.live.get(key, []) if x[0] != target]
        else:
            if key not in self.live:
                self.written.append(key)
            if cue is Cue.CORRECT:
                self.live[key] = [x for x in self.live.get(key, []) if x[0] != target]
            self.live.setdefault(key, []).append((op.index, source, group, str(value)))
            self.recent.append(key)
            if len(self.recent) > 256:
                del self.recent[:128]
        return op

    def queries(self) -> Iterator[QueryOp]:
        self.credit += self.r
        while self.credit >= 1.0 and self.written:
            self.credit -= 1.0
            rng = self.rng
            key = self.recent[rng.randrange(len(self.recent))] if self.recent and rng.random() < 0.7 else self.written[rng.randrange(len(self.written))]
            entity, attr = key
            if attr == "employer" and rng.random() < 0.3:
                attr = "work_city"
            as_of = rng.randint(1, self.n_appends) if rng.random() < 0.15 else None
            yield QueryOp(entity, attr, as_of)


def _person_key(st: _State) -> tuple[str, str]:
    p = f"p{st.person_pick.draw()}"
    roll = st.rng.random()
    attr = "employer" if roll < 0.5 else ("residence" if roll < 0.8 else "affiliations")
    return p, attr


def _value_for(st: _State, attr: str) -> str:
    if attr == "employer":
        return f"o{st.org_pick.draw()}"
    if attr == "affiliations":
        return f"club{st.rng.randrange(40)}"
    return CITIES[st.rng.randrange(len(CITIES))]


def _own(st: _State, key: tuple[str, str]) -> tuple[int, str, str, str] | None:
    live = st.live.get(key)
    return live[st.rng.randrange(len(live))] if live else None


def _prune(st: _State, key: tuple[str, str], keep: int) -> Iterator[AppendOp]:
    """Keep a key at or below the environment budget: withdraw its oldest live reports (by their own source)."""
    live = st.live.get(key, [])
    for idx, source, group, _v in list(live[: max(0, len(live) - keep)]):
        yield st.emit(key[0], key[1], None, Cue.WITHDRAW, source, group, target=idx)


def _w1_step(st: _State, hq_change: float = 0.03) -> Iterator[AppendOp]:
    rng = st.rng
    roll = rng.random()
    if roll < hq_change:  # an organisation moves
        o = f"o{st.org_pick.draw()}"
        yield from _prune(st, (o, "hq_city"), ENV_BUDGET - 1)
        yield st.emit(o, "hq_city", CITIES[rng.randrange(len(CITIES))], Cue.CHANGE, "registry", "registry")
        return
    key = _person_key(st)
    own = _own(st, key)
    yield from _prune(st, key, ENV_BUDGET - 1)
    src, _grp = SOURCES[rng.randrange(len(SOURCES))]
    if own is None or roll < 0.25:  # a new key (or a first report)
        yield st.emit(key[0], key[1], _value_for(st, key[1]), Cue.ASSERT, src, src)
    elif roll < 0.72:  # a re-assertion of the current value, often by another origin group
        yield st.emit(key[0], key[1], own[3], Cue.ASSERT, src, src if rng.random() < 0.7 else own[2])
    elif roll < 0.90:  # a change (a set-valued attribute only ever gains members)
        cue = Cue.ASSERT if key[1] == "affiliations" else Cue.CHANGE
        yield st.emit(key[0], key[1], _value_for(st, key[1]), cue, own[1], own[2])
    elif roll < 0.975:  # the source corrects its own earlier report (a set-valued attribute gains a member instead)
        if key[1] == "affiliations":
            yield st.emit(key[0], key[1], _value_for(st, key[1]), Cue.ASSERT, own[1], own[2])
        else:
            yield st.emit(key[0], key[1], _value_for(st, key[1]), Cue.CORRECT, own[1], own[2], target=own[0])
    else:  # the source withdraws its own earlier report (about 2.5% of steps)
        yield st.emit(key[0], key[1], None, Cue.WITHDRAW, own[1], own[2], target=own[0])


def _seed_orgs(st: _State) -> Iterator[AppendOp]:
    for i in range(st.orgs):
        yield st.emit(f"o{i}", "hq_city", CITIES[st.rng.randrange(len(CITIES))], Cue.ASSERT, "registry", "registry")


def _w2_step(st: _State, hot: list[tuple[str, str]]) -> Iterator[AppendOp]:
    rng = st.rng
    if rng.random() >= 0.4:
        yield from _w1_step(st)
        return
    key = hot[rng.randrange(len(hot))]
    live = st.live.get(key, [])
    if len(live) >= ENV_BUDGET - 1:  # at the budget: prune three, then continue
        yield from _prune(st, key, ENV_BUDGET - 4)
    src, _grp = SOURCES[rng.randrange(len(SOURCES))]
    competing = rng.random() < 0.5
    own = _own(st, key)
    if own is not None and not competing:
        yield st.emit(key[0], key[1], own[3], Cue.ASSERT, src, src)
    elif own is not None and rng.random() < 0.3:
        yield st.emit(key[0], key[1], _value_for(st, key[1]), Cue.CORRECT, own[1], own[2], target=own[0])
    else:
        yield st.emit(key[0], key[1], _value_for(st, key[1]), Cue.ASSERT, src, src)


def _w3_step(st: _State) -> Iterator[AppendOp]:
    rng = st.rng
    roll = rng.random()
    if roll < 0.10:  # a hot organisation's headquarters changes: every dependent work_city moves
        o = f"o{st.org_pick.draw()}"
        yield from _prune(st, (o, "hq_city"), ENV_BUDGET - 1)
        yield st.emit(o, "hq_city", CITIES[rng.randrange(len(CITIES))], Cue.CHANGE, "registry", "registry")
    elif roll < 0.15:  # a headquarters report is withdrawn (two steps upstream of work_city) and re-asserted
        o = f"o{st.org_pick.draw()}"
        own = _own(st, (o, "hq_city"))
        if own is not None:
            yield st.emit(o, "hq_city", None, Cue.WITHDRAW, own[1], own[2], target=own[0])
        yield st.emit(o, "hq_city", CITIES[rng.randrange(len(CITIES))], Cue.ASSERT, "registry", "registry")
    else:  # employer assertions pile onto the few hot organisations
        p = f"p{st.person_pick.draw()}"
        key = (p, "employer")
        yield from _prune(st, key, ENV_BUDGET - 1)
        src, _grp = SOURCES[rng.randrange(len(SOURCES))]
        own = _own(st, key)
        if own is not None and rng.random() < 0.3:
            yield st.emit(p, "employer", f"o{st.org_pick.draw()}", Cue.CHANGE, own[1], own[2])
        else:
            yield st.emit(p, "employer", f"o{st.org_pick.draw()}", Cue.ASSERT, src, src)


def generate(workload: str, n_reports: int, *, r: float = 3.0, seed: int = 1, persons: int | None = None) -> Iterator[Op]:
    """The operation stream of ``workload`` with ``n_reports`` appends and ``r`` queries per append (deterministic)."""
    if workload not in WORKLOADS:
        raise ValueError(f"unknown workload {workload!r}; choose one of {WORKLOADS}")
    rng = random.Random(f"{workload}:{n_reports}:{r}:{seed}:{persons}")
    st = _State(n_reports, rng, r, persons)
    hot: list[tuple[str, str]] = []
    if workload == "w2":
        hot = [(f"p{i}", "employer" if i % 2 == 0 else "residence") for i in range(min(20, st.persons))]
    pending: list[AppendOp] = list(_seed_orgs(st))
    while st.n_appends < n_reports or pending:
        if not pending:
            step = {"w1": _w1_step, "w2": lambda s: _w2_step(s, hot), "w3": _w3_step}[workload]
            pending.extend(step(st))
        op = pending.pop(0)
        if op.index >= n_reports:
            break
        yield op
        yield from st.queries()


def mix(ops: list[Op]) -> dict[str, int]:
    """The realised mix of a generated stream (recorded in every report)."""
    c: Counter[str] = Counter()
    for op in ops:
        if isinstance(op, QueryOp):
            c["query_historical" if op.as_of_lsn is not None else "query_current"] += 1
            c["query_derived"] += op.attr == "work_city"
        else:
            c[f"append_{op.cue.value}"] += 1
    return dict(sorted(c.items()))


def digest(workload: str, n_reports: int, *, r: float = 3.0, seed: int = 1, persons: int | None = None) -> str:
    """A hash of the whole operation stream: two runs with the same arguments must agree (the determinism check)."""
    h = hashlib.sha256()
    for op in generate(workload, n_reports, r=r, seed=seed, persons=persons):
        h.update(repr(op).encode())
    return h.hexdigest()
