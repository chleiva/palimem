"""Converter and compat-admission tests on tiny hand-built study streams (needs the study checkout for the
``revise_stream.model`` classes; skipped locally when absent, a failure in CI)."""

from __future__ import annotations

import os
from typing import Any

import pytest

from harness import study
from harness.convert import CompatAdmission, kernel_schema_of, to_converted
from palimem.types import Cue


@pytest.fixture(scope="module")
def M() -> Any:
    try:
        study.load()
    except FileNotFoundError as e:
        if os.environ.get("HARNESS_REQUIRED"):
            pytest.fail(f"study checkout required but unavailable: {e}")
        pytest.skip(f"study checkout unavailable: {e}")
    from revise_stream import model

    return model


def _stream(M: Any, obs: list[Any]) -> Any:
    attrs = {"emp": M.AttributeSpec("emp", "entity", "single", True)}
    sources = {"s1": M.Source("s1", "standard", "gA"), "s2": M.Source("s2", "trusted", "gA"),
               "s3": M.Source("s3", "trusted", "gC")}
    return M.Stream(stream_id="t", entities=["e"], attributes=attrs, rules=[], sources=sources,
                    observations=obs, queries=[])


def _assert(M: Any, oid: str, t: int, src: str, value: str, **kw: Any) -> Any:
    return M.Observation(id=oid, t_rep=t, source=src, kind="assert", entity="e", attr="emp", value=value, **kw)


def _ids(conv: Any, adm: Any, lsn: int) -> list[str]:
    return sorted(conv.study_id[e.report.id] for es in adm.admitted_by_key(lsn).values() for e in es)


def test_source_retraction_expands_into_per_report_withdraws(M: Any) -> None:
    obs = [_assert(M, "o1", 1, "s1", "a"), _assert(M, "o2", 2, "s1", "b"),
           M.Observation(id="o3", t_rep=3, source="s3", kind="retract", target="s1"),
           _assert(M, "o4", 4, "s1", "c")]
    s = _stream(M, obs)
    conv = to_converted(s, "expand")
    cues = [e.report.cue for e in conv.entries]
    assert cues == [Cue.ASSERT, Cue.ASSERT, Cue.WITHDRAW, Cue.WITHDRAW, Cue.ASSERT]
    assert [e.lsn for e in conv.entries] == [1, 2, 3, 4, 5]  # LSN = arrival index of the expanded log
    assert conv.stats["source_retract_expanded_withdraws"] == 2 and not conv.source_retractions
    assert set(conv.expanded_from.values()) == {"o3"}
    # the withdraws target exactly the assertions ingested before the retraction
    assert [e.report.target for e in conv.entries[2:4]] == [conv.ulid_of["o1"], conv.ulid_of["o2"]]
    # known gap: the later assertion o4 stays admitted, while the paper (Stream.admitted) drops it
    adm = CompatAdmission(conv)
    assert _ids(conv, adm, 5) == ["o4"]
    assert sorted(o.id for o in s.admitted(10**9)) == []


def test_sidetable_mode_reproduces_the_paper_exactly(M: Any) -> None:
    obs = [_assert(M, "o1", 1, "s1", "a"),
           M.Observation(id="o3", t_rep=3, source="s3", kind="retract", target="s1"),
           _assert(M, "o4", 4, "s1", "c")]
    s = _stream(M, obs)
    conv = to_converted(s, "sidetable")
    assert conv.source_retractions == [(2, "s1")] and [e.lsn for e in conv.entries] == [1, 3]
    assert _ids(conv, CompatAdmission(conv), 3) == []
    assert _ids(conv, CompatAdmission(conv), 1) == ["o1"]  # before the retraction the report is admitted


def test_retracted_correction_still_withdraws_its_target_in_the_compat_profile(M: Any) -> None:
    obs = [_assert(M, "o1", 1, "s1", "a"),
           _assert(M, "o2", 2, "s2", "b", op_cue="correction", op_of="o1"),  # same origin gA: A-SELF
           M.Observation(id="o3", t_rep=3, source="s3", kind="retract", target="o2")]
    s = _stream(M, obs)
    conv = to_converted(s)
    paper = sorted(o.id for o in s.admitted(10**9))
    assert paper == []  # the paper: o2 is retracted, but o1 is still withdrawn by A-SELF
    assert _ids(conv, CompatAdmission(conv), 3) == paper  # acting_reports_must_be_live=False (compat)
    live = CompatAdmission(conv, acting_reports_must_be_live=True)
    assert _ids(conv, live, 3) == ["o1"]  # product semantics: a withdrawn correction restores its target


def test_compat_profile_sets_inertia_on_every_attribute(M: Any) -> None:
    s = _stream(M, [_assert(M, "o1", 1, "s1", "a")])
    s.attributes["bd"] = M.AttributeSpec("bd", "date", "single", False)
    s.attributes["nick"] = M.AttributeSpec("nick", "string", "multi", False, True, False, False)
    ks = kernel_schema_of(s)
    assert all(a.inertia for a in ks.attrs.values())  # the paper applies the law of inertia (A4) everywhere
