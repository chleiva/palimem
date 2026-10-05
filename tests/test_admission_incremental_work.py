"""The point of incremental admission: a plain append does no work that grows with the log (operation counts, never
wall-clock). An actor append (withdraw, authorised correct) is the one bounded slow path and is measured too."""

from __future__ import annotations

from palimem.admission import AdmissionConfig, Admitter, IncrementalAdmission
from palimem.types import Cue
from tests._adm import Log


def _grow(log: Log, inc: IncrementalAdmission, ref: str, cue: Cue = Cue.ASSERT, **kw: object) -> None:
    entry = log.add(ref, cue, **kw)  # type: ignore[arg-type]
    inc.sync(log.list, before_lsn=entry.lsn)
    inc.append(entry)


def _work(inc: IncrementalAdmission) -> int:
    return sum(inc.work.values())


def test_plain_appends_scan_nothing_however_many_actors_and_reports_exist() -> None:
    log = Log()
    inc = IncrementalAdmission(Admitter(AdmissionConfig()))
    # 300 actors first: each withdraws one earlier report of its own source
    for i in range(300):
        _grow(log, inc, f"a{i}", entity=f"e{i}")
        _grow(log, inc, f"w{i}", Cue.WITHDRAW, entity=f"e{i}", target=f"a{i}", value=None)
    before = _work(inc)
    assert before > 0  # the actor appends took the bounded slow path
    # then thousands of plain asserts on fresh keys from an uncovered source
    for i in range(3000):
        _grow(log, inc, f"p{i}", entity=f"x{i}", source="press")
    assert _work(inc) == before, "a plain append must not scan actors, keys or sources"


def test_plain_append_work_does_not_depend_on_the_length_of_the_log() -> None:
    def per_append(n: int) -> float:
        log = Log()
        inc = IncrementalAdmission(Admitter(AdmissionConfig()))
        for i in range(n):
            _grow(log, inc, f"p{i}", entity=f"x{i % 50}", attr="employer", value=f"v{i % 4}", source=f"s{i % 5}")
        return _work(inc) / n

    short, long = per_append(200), per_append(2000)
    assert long <= short + 1e-9, (short, long)


def test_an_actor_append_costs_the_actors_not_the_log() -> None:
    log = Log()
    inc = IncrementalAdmission(Admitter(AdmissionConfig()))
    for i in range(2000):
        _grow(log, inc, f"p{i}", entity=f"x{i}")
    base = _work(inc)
    _grow(log, inc, "w", Cue.WITHDRAW, entity="x5", target="p5", value=None)
    cost_one_actor = _work(inc) - base
    assert 0 < cost_one_actor <= 10, "one actor in a 2,000-report log: the slow path scans only the actors"
