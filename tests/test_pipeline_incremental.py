"""The pipeline's append path runs no whole-log admission evaluation, and the three modes agree."""

from __future__ import annotations

from dataclasses import replace

import pytest

from palimem.admission import Admitter
from palimem.memory import Memory
from palimem.types import Cue, Resolved
from tests._pipeline_helpers import (
    Clock,
    assertion,
    current,
    make_backend,
    src,
    toy_memory,
)


def _run(monkeypatch: pytest.MonkeyPatch, mode: str, n: int = 60) -> tuple[Memory, int]:
    monkeypatch.setenv("PALIMEM_ADMISSION", mode)
    calls = {"n": 0}
    original = Admitter.evaluate

    def counting(self: Admitter, log, *, as_of_lsn=None):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return original(self, log, as_of_lsn=as_of_lsn)

    monkeypatch.setattr(Admitter, "evaluate", counting)
    m = toy_memory(make_backend("memory", Clock()))
    for i in range(n):
        m.append(assertion(f"p{i % 7}", "employer", f"org{i % 3}", source="press"))
    m.append(assertion("p1", "employer", "acme", source="press", cue=Cue.CHANGE))
    return m, calls["n"]


def test_the_incremental_append_path_runs_no_whole_log_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    _, evals = _run(monkeypatch, "incremental")
    assert evals == 0


def test_the_whole_log_mode_still_evaluates_per_append(monkeypatch: pytest.MonkeyPatch) -> None:
    _, evals = _run(monkeypatch, "whole-log")
    assert evals >= 60


def _snapshot(m: Memory) -> dict[str, object]:
    out: dict[str, object] = {}
    for i in range(7):
        for attr in ("employer", "work_city"):
            ans = current(m, f"p{i}", attr)
            assert isinstance(ans, Resolved)
            out[f"p{i}/{attr}"] = (
                ans.kernel_status.value,
                None if ans.justified.segment.established is None else ans.justified.segment.established.id,
                tuple(c.id for c in ans.justified.segment.alternatives),
            )
    return out


def test_the_three_modes_give_the_same_answers_on_every_key(monkeypatch: pytest.MonkeyPatch) -> None:
    snaps = {}
    for mode in ("incremental", "whole-log", "crosscheck"):
        m, _ = _run(monkeypatch, mode, n=40)
        assert m.backend.verify_log().ok
        snaps[mode] = _snapshot(m)
        monkeypatch.undo()
    assert snaps["incremental"] == snaps["whole-log"] == snaps["crosscheck"]
    assert any(v[0] != "unknown" for v in snaps["incremental"].values())  # type: ignore[index]


def test_an_unknown_admission_mode_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PALIMEM_ADMISSION", "sometimes")
    with pytest.raises(ValueError):
        toy_memory(make_backend("memory", Clock()))


def test_crosscheck_mode_catches_a_broken_incremental_state(monkeypatch: pytest.MonkeyPatch) -> None:
    from palimem.admission import IncrementalAdmission

    monkeypatch.setenv("PALIMEM_ADMISSION", "crosscheck")
    monkeypatch.setattr(IncrementalAdmission, "_reconfirm", lambda self, key: [])  # never lifts a quarantine
    m = toy_memory(make_backend("memory", Clock()))
    quarantined = replace(assertion("p0", "employer", "acme", source="q1", group="gq"), source=src("q1", "quarantined"))
    m.append(quarantined)
    with pytest.raises(AssertionError):
        m.append(assertion("p0", "employer", "acme", source="press", group="gp"))  # would confirm the quarantined one
