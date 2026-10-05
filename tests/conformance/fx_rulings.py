"""Fixtures for the author's rulings of 2026-10-05 (docs/decisions/RULINGS-2026-10-05.md), one family per ruling.

As elsewhere in this suite every expectation is derived by hand from the ruling text, never from a kernel. The ``why`` of
each fixture says which ruling it pins and what the by-hand derivation is.
"""

from __future__ import annotations

from typing import Any

from .dsl import (
    ABSENT,
    NV,
    A,
    Q,
    V,
    append,
    c_not_value,
    c_value,
    envs,
    length,
    op,
    query,
    reports,
    resolved,
    unordered,
    withdraw,
)
from .fx_core import d, scen
from .fx_other import BASE, EMP, WORK_CITY

RULINGS = "docs/decisions/RULINGS-2026-10-05.md"


def r01() -> list[dict[str, Any]]:
    """Ruling 1: a failed ``correct`` loses its effect on the target, not its content."""
    out: list[dict[str, Any]] = []
    out.append(scen(
        "r01-failed-correct-content-admitted-as-assert", "A correction that fails authority is an allege for the target, but its content is an assert from its own source",
        "G1", "authority", ["ruling-1", "S-02", "RA-006"],
        "RA-006 in miniature. The registry asserts alice's employer is Acme (r1). The forum (a different source, a different origin group) "
        "'corrects' r1 to Globex (r2). The default authority is the target's own source (S-02), so the correction fails the check: its effect "
        "on the TARGET is an allege (recorded_cue allege, r1 is not withdrawn, r1 stays effective). Ruling 1: its proposition is still a claim "
        "by its own, admissible source, so it is admitted as an ordinary assert (admission outcome admissible, reason admitted). Two admissible "
        "reports disagree and nothing cues either: the key is unresolved with both candidates, one environment each, and the policy asks. "
        "A correction is never worth less than the same statement as an assert, otherwise an attacker would simply use assert.",
        EMP,
        [
            append("r1", d(2, 1), "alice", "employer", V("acme")),
            append("r2", d(2, 5), "alice", "employer", V("globex"), cue="correct", target="$r1", source="forum",
                   expect={"recorded_cue": "allege", "admission": {"outcome": "admissible", "reason": "admitted"}}),
            query("q1", Q("alice", "employer"),
                  resolved("unresolved", decision="ask", _candidates=unordered(c_value("acme"), c_value("globex")),
                           _environments=envs(["$r1"], ["$r2"]))),
            reports("rep_r1", {"id": "$r1"}, {"rows": [{"withdrawn": False}]}),
        ],
        source=RULINGS + " item 1"))

    out.append(scen(
        "r01-failed-correct-is-not-worth-less-than-an-assert", "A cross-source correction and a plain assert of the same value give the same answer",
        "G1", "authority", ["ruling-1", "S-02"],
        "The registry asserts Acme (r1); the forum then states Globex, once as a correction of r1 (fails authority) and, in the control, as a plain "
        "assert. The ruling: the failed correction loses its effect on the target but keeps its content, so both forms give exactly the same "
        "answer (unresolved {Acme, Globex}, the policy asks). Here both appear on two different keys of one store so they can be compared: "
        "alice gets the correction, bob the plain assert.",
        EMP,
        [
            append("a1", d(2, 1), "alice", "employer", V("acme")),
            append("a2", d(2, 5), "alice", "employer", V("globex"), cue="correct", target="$a1", source="forum"),
            append("b1", d(2, 1), "bob", "employer", V("acme")),
            append("b2", d(2, 5), "bob", "employer", V("globex"), source="forum"),
            query("qa", Q("alice", "employer"),
                  resolved("unresolved", decision="ask", _candidates=unordered(c_value("acme"), c_value("globex")))),
            query("qb", Q("bob", "employer"),
                  resolved("unresolved", decision="ask", _candidates=unordered(c_value("acme"), c_value("globex")))),
        ],
        source=RULINGS + " item 1"))
    out[-1]["expect"] = {"equal": [{"a": "qa", "b": "qb", "fields": ["kernel_status", "decision"]}]}

    out.append(scen(
        "r01-sibling-source-same-origin-group-is-not-authority", "A sibling source of the same origin group does not hold authority over the target (RA-007), its content is admitted",
        "G1", "authority", ["ruling-1", "S-02", "RA-007"],
        "RA-007 in miniature: two desks of one newspaper group. desk_b corrects desk_a's report. Product authority is the target's own SOURCE; "
        "origin_group is for corroboration only, never authority (a shared origin is what an attacker can imitate). So the correction fails "
        "the check (recorded_cue allege, r1 not withdrawn) and its content is admitted as an assert from an admissible source. Under P0c "
        "(self-update off, the semantic configuration these fixtures run under) two values from one origin group still compete, so the key "
        "is unresolved {Leeds, Manchester} and the policy asks. (Under P0cSU the same origin group's later value supersedes: that is the "
        "A-SU semantic variant, unchanged by this ruling.)",
        {"office_city": A("single_changeable", vt="entity")},
        [
            append("r1", d(1, 2), "acme", "office_city", V("leeds"), source="desk_a", group="newsco", source_class="standard"),
            append("r2", d(1, 6), "acme", "office_city", V("manchester"), cue="correct", target="$r1", source="desk_b", group="newsco",
                   source_class="standard", expect={"recorded_cue": "allege"}),
            query("q1", Q("acme", "office_city"),
                  resolved("unresolved", decision="ask", _candidates=unordered(c_value("leeds"), c_value("manchester")))),
            reports("rep_r1", {"id": "$r1"}, {"rows": [{"withdrawn": False}]}),
        ],
        sources={"desk_a": {"class": "standard", "origin_group": "newsco"}, "desk_b": {"class": "standard", "origin_group": "newsco"}},
        source=RULINGS + " item 1; RETRACT-ACT RA-007"))

    out.append(scen(
        "r01-authorised-correct-still-withdraws-its-target", "A correction by the target's own source is unchanged: it withdraws its target and asserts the new value",
        "G1", "authority", ["ruling-1", "S-02"],
        "The ruling changes only the corrections that FAIL authority. The registry corrects its own r1 (Acme to Globex): the authority check passes "
        "(own source), r1 is withdrawn, and employer is established Globex with environment {r2}.",
        EMP,
        [
            append("r1", d(2, 1), "alice", "employer", V("acme")),
            append("r2", d(2, 5), "alice", "employer", V("globex"), cue="correct", target="$r1",
                   expect={"recorded_cue": "correct", "admission": {"outcome": "admissible"}}),
            query("q1", Q("alice", "employer"), resolved("established", assertion=c_value("globex"), _environments=envs(["$r2"]))),
            reports("rep_r1", {"id": "$r1"}, {"rows": [{"withdrawn": True}]}),
        ],
        source=RULINGS + " item 1"))
    return out


def r02() -> list[dict[str, Any]]:
    """Ruling 2: ``exclude_source`` is an admission operation, not a report cue."""
    out: list[dict[str, Any]] = []
    ops: list[dict[str, Any]] = [
        append("r1", d(2, 1), "alice", "employer", V("acme"), source="press"),
        append("r2", d(2, 2), "alice", "employer", V("globex"), source="registry"),
        query("q_before", Q("alice", "employer"), resolved("unresolved", _candidates=unordered(c_value("acme"), c_value("globex")))),
        op("exclude_source", name="x1", source="press", from_lsn=1, reason="the press feed was hijacked",
           expect={"admission": {"outcome": "admissible"}}),
        query("q_after", Q("alice", "employer"), resolved("established", assertion=c_value("globex"), _environments=envs(["$r2"]))),
        append("r3", d(2, 10), "alice", "employer", V("initech"), source="press"),
        query("q_late", Q("alice", "employer"), resolved("established", assertion=c_value("globex"), _environments=envs(["$r2"]))),
        query("q_history", Q("alice", "employer", belief_as_of="$r2.lsn"),
              resolved("unresolved", _candidates=unordered(c_value("acme"), c_value("globex")))),
        reports("rep_r1", {"id": "$r1"}, {"rows": [{"id": "$r1"}]}),  # nothing was deleted: the report is still in the log
    ]
    out.append(scen(
        "r02-exclude-source-removes-earlier-and-later-reports", "exclude_source removes the source's earlier reports AND the ones logged after the decision (the late-assert class)",
        "G1", "admission", ["ruling-2", "S-03"],
        "A source going bad is an admission operation: the contract has no source-scope withdraw. The press says Acme (r1), the registry says "
        "Globex (r2): unresolved. The host excludes the press from log position 1 with a reason. The press's earlier report leaves the evidence "
        "set (repaired like a withdrawal): employer is established Globex with environment {r2}. The press then reports Initech (r3, the paper's "
        "'later assertion'): a per-report withdraw could not reach a report that did not exist yet, the exclusion does, so the answer stays "
        "Globex. The history is preserved: a belief_as_of query at r2's position still shows what was believed then (unresolved {Acme, Globex}). "
        "Nothing was deleted: r1 is still in the log.",
        EMP, ops, requires=["source_exclusion"], source=RULINGS + " item 2"))

    out.append(scen(
        "r02-exclude-source-from-lsn-scopes-the-decision", "from_lsn scopes the exclusion to the source's reports at or after that position",
        "G1", "admission", ["ruling-2"],
        "The press reports about alice (r1, position 1) and about bob (r2, position 2). The host excludes the press from r2's position on: "
        "alice's employer, reported before from_lsn, is still heard (established Acme); bob's, at the position, is not (unknown).",
        EMP,
        [
            append("r1", d(2, 1), "alice", "employer", V("acme"), source="press"),
            append("r2", d(2, 2), "bob", "employer", V("globex"), source="press"),
            op("exclude_source", source="press", from_lsn="$r2.lsn", reason="the feed went bad at the second report"),
            query("q_alice", Q("alice", "employer"), resolved("established", assertion=c_value("acme"))),
            query("q_bob", Q("bob", "employer"), {"kernel_status": "unknown"}),
        ],
        requires=["source_exclusion"], source=RULINGS + " item 2"))

    out.append(scen(
        "r02-exclude-source-repairs-derived-beliefs", "Excluding a source repairs everything that rested on it, two steps downstream",
        "G1", "retraction", ["ruling-2", "design-row-1"],
        "work_city(alice) derives from employer(alice) (press) and hq_city(veltran) (registry). Before the decision the derived belief is "
        "established Tessaly. After the host excludes the press the employer report leaves the evidence set and the derived belief loses its "
        "only justification: unknown. hq_city, from the registry, is untouched.",
        {**BASE, "work_city": WORK_CITY},
        [
            append("r1", d(2, 1), "alice", "employer", V("veltran"), source="press"),
            append("r2", d(2, 2), "veltran", "hq_city", V("tessaly"), source="registry"),
            query("q_before", Q("alice", "work_city"), resolved("established", assertion=c_value("tessaly"))),
            op("exclude_source", source="press", from_lsn=1, reason="compromised"),
            query("q_after", Q("alice", "work_city"), {"kernel_status": "unknown"}),
            query("q_registry", Q("veltran", "hq_city"), resolved("established", assertion=c_value("tessaly"))),
        ],
        requires=["source_exclusion"], source=RULINGS + " item 2"))

    out.append(scen(
        "r02-exclude-source-is-reversible-by-a-later-decision", "A restore is a later recorded decision that lifts the exclusion; nothing is rewritten",
        "G1", "admission", ["ruling-2"],
        "After the exclusion of r02-exclude-source-removes-earlier-and-later-reports the host decides the alarm was false and restores the "
        "press from position 1. The press's report is heard again: with only the press and no other source, alice's employer is established Acme.",
        EMP,
        [
            append("r1", d(2, 1), "alice", "employer", V("acme"), source="press"),
            op("exclude_source", source="press", from_lsn=1, reason="suspected compromise"),
            query("q_excluded", Q("alice", "employer"), {"kernel_status": "unknown"}),
            op("restore_source", source="press", from_lsn=1, reason="false alarm: the feed was verified"),
            query("q_restored", Q("alice", "employer"), resolved("established", assertion=c_value("acme"))),
        ],
        requires=["source_exclusion"], source=RULINGS + " item 2"))

    out.append(scen(
        "r02-exclude-source-needs-a-host-principal", "Only a system: or user: principal can exclude a source; an agent's or a connector's attempt is refused",
        "G1", "security", ["ruling-2", "T-02"],
        "Which sources are heard is the host's decision. An exclude_source by an agent principal or a connector is refused (nothing is "
        "logged) and the source is still heard.",
        EMP,
        [
            append("r1", d(2, 1), "alice", "employer", V("acme"), source="press"),
            op("exclude_source", source="press", from_lsn=1, reason="an agent decided", actor="agent:planner",
               expect={"error": {"code": "ExclusionNotHonoured"}}),
            op("exclude_source", source="press", from_lsn=1, reason="a connector decided", actor="connector:press",
               expect={"error": {"code": "ExclusionNotHonoured"}}),
            query("q1", Q("alice", "employer"), resolved("established", assertion=c_value("acme"))),
        ],
        requires=["source_exclusion"], source=RULINGS + " item 2"))
    return out


def r04() -> list[dict[str, Any]]:
    """Ruling 4: negative evidence in the open-world product kernel."""
    out: list[dict[str, Any]] = []
    out.append(scen(
        "r04-two-compatible-denials-are-unknown-with-constraints", "Two compatible not_value reports: unknown, both listed as constraints in alternatives",
        "G1", "negative_evidence", ["ruling-4", "design-row-18", "S-04"],
        "r1 (registry) denies Acme, r2 (press) denies Globex. Denials of different values are compatible, so every interpretation labels both "
        "TRUE; the employer is neither, but the evidence does not say what it is. Ruling 4: kernel_status unknown, with both negatives listed as "
        "constraints in alternatives (a single established candidate cannot carry two denials), nothing committed (decision abstain, no assertion), "
        "one minimal environment per denial. established_false only when completeness makes them exhaustive.",
        EMP,
        [
            append("r1", d(2, 1), "alice", "employer", NV("acme")),
            append("r2", d(2, 2), "alice", "employer", NV("globex"), source="press"),
            query("q1", Q("alice", "employer"),
                  resolved("unknown", decision="abstain", assertion=ABSENT,
                           _candidates=unordered(c_not_value("acme"), c_not_value("globex")), _environments=envs(["$r1"], ["$r2"]))),
        ],
        source=RULINGS + " item 11"))

    out.append(scen(
        "r04-one-denial-alone-is-established-false", "One denial alone is established_false carrying the not_value candidate",
        "G1", "negative_evidence", ["ruling-4", "S-04"],
        "A single explicit denial and nothing else: design v0.3 says explicit negative evidence suffices for established_false. The candidate "
        "is not_value(acme), the environment {r1}, and the policy commits to it.",
        EMP,
        [
            append("r1", d(2, 1), "alice", "employer", NV("acme")),
            query("q1", Q("alice", "employer"),
                  resolved("established_false", decision="commit", assertion=c_not_value("acme"), _environments=envs(["$r1"]))),
        ],
        source=RULINGS + " item 11"))

    out.append(scen(
        "r04-denial-of-another-value-is-a-constraint-not-a-rival", "A denial of a different value is consistent with the positive report and does not make it unresolved",
        "G1", "negative_evidence", ["ruling-4", "S-04"],
        "r1 affirms Acme, r2 denies Globex. They are about different values, so they do not conflict: every interpretation labels both TRUE and the "
        "employer is established Acme. The denial is a consistent constraint (true in every world) and is not listed; the minimal environment of "
        "Acme is {r1} alone.",
        EMP,
        [
            append("r1", d(2, 1), "alice", "employer", V("acme")),
            append("r2", d(2, 2), "alice", "employer", NV("globex"), source="press"),
            query("q1", Q("alice", "employer"),
                  resolved("established", decision="commit", assertion=c_value("acme"), _environments=envs(["$r1"]))),
        ],
        source=RULINGS + " item 11"))

    out.append(scen(
        "r04-positive-and-denial-of-the-same-value-withdraw-restores", "A positive and a denial of the same value are unresolved with both readings; withdrawing the denial restores the positive",
        "G1", "negative_evidence", ["ruling-4", "design-row-13"],
        "r1 (registry) affirms Acme, r2 (press) denies it: they conflict, an interpretation may label only one TRUE, and either may be wrong "
        "(A-ERR), so the key is unresolved with both readings as candidates (value acme, not_value acme), one environment each, and the policy asks. "
        "The press withdraws its own denial: the positive alone decides (established Acme). The history is kept: at r2's position the key was "
        "unresolved.",
        EMP,
        [
            append("r1", d(2, 1), "alice", "employer", V("acme")),
            append("r2", d(2, 2), "alice", "employer", NV("acme"), source="press"),
            query("q_both", Q("alice", "employer"),
                  resolved("unresolved", decision="ask", _candidates=unordered(c_value("acme"), c_not_value("acme")),
                           _environments=envs(["$r1"], ["$r2"]))),
            withdraw("r3", d(2, 10), "$r2", "alice", "employer", source="press"),
            query("q_after", Q("alice", "employer"), resolved("established", decision="commit", assertion=c_value("acme"))),
            query("q_history", Q("alice", "employer", belief_as_of="$r2.lsn"), resolved("unresolved", _candidates=length(2))),
        ],
        source=RULINGS + " items 11, 12"))
    return out


# a declared grant table REPLACES the profile default, so the default grant (a source's own reports) is restated beside the grant
GRANT = [
    {"who": {"kind": "target_source"}, "may": ["correct", "withdraw", "dispute"], "on": {}, "targets": "report", "over_origins": None},
    {"who": {"kind": "principal", "value": "user:alice"}, "may": ["dispute"], "on": {"attr": "employer", "entity": "*"},
     "targets": "report", "over_origins": None},
]


def r03() -> list[dict[str, Any]]:
    """Ruling 3: an authorised dispute makes the target unresolved against 'disputed'."""
    out: list[dict[str, Any]] = []
    out.append(scen(
        "r03-authorised-dispute-is-unresolved-against-disputed", "An authorised dispute: the target's candidate is unresolved against 'disputed', no value asserted",
        "G1", "authority", ["ruling-3", "S-02", "tb-18"],
        "The registry asserts Acme (r1). A user principal granted the dispute power on employer disputes r1 (r2). Ruling 3: the target's candidate "
        "becomes unresolved against 'disputed', with no value asserted: candidates value(acme) and not_value(acme), one environment each ({r1} and "
        "the dispute {r2}), the policy asks and commits to nothing. The dispute does not quarantine the source and does not withdraw r1.",
        EMP,
        [
            append("r1", d(2, 1), "alice", "employer", V("acme")),
            query("q_before", Q("alice", "employer"), resolved("established", assertion=c_value("acme"))),
            append("r2", d(2, 5), "alice", "employer", None, cue="dispute", target="$r1", source="chat", actor="user:alice",
                   expect={"recorded_cue": "dispute", "admission": {"outcome": "admissible"}}),
            query("q_after", Q("alice", "employer"),
                  resolved("unresolved", decision="ask", assertion=ABSENT,
                           _candidates=unordered(c_value("acme"), c_not_value("acme")), _environments=envs(["$r1"], ["$r2"]))),
            reports("rep_r1", {"id": "$r1"}, {"rows": [{"withdrawn": False}]}),
        ],
        authority=GRANT, source=RULINGS + " item 3"))

    out.append(scen(
        "r03-dispute-overridden-by-confirmation-from-another-origin-group", "Confirmation of the target from another origin group ends the dispute's effect",
        "G1", "authority", ["ruling-3", "S-02"],
        "After the dispute of r1 (unresolved) the press, another origin group (neither the registry's nor the disputer's), reports the same value: "
        "independent corroboration outweighs one dispute, so the key is established Acme again. A report from the target's own origin group, or from the "
        "disputer's, would not count (it is not independent of what it confirms or contradicts).",
        EMP,
        [
            append("r1", d(2, 1), "alice", "employer", V("acme")),
            append("r2", d(2, 5), "alice", "employer", None, cue="dispute", target="$r1", source="chat", actor="user:alice"),
            query("q_disputed", Q("alice", "employer"), resolved("unresolved", assertion=ABSENT)),
            append("r3", d(2, 6), "alice", "employer", V("acme"), source="press"),
            query("q_confirmed", Q("alice", "employer"), resolved("established", decision="commit", assertion=c_value("acme"))),
            query("q_history", Q("alice", "employer", belief_as_of="$r2.lsn"), resolved("unresolved")),
        ],
        authority=GRANT, source=RULINGS + " item 3"))

    out.append(scen(
        "r03-withdrawing-the-dispute-restores-the-target", "Withdrawal of the dispute (by its own source) restores the target",
        "G1", "authority", ["ruling-3", "S-02"],
        "The disputer withdraws its own dispute (the default authority: the target's own source, here the chat source of r2): the dispute no longer acts, "
        "everything it affected is repaired like any withdrawal, and the registry's Acme is established again.",
        EMP,
        [
            append("r1", d(2, 1), "alice", "employer", V("acme")),
            append("r2", d(2, 5), "alice", "employer", None, cue="dispute", target="$r1", source="chat", actor="user:alice"),
            query("q_disputed", Q("alice", "employer"), resolved("unresolved", assertion=ABSENT)),
            withdraw("r3", d(2, 6), "$r2", "alice", "employer", source="chat", actor="user:alice"),
            query("q_restored", Q("alice", "employer"), resolved("established", decision="commit", assertion=c_value("acme"), _environments=envs(["$r1"]))),
        ],
        authority=GRANT, source=RULINGS + " item 3"))

    out.append(scen(
        "r03-dispute-without-a-grant-has-no-kernel-effect", "A dispute that fails the authority check is an allege: the target stands",
        "G1", "authority", ["ruling-3", "S-02"],
        "The same dispute from a principal without the grant (the press, no rule for it): recorded_cue allege, no effect; employer is still established "
        "Acme with environment {r1}.",
        EMP,
        [
            append("r1", d(2, 1), "alice", "employer", V("acme")),
            append("r2", d(2, 5), "alice", "employer", None, cue="dispute", target="$r1", source="press",
                   expect={"recorded_cue": "allege"}),
            query("q1", Q("alice", "employer"), resolved("established", decision="commit", assertion=c_value("acme"), _environments=envs(["$r1"]))),
        ],
        authority=GRANT, source=RULINGS + " item 3"))
    return out


def build() -> list[dict[str, Any]]:
    return [*r01(), *r02(), *r03(), *r04()]


__all__ = ["build"]
