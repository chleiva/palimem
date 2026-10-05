"""A-ERR is evaluated per key in both profiles; segments are cut afterwards (author ruling 5 of 2026-10-05).

An interpretation labels a REPORT TRUE or ERR, and a report is one object; admissibility (A-ERR: an error needs a dispute by a
TRUE report) is therefore decided once per key, and the valid-time segments are cut afterwards from the interpretations. The
consequence tested here: a LATER report still makes an EARLIER report possibly wrong in segments that end before the later report
starts, so those segments are not established. A per-segment A-ERR would have called them established.

The expectations are derived by hand (below), and the per-key enumeration is the validated oracle's own method
(``tests/test_kernel_oracle_property.py`` compares the kernel's interpretation sets with the study's ``oracle_v1``).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from palimem.kernel import AttrSpec, KernelSchema, justify_key
from palimem.types import Cue, KernelStatus, Key, Profile, SemanticConfig, ValueProp
from tests._adm import T0, Log

SCHEMA = KernelSchema(attrs={"employer": AttrSpec("employer", "single", True)}, entities=("alice",))
KEY = Key(entity="alice", attr="employer")


def two_reports():
    """r1 says Acme at day 10, r2 says Globex at day 100 (no cues; recorded_at is the anchor)."""
    log = Log()
    e1 = replace(log.add("r1", Cue.ASSERT, source="s1", proposition=ValueProp(value="acme")), recorded_at=T0 + timedelta(days=10))
    e2 = replace(log.add("r2", Cue.ASSERT, source="s2", proposition=ValueProp(value="globex")), recorded_at=T0 + timedelta(days=100))
    return e1, e2


def forms(seg):
    return sorted(c.form.form if c.form.form == "empty" else c.form.value for c in ([seg.established] if seg.established else []) + list(seg.alternatives))


@pytest.mark.parametrize("profile", [Profile.OPEN_WORLD, Profile.REVISE_STREAM_V1])
def test_interpretations_label_reports_per_key(profile: Profile) -> None:
    e1, e2 = two_reports()
    j = justify_key(SCHEMA, KEY, [e1, e2], SemanticConfig(self_update=False, profile=profile))
    # by hand: both TRUE (a change in between), r1 ERR (disputed by TRUE r2), r2 ERR (disputed by TRUE r1); both ERR is
    # not admissible (each ERR needs a TRUE disputer): exactly three interpretations
    assert len(j.interpretations) == 3
    assert sorted(len(err) for err, _ in j.interpretations) == [0, 1, 1]
    assert {True, False} <= set(j.erroneous_truths(e1.report.id))  # r1 is ERR in some interpretation, TRUE in others
    assert {True, False} <= set(j.erroneous_truths(e2.report.id))


@pytest.mark.parametrize("profile", [Profile.OPEN_WORLD, Profile.REVISE_STREAM_V1])
def test_a_later_report_makes_an_earlier_segment_unresolved(profile: Profile) -> None:
    e1, e2 = two_reports()
    j = justify_key(SCHEMA, KEY, [e1, e2], SemanticConfig(self_update=False, profile=profile))
    segs = j.segments()
    # before r1 starts nothing is known
    assert segs[0].kernel_status is KernelStatus.UNKNOWN
    # after r1 starts but before r2 starts the key is NOT established: the reading "r1 is wrong" (empty world) exists
    # because TRUE r2 would dispute it, although r2 has not begun
    early = next(s for s in segs if "empty" in forms(s) and "acme" in forms(s))
    assert early.kernel_status is KernelStatus.UNRESOLVED
    # no segment of this key is established at any valid time
    assert all(s.kernel_status is not KernelStatus.ESTABLISHED for s in segs)
    # after both anchors the candidates are the two values
    assert forms(segs[-1]) == ["acme", "globex"]


def test_both_profiles_cut_the_same_segments_from_the_same_interpretations() -> None:
    e1, e2 = two_reports()
    a = justify_key(SCHEMA, KEY, [e1, e2], SemanticConfig(self_update=False, profile=Profile.OPEN_WORLD))
    b = justify_key(SCHEMA, KEY, [e1, e2], SemanticConfig(self_update=False, profile=Profile.REVISE_STREAM_V1))
    assert [(s.valid_from, s.valid_to, s.kernel_status, forms(s)) for s in a.segments()] == [
        (s.valid_from, s.valid_to, s.kernel_status, forms(s)) for s in b.segments()
    ]
