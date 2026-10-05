"""Negative evidence in the open-world product kernel (author ruling 4 of 2026-10-05).

The kernel's closed-form worlds (``palimem.kernel.polarity``) are checked against an **independent brute-force reference
written here from the definition** (enumerate every TRUE/ERR labelling, keep the admissible ones, read the status off the
resulting worlds), never derived from the kernel under test. The reference implements the semantics the module docstring
states: a denial and an affirmation about the same value conflict, everything else is compatible; consistency forbids two
conflicting TRUE reports; A-ERR lets a report be ERR only if a TRUE report conflicts with it.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from itertools import product

import pytest

from palimem.kernel import AttrSpec, KernelSchema, KernelUnsupported, justify_key
from palimem.types import (
    Cue,
    KernelStatus,
    Key,
    MemberProp,
    NotMemberProp,
    NotValueProp,
    Profile,
    SemanticConfig,
    ValueProp,
)
from tests._adm import Log

OPEN = SemanticConfig(self_update=False, profile=Profile.OPEN_WORLD)
SCHEMA = KernelSchema(
    attrs={
        "employer": AttrSpec("employer", "single", True),
        "affiliations": AttrSpec("affiliations", "multi", False, competing_values=False),
    },
    entities=("alice",),
)
VALUES = ("a", "b", "c")


# ------------------------------------------------------------------------------ the reference (from the definition)


def conflicts(x: tuple[bool, str], y: tuple[bool, str]) -> bool:
    """Two reports (negative?, value) conflict iff they are about the same value with opposite polarity."""
    return x[1] == y[1] and x[0] != y[0]


def admissible_labellings(reports: list[tuple[bool, str]]) -> list[tuple[int, ...]]:
    """Every TRUE(1)/ERR(0) labelling that is consistent and whose every ERR report is disputed by a TRUE one."""
    out: list[tuple[int, ...]] = []
    for lab in product((1, 0), repeat=len(reports)):
        true = [r for r, x in zip(reports, lab, strict=True) if x]
        if any(conflicts(r, s) for i, r in enumerate(true) for s in true[i + 1 :]):
            continue  # consistency
        if any(not x and not any(conflicts(r, s) for s in true) for r, x in zip(reports, lab, strict=True)):
            continue  # A-ERR
        out.append(lab)
    return out


Shape = tuple[str, frozenset[tuple[str, str]]]  # (status, {(form, value-or-values-as-text)})


def forms_of(kind: str, vals: list[str]) -> frozenset[tuple[str, str]]:
    return frozenset((f"{'not_value' if kind == 'single' else 'not_member'}", v) for v in vals)


def reference(kind: str, reports: list[tuple[bool, str]]) -> Shape:
    """The expected (status, candidates) read off the admissible worlds, by the rules of the module docstring."""
    labs = admissible_labellings(reports)
    pos_name, neg_name = ("value", "not_value") if kind == "single" else ("set", "not_member")
    worlds = []
    for lab in labs:
        true = [r for r, x in zip(reports, lab, strict=True) if x]
        worlds.append((frozenset(v for neg, v in true if not neg), frozenset(v for neg, v in true if neg)))
    members = {w[0] for w in worlds}
    if len(members) == 1:
        (m,) = members
        if m:  # every interpretation agrees on a non-empty value / set: established
            txt = ",".join(sorted(m))
            return ("established", frozenset({(pos_name, txt)}))
        # only denials: the same denials in every world
        denied = sorted({v for _, n in worlds for v in n})
        if len(denied) == 1:
            return ("established_false", frozenset({(neg_name, denied[0])}))
        return ("unknown", frozenset((neg_name, v) for v in denied))
    # several readings: the positive worlds as candidates, the empty reading by the denials that conflict with a positive
    cands: set[tuple[str, str]] = set()
    positive_values = {v for neg, v in reports if not neg}
    for m, n in worlds:
        if m:
            cands.add((pos_name, ",".join(sorted(m))))
        else:
            cands |= {(neg_name, v) for v in n if v in positive_values}
    return ("unresolved", frozenset(cands))


# ------------------------------------------------------------------------------ the kernel's answer in the same shape


def kernel_shape(kind: str, reports: list[tuple[bool, str]]) -> Shape:
    attr = "employer" if kind == "single" else "affiliations"
    key = Key(entity="alice", attr=attr)
    log = Log()
    entries = []
    for i, (neg, v) in enumerate(reports):
        if kind == "single":
            prop = NotValueProp(value=v) if neg else ValueProp(value=v)
        else:
            prop = NotMemberProp(value=v) if neg else MemberProp(value=v)
        entries.append(log.add(f"r{i}", Cue.ASSERT, attr=attr, source=f"s{i}", proposition=prop))
    j = justify_key(SCHEMA, key, entries, OPEN)
    seg = j.segments()[0]  # type: ignore[union-attr]
    cs = ([seg.established] if seg.established is not None else []) + list(seg.alternatives)
    out: set[tuple[str, str]] = set()
    for c in cs:
        f = c.form
        txt = ",".join(sorted(f.values)) if hasattr(f, "values") else f.value  # type: ignore[union-attr]
        out.add((f.form, txt))
    return (seg.kernel_status.value, frozenset(out))


def in_scope(kind: str, reports: list[tuple[bool, str]]) -> bool:
    pos = {v for neg, v in reports if not neg}
    neg = {v for neg_, v in reports if neg_}
    if kind == "single":
        return len(pos) <= 1
    return len(pos & neg) <= 1


def random_reports(rng: random.Random) -> tuple[str, list[tuple[bool, str]]]:
    kind = rng.choice(["single", "multi"])
    n = rng.randint(1, 5)
    reports = [(rng.random() < 0.5, rng.choice(VALUES)) for _ in range(n)]
    if not any(neg for neg, _ in reports):
        reports[0] = (True, reports[0][1])  # the polarity kernel only runs when a denial is present
    return kind, reports


# ------------------------------------------------------------------------------ the tests


def cases() -> Iterator[tuple[str, list[tuple[bool, str]]]]:
    rng = random.Random(20261005)
    seen = 0
    while seen < 600:
        kind, reports = random_reports(rng)
        if in_scope(kind, reports):
            seen += 1
            yield kind, reports


@pytest.mark.parametrize("kind,reports", list(cases()))
def test_the_kernel_matches_the_brute_force_reference(kind: str, reports: list[tuple[bool, str]]) -> None:
    assert kernel_shape(kind, reports) == reference(kind, reports)


def test_the_reference_itself_gives_the_rulings_cases() -> None:
    # two compatible denials: unknown, both listed as constraints (ruling 4)
    assert reference("single", [(True, "a"), (True, "b")]) == ("unknown", frozenset({("not_value", "a"), ("not_value", "b")}))
    # one denial alone: established_false (design v0.3, explicit negative evidence)
    assert reference("single", [(True, "a")]) == ("established_false", frozenset({("not_value", "a")}))
    # positive + negative on the same value: unresolved with both readings (design)
    assert reference("single", [(False, "a"), (True, "a")]) == ("unresolved", frozenset({("value", "a"), ("not_value", "a")}))
    assert reference("multi", [(False, "a"), (True, "a")]) == ("unresolved", frozenset({("set", "a"), ("not_member", "a")}))
    # a denial of another value is a consistent constraint, not a rival
    assert reference("single", [(False, "a"), (True, "b")]) == ("established", frozenset({("value", "a")}))


def test_the_reference_is_sensitive_to_a_wrong_semantics() -> None:
    """A mutation of the reference (a denial vetoes a positive) disagrees with the kernel on the conflict case: the
    comparison above can fail."""
    veto = ("established_false", frozenset({("not_value", "a")}))
    assert kernel_shape("single", [(False, "a"), (True, "a")]) != veto


@pytest.mark.parametrize(
    "kind,reports",
    [
        ("single", [(False, "a"), (False, "b"), (True, "a")]),  # competing positives with a denial: no oracle
        ("multi", [(False, "a"), (True, "a"), (False, "b"), (True, "b")]),  # two disputed members
    ],
)
def test_outside_the_scope_the_kernel_refuses_instead_of_guessing(kind: str, reports: list[tuple[bool, str]]) -> None:
    assert not in_scope(kind, reports)
    with pytest.raises(KernelUnsupported):
        kernel_shape(kind, reports)


def test_cues_and_valid_time_beside_denials_are_refused_and_the_compat_profile_rejects_denials() -> None:
    key = Key(entity="alice", attr="employer")
    log = Log()
    e1 = log.add("r1", Cue.ASSERT, source="s1", proposition=ValueProp(value="a"))
    e2 = log.add("r2", Cue.ASSERT, source="s2", proposition=NotValueProp(value="b"))
    e3 = log.add("r3", Cue.CHANGE, source="s3", proposition=ValueProp(value="a"))
    with pytest.raises(KernelUnsupported):
        justify_key(SCHEMA, key, [e1, e2, e3], OPEN)  # a change cue beside a denial: no oracle yet
    with pytest.raises(KernelUnsupported):
        justify_key(SCHEMA, key, [e1, e2], SemanticConfig(self_update=False, profile=Profile.REVISE_STREAM_V1))


def test_an_unknown_segment_lists_negative_constraints_and_the_kernel_status_is_not_established() -> None:
    j = justify_key(SCHEMA, Key(entity="alice", attr="employer"), _two_denials(), OPEN)
    seg = j.segments()[0]  # type: ignore[union-attr]
    assert seg.kernel_status is KernelStatus.UNKNOWN and seg.established is None
    assert sorted(c.form.value for c in seg.alternatives) == ["a", "b"]  # type: ignore[union-attr]
    assert all(c.id in seg.support for c in seg.alternatives)  # each constraint carries its own support


def _two_denials() -> list:
    log = Log()
    return [
        log.add("r1", Cue.ASSERT, source="s1", proposition=NotValueProp(value="a")),
        log.add("r2", Cue.ASSERT, source="s2", proposition=NotValueProp(value="b")),
    ]


def test_supports_are_the_subset_minimal_environments_over_base_reports() -> None:
    log = Log()
    r1 = log.add("r1", Cue.ASSERT, source="s1", proposition=ValueProp(value="a"))
    r2 = log.add("r2", Cue.ASSERT, source="s2", proposition=NotValueProp(value="a"))
    r3 = log.add("r3", Cue.ASSERT, source="s3", proposition=NotValueProp(value="b"))  # an unrelated denial
    j = justify_key(SCHEMA, Key(entity="alice", attr="employer"), [r1, r2, r3], OPEN)
    seg = j.segments()[0]  # type: ignore[union-attr]
    by_form = {c.form.form: tuple(s.environment for s in seg.support[c.id]) for c in seg.alternatives}  # type: ignore[union-attr]
    assert by_form == {"value": ((r1.report.id,),), "not_value": ((r2.report.id,),)}  # the unrelated denial is in neither
    assert j.admitted_ids == (r1.report.id, r2.report.id, r3.report.id)  # but it is pinned: the belief consumed it
