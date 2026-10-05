"""The class state of merge decisions: classes, representatives, pins, no-ops and markers."""

from __future__ import annotations

from palimem.entities.registry import (
    ENTITY_MERGE_ATTR,
    MergeDecision,
    MergeOp,
    MergeRegistry,
    apply_ops,
    decode_marker,
    encode_marker,
    from_host,
)
from palimem.types import Cue, Key, MemberProp, Origin, Report, Source, ValueProp

ULIDS = [f"01ARZ3NDEKTSV4RRFFQ69G5FA{c}" for c in "ABCDEFGHJK"]


def merge(i: int, alias: str, into: str, lsn: int | None = None) -> MergeDecision:
    return MergeDecision(
        id=ULIDS[i], lsn=lsn or i + 1, op=MergeOp.MERGE, alias=alias, into=into, target=None, reason="r", method="manual",
        score=None, proposer="system:x",
    )


def unmerge(i: int, target: int, lsn: int | None = None) -> MergeDecision:
    return MergeDecision(
        id=ULIDS[i], lsn=lsn or i + 1, op=MergeOp.UNMERGE, alias="a", into=None, target=ULIDS[target], reason="r",
        method="manual", score=None, proposer="system:x",
    )


def test_a_merge_makes_one_class_with_one_representative() -> None:
    st = apply_ops([merge(0, "A", "B")])
    assert st.canon("A") == "B" and st.canon("B") == "B" and st.canon("Z") == "Z"
    assert st.members("A") == ("A", "B") and st.members("Z") == ("Z",)


def test_chained_merges_resolve_to_the_final_representative() -> None:
    st = apply_ops([merge(0, "A", "B"), merge(1, "B", "C")])
    assert {st.canon(x) for x in "ABC"} == {"C"} and st.members("A") == ("A", "B", "C")
    # merging by an alias name resolves to the class representatives first
    st2 = apply_ops([merge(0, "A", "B"), merge(1, "C", "A")])  # C joins the class of A (rep B)
    assert {st2.canon(x) for x in "ABC"} == {"B"}


def test_a_merge_inside_one_class_and_an_unknown_unmerge_are_recorded_no_ops() -> None:
    st = apply_ops([merge(0, "A", "B"), merge(1, "B", "A"), unmerge(2, 9)])
    assert len(st.edges) == 1 and st.members("A") == ("A", "B")


def test_unmerge_removes_exactly_one_edge() -> None:
    ops = [merge(0, "A", "B"), merge(1, "B", "C")]
    st = apply_ops([*ops, unmerge(2, 0)])  # undo A-B: B and C stay merged
    assert st.members("B") == ("B", "C") and st.members("A") == ("A",)
    st2 = apply_ops([*ops, unmerge(2, 1)])  # undo B-C: A and B stay merged, C is alone
    assert st2.members("A") == ("A", "B") and st2.members("C") == ("C",)
    assert st2.canon("A") == "B"


def test_merge_pins_follow_the_evidence_not_the_edges() -> None:
    st = apply_ops([merge(0, "A", "B"), merge(1, "B", "C")])  # class {A, B, C}, representative C
    assert st.merge_pins("C", ["C"]) == ()  # all evidence on the representative: nothing crossed a merge
    assert st.merge_pins("C", ["B", "C"]) == (ULIDS[1],)  # B's evidence crossed B->C only
    assert st.merge_pins("C", ["A"]) == tuple(sorted((ULIDS[0], ULIDS[1])))  # A's crossed both
    assert st.merge_pins("C", []) == ()


def test_registry_state_is_a_function_of_the_log_position() -> None:
    reg = MergeRegistry()
    reg.add(merge(0, "A", "B", lsn=5))
    reg.add(unmerge(1, 0, lsn=9))
    assert reg.state_at(4).empty and reg.state_at(5).members("A") == ("A", "B")
    assert reg.state_at(8).canon("A") == "B" and reg.state_at(9).canon("A") == "A"
    assert [m.id for m in reg.active_merges(8)] == [ULIDS[0]] and reg.active_merges(9) == ()
    assert [o.op for o in reg.history()] == [MergeOp.MERGE, MergeOp.UNMERGE]
    reg.reset()
    assert reg.state_at(8).empty and reg.scanned == 0


def marker_report(text: str, *, attr: str = ENTITY_MERGE_ATTR, cue: Cue = Cue.ASSERT, prop: object | None = None) -> Report:
    return Report(
        key=Key(entity="Alias", attr=attr), cue=cue, proposition=prop if prop is not None else MemberProp(value=text),  # type: ignore[arg-type]
        source=Source(id="system:er", cls="trusted"), origin=Origin.EXTERNAL_OBSERVATION, origin_group="g",
        actor="system:er",
    )


def test_marker_round_trip_and_strict_decoding() -> None:
    good = encode_marker(MergeOp.MERGE, into="Real", reason="same", method="lexical", score=0.91)
    body = decode_marker(marker_report(good))
    assert body is not None and body["into"] == "Real" and body["score"] == 0.91
    assert decode_marker(marker_report(encode_marker(MergeOp.UNMERGE, target=ULIDS[0], reason="r"))) is not None
    # everything else is ignored, never raised
    assert decode_marker(marker_report("not json")) is None
    assert decode_marker(marker_report('{"v":1,"op":"merge","reason":"r","method":"m"}')) is None  # no `into`
    assert decode_marker(marker_report('{"v":2,"op":"merge","into":"x","reason":"r","method":"m"}')) is None  # version
    assert decode_marker(marker_report('{"v":1,"op":"merge","into":"Alias","reason":"r","method":"m"}')) is None  # self-merge
    assert decode_marker(marker_report('{"v":1,"op":"frobnicate","reason":"r","method":"m"}')) is None
    assert decode_marker(marker_report(good, prop=ValueProp(value=good))) is None  # not a member proposition
    assert decode_marker(marker_report(good, attr="employer")) is None  # not the reserved attribute


def test_only_host_principals_with_an_external_origin_are_honoured() -> None:
    ok = marker_report(encode_marker(MergeOp.MERGE, into="x", reason="r"))
    assert from_host(ok)
    from dataclasses import replace

    assert not from_host(replace(ok, actor="agent:a"))
    assert not from_host(replace(ok, actor="connector:c"))
    assert not from_host(replace(ok, source=Source(id="connector:c", cls="trusted")))
    assert not from_host(replace(ok, origin=Origin.AGENT_STATEMENT))
    assert from_host(replace(ok, actor="user:alice", source=Source(id="user:alice", cls="trusted")))
