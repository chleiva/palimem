"""The independent acceptance tests of design v0.3 (§Independent acceptance tests), one fixture per row.

Every expected answer below was worked out by hand from the design text and the decision records,
never by running a kernel. The ``why`` field of each fixture states the reasoning so a reviewer can
check the expectation without any code.
"""

from __future__ import annotations

from typing import Any

from .dsl import (
    ABSENT,
    ANY,
    BO,
    EN,
    NM,
    NV,
    A,
    K,
    M,
    Q,
    V,
    append,
    c_belief_of,
    c_empty,
    c_not_member,
    c_not_value,
    c_set,
    c_value,
    contains,
    envs,
    length,
    limited,
    none,
    op,
    query,
    reports,
    resolved,
    unordered,
    withdraw,
)
from .fx_core import d, day, scen

ROW = "design-v0.3 §Independent acceptance tests, row {}"

WORK_CITY = A("derived", vt="entity", reads=["employer", "hq_city"], fn="work_city(e,c) <- employer(e,x), hq_city(x,c)")
BASE = {"employer": A("single_changeable", vt="entity"), "hq_city": A("single_stable", vt="entity")}
CHAIN = {
    **BASE,
    "work_city": WORK_CITY,
    "local_tax_city": A("derived", vt="entity", reads=["work_city"], fn="local_tax_city(e,c) <- work_city(e,c)"),
}
NOT_FOUND = {"kernel_status": "unknown", "_candidates": length(0)}


def build() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    # 1 -----------------------------------------------------------------------------------------
    out.append(scen(
        "ind-01-two-independent-supports-one-withdrawn", "Two independent supports; one is withdrawn, the conclusion survives",
        "G1", "retraction", ["design-row-1", "S-12"],
        "work_city(alice) is derived from employer(alice) and hq_city(veltran). Two independent origin groups (press, "
        "directory) report employer=veltran, so the derived conclusion has two minimal environments over BASE reports: "
        "{r1,r3} and {r2,r3} (derivation pins are not evidence, S-12). Withdrawing r1 by its own source (authority default: "
        "the target's own source) removes only the environment containing r1, so the conclusion survives with exactly {r2,r3}. "
        "The pre-withdrawal answer is still served under belief_as_of = the LSN of r3.",
        {**BASE, "work_city": WORK_CITY},
        [
            append("r1", d(2, 1), "alice", "employer", V("veltran"), source="press"),
            append("r2", d(2, 2), "alice", "employer", V("veltran"), source="directory"),
            append("r3", d(2, 3), "veltran", "hq_city", V("tessaly")),
            query("q1", Q("alice", "work_city"), resolved("established", assertion=c_value("tessaly"),
                                                         _environments=envs(["$r1", "$r3"], ["$r2", "$r3"]))),
            withdraw("r4", d(2, 10), "$r1", "alice", "employer", source="press", expect={"recorded_cue": "withdraw"}),
            query("q2", Q("alice", "work_city"), resolved("established", assertion=c_value("tessaly"),
                                                         _environments=envs(["$r2", "$r3"]),
                                                         justified={"inference": {"complete": True}})),
            query("q3", Q("alice", "work_city", belief_as_of="$r3.lsn"),
                  resolved("established", _environments=envs(["$r1", "$r3"], ["$r2", "$r3"]))),
        ],
        source=ROW.format(1)))

    # 2 -----------------------------------------------------------------------------------------
    out.append(scen(
        "ind-02-last-support-withdrawn-cascades", "Last surviving support withdrawn: every unsupported downstream conclusion disappears",
        "G1", "retraction", ["design-row-2"],
        "employer(alice) has one report r1; work_city(alice) derives from it and hq_city(veltran); local_tax_city(alice) derives from "
        "work_city: a two-step chain. Withdrawing r1 leaves no admissible evidence for employer, so work_city and local_tax_city lose "
        "their only justification and become unknown (no candidates). hq_city(veltran) rests on r2, which is untouched, and stays "
        "established: only UNSUPPORTED conclusions disappear.",
        CHAIN,
        [
            append("r1", d(2, 1), "alice", "employer", V("veltran"), source="press"),
            append("r2", d(2, 2), "veltran", "hq_city", V("tessaly")),
            query("q1", Q("alice", "local_tax_city"), resolved("established", assertion=c_value("tessaly"))),
            withdraw("r3", d(2, 10), "$r1", "alice", "employer", source="press"),
            query("q2", Q("alice", "employer"), NOT_FOUND),
            query("q3", Q("alice", "work_city"), NOT_FOUND),
            query("q4", Q("alice", "local_tax_city"), NOT_FOUND),
            query("q5", Q("veltran", "hq_city"), resolved("established", assertion=c_value("tessaly"), _environments=envs(["$r2"]))),
        ],
        source=ROW.format(2)))

    # 3 -----------------------------------------------------------------------------------------
    out.append(scen(
        "ind-03-unknown-empty-denied-are-distinct", "No evidence, explicit empty set and explicit denial give three distinct answers",
        "G1", "evidence_states", ["design-row-3", "S-04"],
        "Open world (the product default): absence of any report is unknown (alice). An explicit enumeration([]) report on a multi_set key "
        "is explicit negative evidence and yields established_empty (bob). An explicit denial (not_member on a set key, not_value on a "
        "single-valued key) yields established_false and keeps its content as the candidate (carol, dave). Three different statuses; "
        "absence alone never produces established_empty or established_false.",
        {"affiliations": A("multi_set"), "employer": A("single_changeable", vt="entity")},
        [
            append("r1", d(2, 1), "bob", "affiliations", EN()),
            append("r2", d(2, 2), "carol", "affiliations", NM("acme")),
            append("r3", d(2, 3), "dave", "employer", NV("acme")),
            query("q_alice", Q("alice", "affiliations"), NOT_FOUND),
            query("q_bob", Q("bob", "affiliations"), resolved("established_empty", _candidates=contains(c_empty()))),
            query("q_carol", Q("carol", "affiliations"), resolved("established_false", _candidates=contains(c_not_member("acme")))),
            query("q_dave", Q("dave", "employer"), resolved("established_false", _candidates=contains(c_not_value("acme")))),
        ],
        source=ROW.format(3)))

    # 4 -----------------------------------------------------------------------------------------
    out.append(scen(
        "ind-04-retrospective-correction-two-axes", "Retrospective correction: right answers on both time axes, old answer still served",
        "G1", "temporal", ["design-row-4", "S-05"],
        "r1 (recorded 10 Jan) says alice worked for Acme from 2025-01-01. On 10 Mar the same source corrects r1: the employer was Globex "
        "from the same date. A same-source correction withdraws its target (A-SELF) and asserts the corrected value. valid_at 2025-02-01 "
        "is the world axis; belief_as_of is the log axis, given both as a timestamp and as an LSN (S-05). Before the correction (belief "
        "axis 1 Mar, or the LSN of r1): Acme. After it: Globex. The two belief_as_of forms must agree.",
        {"employer": A("single_changeable", vt="entity")},
        [
            append("r1", d(1, 10), "alice", "employer", V("acme"), valid_from=day(1, 1, 2025)),
            append("r2", d(3, 10), "alice", "employer", V("globex"), cue="correct", target="$r1", valid_from=day(1, 1, 2025)),
            query("q_ts_before", Q("alice", "employer", valid_at=day(2, 1, 2025), belief_as_of=day(3, 1)),
                  resolved("established", assertion=c_value("acme"))),
            query("q_lsn_before", Q("alice", "employer", valid_at=day(2, 1, 2025), belief_as_of="$r1.lsn"),
                  resolved("established", assertion=c_value("acme"))),
            query("q_after_ts", Q("alice", "employer", valid_at=day(2, 1, 2025), belief_as_of=day(3, 15)),
                  resolved("established", assertion=c_value("globex"))),
            query("q_now", Q("alice", "employer", valid_at=day(2, 1, 2025)), resolved("established", assertion=c_value("globex"))),
        ],
        source=ROW.format(4)))

    # 5 -----------------------------------------------------------------------------------------
    out.append(scen(
        "ind-05-unauthorised-withdrawal-rejected", "Unauthorised withdrawal is rejected at the authority check; a dispute is logged",
        "G1", "authority", ["design-row-5", "S-02", "S-03"],
        "The registry asserts r1. The press (a different source, actor and origin group) tries to withdraw it. Default authority is the "
        "target's own source (S-02), so the check fails: the report is recorded with cue allege (no effect on admissibility; admission "
        "reason authority_failed). r1 stays effective: employer is still established Acme with environment {r1}, and r1 is not marked "
        "withdrawn. The 'dispute logged' of the design row is the allege row that points at r1.",
        {"employer": A("single_changeable", vt="entity")},
        [
            append("r1", d(2, 1), "alice", "employer", V("acme")),
            withdraw("r2", d(2, 5), "$r1", "alice", "employer", source="press",
                     expect={"recorded_cue": "allege", "admission": {"reason": "authority_failed"}}),
            query("q1", Q("alice", "employer"), resolved("established", assertion=c_value("acme"), _environments=envs(["$r1"]))),
            reports("rep_allege", {"cue": "allege"}, {"count": 1, "rows": [{"target": "$r1", "actor": "connector:press"}]}),
            reports("rep_r1", {"id": "$r1"}, {"rows": [{"withdrawn": False}]}),
        ],
        source=ROW.format(5)))

    # 6 -----------------------------------------------------------------------------------------
    out.append(scen(
        "ind-06-agent-repeats-own-hypothesis", "An agent repeating its own hypothesis is not corroboration",
        "G1", "admission", ["design-row-6", "S-03"],
        "r1 comes from a quarantined source (admissible only on confirmation by an already admissible report of a DIFFERENT origin group). "
        "The agent then states the same value twice as agent_hypothesis reports sharing one origin group. Agent-origin reports are never "
        "admissible and never confirm: both are excluded (reason origin_not_admissible) and r1 stays quarantined. With nothing admissible "
        "the key is unknown.",
        {"employer": A("single_changeable", vt="entity")},
        [
            append("r1", d(2, 1), "alice", "employer", V("acme"), source="q1",
                   expect={"admission": {"outcome": "quarantined", "reason": "source_quarantined"}}),
            append("r2", d(2, 2), "alice", "employer", V("acme"), source="agent_a1", origin="agent_hypothesis", actor="agent:a1",
                   expect={"admission": {"outcome": "excluded", "reason": "origin_not_admissible"}}),
            append("r3", d(2, 3), "alice", "employer", V("acme"), source="agent_a1", origin="agent_hypothesis", actor="agent:a1",
                   expect={"admission": {"outcome": "excluded", "reason": "origin_not_admissible"}}),
            reports("rep_r1", {"id": "$r1"}, {"rows": [{"admission": {"outcome": "quarantined"}}]}),
            query("q1", Q("alice", "employer"), NOT_FOUND),
        ],
        source=ROW.format(6)))

    # 7 -----------------------------------------------------------------------------------------
    out.append(scen(
        "ind-07-crash-between-append-and-index", "Crash between append and index update, then retry: no duplicate, no partial revision",
        "G1", "crash_recovery", ["design-row-7", "E1.4"],
        "The process dies in the middle of the append transaction. The write is transactional (report, admission record, belief versions, "
        "generation and index become visible together or not at all), so after recovery there is no report and no belief. A retry with "
        "the same idempotency key then lands exactly once; a second retry with the same key is a duplicate and returns the same report id.",
        {"employer": A("single_changeable", vt="entity")},
        [
            append("r1x", d(2, 1), "alice", "employer", V("acme"), idem="k1", crash="mid_transaction", expect={"crashed": True}),
            op("recover", name="rec", expect={"recovered": True}),
            reports("rep_none", {}, {"count": 0}),
            query("q_none", Q("alice", "employer"), NOT_FOUND),
            append("r1", d(2, 2), "alice", "employer", V("acme"), idem="k1", expect={"duplicate": False, "admission": {"outcome": "admissible"}}),
            append("r1b", d(2, 2), "alice", "employer", V("acme"), idem="k1", expect={"duplicate": True, "report_id": "$eq:$r1"}),
            reports("rep_one", {}, {"count": 1}),
            query("q_one", Q("alice", "employer"), resolved("established", assertion=c_value("acme"), _environments=envs(["$r1"]))),
        ],
        requires=["crash_injection"], source=ROW.format(7)))

    out.append(scen(
        "ind-07b-crash-after-commit-before-ack", "Extension: crash after commit but before the acknowledgement; the retry is a duplicate",
        "G1", "crash_recovery", ["design-row-7", "E1.4"],
        "Variant of row 7 where the crash happens after the transaction committed but before the caller got its acknowledgement. After "
        "recovery the report exists exactly once; the caller's retry with the same idempotency key is reported as a duplicate and returns "
        "the id of the committed report.",
        {"employer": A("single_changeable", vt="entity")},
        [
            append("r1x", d(2, 1), "alice", "employer", V("acme"), idem="k1", crash="after_commit_before_ack", expect={"crashed": True}),
            op("recover", name="rec", expect={"recovered": True}),
            reports("rep_one", {}, {"count": 1}),
            append("r1", d(2, 2), "alice", "employer", V("acme"), idem="k1",
                   expect={"duplicate": True, "report_id": "$eq:$rep_one.rows.0.id"}),
            reports("rep_still_one", {}, {"count": 1}),
            query("q1", Q("alice", "employer"), resolved("established", assertion=c_value("acme"))),
        ],
        requires=["crash_injection"], source=ROW.format(7) + " (extension)"))

    # 8 -----------------------------------------------------------------------------------------
    srcs5 = {f"s{i}": {"class": "standard", "origin_group": f"g_s{i}"} for i in range(1, 6)}
    ops8: list[dict[str, Any]] = [op("configure", limits={"environment_budget": 4})]
    for i in range(1, 5):
        ops8.append(append(f"r{i}", d(2, i), "alice", "employer", V("acme"), source=f"s{i}"))
    ops8.append(query("q_at_budget", Q("alice", "employer"), resolved("established", assertion=c_value("acme"))))
    ops8.append(append("r5", d(2, 5), "alice", "employer", V("acme"), source="s5"))
    ops8.append(query("q_over", Q("alice", "employer"), limited("environment_budget", reason_key=K("alice", "employer"))))
    ops8.append(withdraw("r6", d(2, 6), "$r5", "alice", "employer", source="s5"))
    ops8.append(query("q_back", Q("alice", "employer"), resolved("established", assertion=c_value("acme"))))
    out.append(scen(
        "ind-08a-environment-budget-resource-limited", "Inference budget exceeded: ResourceLimited with no kernel_status, never unresolved",
        "G1", "resource_limits", ["design-row-8", "S-06"],
        "The environment budget is set to 4. Four independent origin groups report the same value: four minimal environments, at the "
        "budget, so the answer is still Resolved (established). A fifth independent report exceeds the budget: the answer is "
        "ResourceLimited(environment_budget) naming the key, and carries NO kernel_status, no segment and no assertion; in particular it "
        "is not 'unresolved' (S-06). Withdrawing the fifth report brings the key back within budget and it resolves again. Distinct "
        "origin groups are used so that no equivalence collapsing can legitimately reduce the count.",
        {"employer": A("single_changeable", vt="entity")}, ops8, requires=["budget_control"], sources=srcs5,
        source=ROW.format(8) + " (inference budget)"))

    srcs3 = {f"s{i}": {"class": "standard", "origin_group": f"g_s{i}"} for i in range(1, 4)}
    out.append(scen(
        "ind-08b-explanation-budget-truncates", "Explanation budget exceeded: truncated, decision unchanged, justified view and provenance both cut",
        "G1", "provenance", ["design-row-8", "S-12"],
        "Three independent origin groups support the same value, giving three minimal environments. A query with explanation_budget=1 "
        "cuts BOTH the provenance and the embedded justified view to one environment and marks explanation=truncated; kernel_status, "
        "decision and assertion are exactly those of the unbudgeted query. The full explanation stays available by reference.",
        {"employer": A("single_changeable", vt="entity")},
        [
            append("r1", d(2, 1), "alice", "employer", V("acme"), source="s1"),
            append("r2", d(2, 2), "alice", "employer", V("acme"), source="s2"),
            append("r3", d(2, 3), "alice", "employer", V("acme"), source="s3"),
            query("q_full", Q("alice", "employer"),
                  resolved("established", explanation="complete", _environments=envs(["$r1"], ["$r2"], ["$r3"]))),
            query("q_cut", Q("alice", "employer", budget=1),
                  resolved("established", explanation="truncated", provenance=length(1), _support_max_len={"$lte": 1})),
        ],
        sources=srcs3, source=ROW.format(8) + " (explanation budget)"))
    out[-1]["expect"] = {"equal": [{"a": "q_full", "b": "q_cut", "fields": ["kernel_status", "decision", "assertion"]}]}

    # 9 -----------------------------------------------------------------------------------------
    out.append(scen(
        "ind-09-mistaken-merge-reversed", "Mistaken entity merge reversed: exactly the beliefs pinned to the merge are recomputed",
        "G2", "entity_resolution", ["design-row-9", "E2.1"],
        "Two different people were merged by mistake: 'alice smith' (Acme) and 'a. smith' (Globex). While merged the canonical entity has "
        "two competing employers (unresolved). Reversing the merge restores both separate beliefs. Bob's belief is not pinned to the "
        "merge, so it must not be recomputed: its belief version is unchanged across the merge and the reversal, and he never appears "
        "among the recomputed keys. A merge is an admission-stage decision performed by a host-level principal (system:admin).",
        {"employer": A("single_changeable", vt="entity")},
        [
            append("r1", d(2, 1), "alice smith", "employer", V("acme")),
            append("r2", d(2, 2), "a. smith", "employer", V("globex")),
            append("r3", d(2, 3), "bob", "employer", V("initech")),
            query("q_bob_before", Q("bob", "employer"), resolved("established", assertion=c_value("initech"))),
            op("merge", merge_id="m1", entities=["alice smith", "a. smith"], canonical="alice smith", actor="system:admin", at=d(2, 10),
               name="mg", expect={"recomputed_keys": {"$none": {"entity": "bob"}}}),
            query("q_merged", Q("alice smith", "employer"), resolved("unresolved", _candidates=unordered(c_value("acme"), c_value("globex")))),
            query("q_alias", Q("a. smith", "employer"), resolved("unresolved")),
            op("unmerge", merge_id="m1", actor="system:admin", at=d(2, 11), name="unmg", expect={"recomputed_keys": {"$none": {"entity": "bob"}}}),
            query("q_alice_after", Q("alice smith", "employer"), resolved("established", assertion=c_value("acme"))),
            query("q_asmith_after", Q("a. smith", "employer"), resolved("established", assertion=c_value("globex"))),
            query("q_bob_after", Q("bob", "employer"), resolved("established", assertion=c_value("initech"))),
        ],
        requires=["merge"], deps=["merge authority is not a Power of AuthorityRule in palimem.types (correct/withdraw/dispute only); SEC-18/19 need it"],
        source=ROW.format(9)))
    out[-1]["expect"] = {"equal": [{"a": "q_bob_before", "b": "q_bob_after", "fields": ["justified.version"]}]}

    # 10 ----------------------------------------------------------------------------------------
    out.append(scen(
        "ind-10-deletion-with-dependants", "Deletion of a report with dependants: content gone, tombstone present, dependants repaired",
        "G1", "deletion", ["design-row-10", "S-13"],
        "Deletion (an erasure obligation) removes the report's content and raw reference but, unlike withdrawal, also from the log's "
        "readable content; it triggers the same dependency repair as a withdrawal. After deleting r1, the stored row has no proposition "
        "and no raw_ref, a tombstone (id, actor, reason) exists, work_city that depended on r1 is repaired to unknown, and the delete "
        "returns an erasure report listing what can no longer be reconstructed.",
        {**BASE, "work_city": WORK_CITY},
        [
            append("r1", d(2, 1), "alice", "employer", V("veltran"), source="press", raw_ref="blob:r1"),
            append("r2", d(2, 2), "veltran", "hq_city", V("tessaly")),
            query("q_before", Q("alice", "work_city"), resolved("established", assertion=c_value("tessaly"))),
            op("delete", target="$r1", actor="system:admin", reason="erasure_request", at=d(2, 10), name="del",
               expect={"tombstone": {"id": "$r1", "actor": "system:admin", "reason": "erasure_request"}, "erasure_report": ANY}),
            reports("rep_r1", {"id": "$r1"}, {"rows": [{"tombstone": True, "proposition": ABSENT, "raw_ref": ABSENT}]}),
            query("q_work", Q("alice", "work_city"), NOT_FOUND),
            query("q_emp", Q("alice", "employer"), NOT_FOUND),
        ],
        requires=["delete"], source=ROW.format(10)))

    # 11 ----------------------------------------------------------------------------------------
    out.append(scen(
        "ind-11-disjoint-intervals-one-withdrawn", "Same candidate supported in disjoint intervals; withdrawing R1 affects only January",
        "G1", "segments", ["design-row-11", "S-12"],
        "location has no inertia. R1 (registry) says Paris for January; R2 (press) says Paris for 10 Feb to 1 Mar. The two intervals are "
        "disjoint, so each segment's environment names only its own report: January {R1}, February {R2}. Withdrawing R1 makes January "
        "unknown (no admissible evidence bears on that segment) and leaves February with R2 untouched. R2 is never listed as support for "
        "January, before or after: a February justification is not replacement support for January.",
        {"location": A("single_changeable", inertia=False)},
        [
            append("r1", d(2, 1), "alice", "location", V("paris"), valid_from=day(1, 1), valid_to=day(2, 1)),
            append("r2", d(3, 1), "alice", "location", V("paris"), source="press", valid_from=day(2, 10), valid_to=day(3, 1)),
            query("q_jan", Q("alice", "location", valid_at=day(1, 15)), resolved("established", assertion=c_value("paris"), _environments=envs(["$r1"]))),
            query("q_feb", Q("alice", "location", valid_at=day(2, 15)), resolved("established", assertion=c_value("paris"), _environments=envs(["$r2"]))),
            withdraw("r3", d(3, 10), "$r1", "alice", "location"),
            query("q_jan2", Q("alice", "location", valid_at=day(1, 15)), {"kernel_status": "unknown", "_environments": length(0)}),
            query("q_feb2", Q("alice", "location", valid_at=day(2, 15)),
                  resolved("established", assertion=c_value("paris"), _environments=envs(["$r2"]),
                           segment={"valid_from": day(2, 10), "valid_to": day(3, 1)})),
        ],
        source=ROW.format(11)))

    # 12 ----------------------------------------------------------------------------------------
    out.append(scen(
        "ind-12-overlapping-intervals-one-closed", "Conflicting candidates in overlapping intervals; one later closed: segments split at the boundaries",
        "G1", "segments", ["design-row-12", "S-04"],
        "employer has no inertia. R1 (registry): Acme from 1 Jan, open-ended. R2 (press): Globex from 1 Mar, open-ended. They overlap from 1 Mar. "
        "Before R3: segment [1 Jan,1 Mar) = Acme established, segment [1 Mar,end) = unresolved with both candidates. On 10 Apr the registry "
        "corrects R1 to close it at 1 Apr (R3: same value, valid 1 Jan to 1 Apr; a same-source correction withdraws R1). After R3 the "
        "segments are [1 Jan,1 Mar) Acme, [1 Mar,1 Apr) unresolved {Acme,Globex}, [1 Apr,end) Globex established, each with its own status.",
        {"employer": A("single_changeable", vt="entity", inertia=False)},
        [
            append("r1", d(2, 1), "alice", "employer", V("acme"), valid_from=day(1, 1)),
            append("r2", d(3, 5), "alice", "employer", V("globex"), source="press", valid_from=day(3, 1)),
            query("q_feb_before", Q("alice", "employer", valid_at=day(2, 1)), resolved("established", assertion=c_value("acme"))),
            query("q_mar_before", Q("alice", "employer", valid_at=day(3, 15)),
                  resolved("unresolved", _candidates=unordered(c_value("acme"), c_value("globex")), segment={"valid_from": day(3, 1)})),
            append("r3", d(4, 10), "alice", "employer", V("acme"), cue="correct", target="$r1", valid_from=day(1, 1), valid_to=day(4, 1)),
            query("q_feb", Q("alice", "employer", valid_at=day(2, 1)),
                  resolved("established", assertion=c_value("acme"), segment={"valid_from": day(1, 1), "valid_to": day(3, 1)})),
            query("q_mar", Q("alice", "employer", valid_at=day(3, 15)),
                  resolved("unresolved", _candidates=unordered(c_value("acme"), c_value("globex")),
                           segment={"valid_from": day(3, 1), "valid_to": day(4, 1)})),
            query("q_may", Q("alice", "employer", valid_at=day(5, 1)),
                  resolved("established", assertion=c_value("globex"), segment={"valid_from": day(4, 1)})),
            query("q_mar_asof", Q("alice", "employer", valid_at=day(3, 15), belief_as_of="$r2.lsn"),
                  resolved("unresolved", segment={"valid_from": day(3, 1)})),
        ],
        deps=[("Segment-local status assumes admissibility (A-ERR) is evaluated per segment, as design v0.3 'each segment reports its own "
              "status' implies. The paper's per-key kernel lets any report be ERR when disputed anywhere, which would make the non-overlap "
              "segments unresolved too. Needs a decision before G1.")],
        source=ROW.format(12)))

    # 13 ----------------------------------------------------------------------------------------
    sets = {"affiliations": A("multi_set")}
    for suffix, who, surviving, ids in (("a", "r1", "negative", c_not_member("acme")), ("b", "r2", "positive", c_set("acme"))):
        tgt = "$r1" if who == "r1" else "$r2"
        src_ = "registry" if who == "r1" else "press"
        out.append(scen(
            f"ind-13{suffix}-positive-negative-withdraw-{'positive' if who == 'r1' else 'negative'}",
            f"Positive and negative reports for one member; withdraw the {'positive' if who == 'r1' else 'negative'} one: the surviving report alone decides",
            "G1", "negative_evidence", ["design-row-13", "S-04"],
            "r1 (registry) says Acme is a member of alice's affiliations; r2 (press) says it is not. Admissible positive and negative evidence "
            "for the same member in overlapping valid time is unresolved with BOTH readings as candidates, never established_false: a denial is "
            "evidence against, not a veto. Withdrawing one of them (by its own source) leaves the other alone to decide: "
            + ("established_false carrying not_member(acme)." if surviving == "negative" else "established with member acme."),
            sets,
            [
                append("r1", d(2, 1), "alice", "affiliations", M("acme")),
                append("r2", d(2, 2), "alice", "affiliations", NM("acme"), source="press"),
                query("q_both", Q("alice", "affiliations"),
                      resolved("unresolved", _candidates=unordered(c_set("acme"), c_not_member("acme")))),
                withdraw("r3", d(2, 10), tgt, "alice", "affiliations", source=src_),
                query("q_after", Q("alice", "affiliations"),
                      resolved("established_false" if surviving == "negative" else "established", _candidates=length(1))),
            ],
            source=ROW.format(13),
            deps=[("Whether member-only evidence under open completeness yields a determined set (established) or a weaker status is not "
                  "settled by S-04; this fixture follows the design row ('the surviving one alone decides').")] if surviving == "positive" else []))
        out[-1]["ops"][-1]["expect"]["_candidates"] = contains(ids)

    # 14 ----------------------------------------------------------------------------------------
    out.append(scen(
        "ind-14-cascade-budget-stale-c", "Revision budget exhausted on A to B to C before C is visited: C is resource_limited until its job runs",
        "G1", "resource_limits", ["design-row-14", "S-06"],
        "Chain A=employer, B=work_city, C=local_tax_city. The per-append revision budget is 2 keys. Withdrawing the only employer report "
        "recomputes A and B but the budget is exhausted before C is visited, so C is stale (completed_generation < required_generation). "
        "Reads of C return ResourceLimited(stale_dependency) naming C, with no kernel_status, no segment and no assertion: the old C "
        "(Tessaly) is never served as current. A and B are resolved (unknown). After the completion job runs, C resolves to unknown. NOTE: "
        "the design says 'visited' for both marking and recomputation; this fixture takes it as the per-append recomputation budget "
        "('revision_budget') and keeps the marking (traversal) budget for row 20.",
        CHAIN,
        [
            append("r1", d(2, 1), "alice", "employer", V("veltran"), source="press"),
            append("r2", d(2, 2), "veltran", "hq_city", V("tessaly")),
            query("q_c0", Q("alice", "local_tax_city"), resolved("established", assertion=c_value("tessaly"))),
            op("configure", limits={"revision_budget": 2}),
            withdraw("r3", d(2, 10), "$r1", "alice", "employer", source="press"),
            query("q_a", Q("alice", "employer"), NOT_FOUND),
            query("q_b", Q("alice", "work_city"), NOT_FOUND),
            query("q_c", Q("alice", "local_tax_city"), limited("stale_dependency", reason_key=K("alice", "local_tax_city"))),
            op("complete_jobs", name="jobs", expect={"completed": contains(K("alice", "local_tax_city"))}),
            query("q_c_done", Q("alice", "local_tax_city"), NOT_FOUND),
        ],
        requires=["budget_control", "completion_jobs"],
        deps=["Budget names (revision_budget, traversal_budget) are fixture vocabulary: the design does not name its budgets."],
        source=ROW.format(14)))

    # 15 ----------------------------------------------------------------------------------------
    out.append(scen(
        "ind-15-attributed-twice-content-unknown", "Two independent reports that Alice believes P: belief_of(Alice,P) established; P stays unknown",
        "G1", "attribution", ["design-row-15", "S-11"],
        "Two attributed reports from different origin groups say that alice believes bob works for acme. Attributed reports are admissible "
        "as evidence for the attribution only, however many groups corroborate: belief_of(alice, value(acme)) is established (two "
        "minimal environments {r1} and {r2}); the content, bob's employer = acme, is never asserted and no value(acme) candidate exists.",
        {"employer": A("single_changeable", vt="entity")},
        [
            append("r1", d(2, 1), "bob", "employer", BO("alice", V("acme")), origin="attributed", source="press"),
            append("r2", d(2, 2), "bob", "employer", BO("alice", V("acme")), origin="attributed", source="directory"),
            query("q1", Q("bob", "employer"),
                  {"_candidates": contains(c_belief_of("alice", V("acme"))), "_environments": envs(["$r1"], ["$r2"])}),
            query("q2", Q("bob", "employer"), {"_candidates": none(c_value("acme"))}),
        ],
        deps=[("Query has no selector for 'the attribution' versus 'the content'; this fixture reads both from the candidate list of the "
              "one key. S-11 should say how a caller asks for each.")],
        source=ROW.format(15)))

    # 16 ----------------------------------------------------------------------------------------
    out.append(scen(
        "ind-16-quarantined-agree-third-confirms", "Two quarantined sources agree: neither is admitted; an admissible third origin group confirms both",
        "G1", "admission", ["design-row-16", "S-01"],
        "q1 and q2 are quarantined sources in different origin groups and both report employer=acme. Confirmation requires evidence the "
        "system already trusts, so they cannot confirm each other: both stay quarantined and the key is unknown. The registry (trusted, "
        "third origin group) then reports the same value: that report is admissible and confirms both, so both become admissible "
        "(reason confirmed, confirmed_by r3). Because their admission depends on r3, the only subset-minimal environment for acme is {r3}.",
        {"employer": A("single_changeable", vt="entity")},
        [
            append("r1", d(2, 1), "alice", "employer", V("acme"), source="q1", expect={"admission": {"outcome": "quarantined"}}),
            append("r2", d(2, 2), "alice", "employer", V("acme"), source="q2", expect={"admission": {"outcome": "quarantined"}}),
            query("q_none", Q("alice", "employer"), NOT_FOUND),
            append("r3", d(2, 3), "alice", "employer", V("acme")),
            reports("rep_conf", {"id": "$r1"}, {"rows": [{"admission": {"outcome": "admissible", "reason": "confirmed", "confirmed_by": "$r3"}}]}),
            reports("rep_conf2", {"id": "$r2"}, {"rows": [{"admission": {"outcome": "admissible", "reason": "confirmed", "confirmed_by": "$r3"}}]}),
            query("q_est", Q("alice", "employer"), resolved("established", assertion=c_value("acme"), _environments=envs(["$r3"]))),
        ],
        source=ROW.format(16)))

    # 17 ----------------------------------------------------------------------------------------
    out.append(scen(
        "ind-17-outbox-redelivery-after-crash", "Crash after a belief change commits, before the subscriber is notified: re-delivered with the same event id",
        "G2", "outbox", ["design-row-17", "E2.5"],
        "A plan subscribes to employer(alice). A correction changes the belief and the process dies after the revision transaction committed "
        "(the outbox row is part of that transaction) but before the notification was sent. After recovery the event is delivered from the "
        "outbox. The subscriber then crashes before acknowledging, so a second delivery carries the SAME event_id (at least once). The runner's "
        "reference subscriber is idempotent on event_id: it saw two deliveries but applied one effect. After the ack nothing is redelivered.",
        {"employer": A("single_changeable", vt="entity")},
        [
            append("r1", d(2, 1), "alice", "employer", V("acme")),
            op("subscribe", plan_id="p1", keys=[K("alice", "employer")], name="sub"),
            append("r2", d(2, 10), "alice", "employer", V("globex"), cue="correct", target="$r1", crash="after_commit_before_notify",
                   expect={"crashed": True}),
            op("recover", name="rec", expect={"recovered": True}),
            op("deliver", name="d1", expect={"events": [{"plan_id": "p1", "event_id": ANY, "key": K("alice", "employer")}]}),
            op("crash", point="after_delivery_before_ack", name="crash2"),
            op("recover", name="rec2"),
            op("deliver", name="d2", expect={"events": [{"plan_id": "p1", "event_id": "$eq:$d1.events.0.event_id"}]}),
            op("ack", **{"from": "d2"}, name="ack1"),
            op("deliver", name="d3", expect={"events": length(0)}),
            op("subscriber_effects", name="fx", expect={"p1": {"events_seen": 2, "effects_applied": 1}}),
        ],
        requires=["crash_injection", "outbox"], source=ROW.format(17)))

    # 18 ----------------------------------------------------------------------------------------
    out.append(scen(
        "ind-18-two-not-values-distinct", "'Employer is not Acme' and 'employer is not Globex' survive as two distinct not_value candidates",
        "G1", "negative_evidence", ["design-row-18", "S-04"],
        "Two denials about different values are compatible evidence (the employer is neither). Candidates are identified by their content, "
        "so not_value(acme) and not_value(globex) are two different candidates, each carrying its value; neither collapses to a bare "
        "'false', and no positive value candidate appears. AUTHOR RULING of 2026-10-05 (docs/decisions/RULINGS-2026-10-05.md item 11) fixes "
        "the status the design left open: two compatible not_value candidates give kernel_status unknown, with both negatives listed as "
        "constraints in alternatives (the value is narrowed, not determined; a single established candidate cannot carry two denials); "
        "established_false only when completeness makes them exhaustive.",
        {"employer": A("single_changeable", vt="entity")},
        [
            append("r1", d(2, 1), "alice", "employer", NV("acme")),
            append("r2", d(2, 2), "alice", "employer", NV("globex"), source="press"),
            query("q1", Q("alice", "employer"),
                  {"kernel_status": "unknown", "assertion": ABSENT,
                   "_candidates": unordered(c_not_value("acme"), c_not_value("globex"))}),
            query("q2", Q("alice", "employer"), {"_candidates": none({"form": {"form": "value"}})}),
        ],
        source=ROW.format(18)))

    # 19 ----------------------------------------------------------------------------------------
    att = {"employer": A("single_changeable", vt="entity")}
    out.append(scen(
        "ind-19a-attributed-round-trip-typed", "Attributed claim round trip (typed input): belief_of(holder, P) keeps holder and P",
        "G1", "attribution", ["design-row-19", "S-11"],
        "A typed attributed report belief_of(bob, value(acme)) on key (alice, employer) is stored exactly as given (holder and inner "
        "proposition intact) and comes back through query and answer as a belief_of candidate with the same holder and proposition.",
        att,
        [
            append("r1", d(2, 1), "alice", "employer", BO("bob", V("acme")), origin="attributed", source="chat"),
            reports("rep", {"id": "$r1"}, {"rows": [{"proposition": BO("bob", V("acme")), "origin": "attributed"}]}),
            query("q1", Q("alice", "employer"), {"_candidates": contains(c_belief_of("bob", V("acme")))}),
        ],
        source=ROW.format(19) + " (storage, query, answer)"))

    out.append(scen(
        "ind-19b-attributed-round-trip-extracted", "Attributed claim round trip through extraction",
        "G2", "extraction", ["design-row-19", "S-11"],
        "Same as 19a but the typed report is produced by an extractor from text ('Bob told me Alice works at Acme'). The extractor is a "
        "deterministic stub so the fixture tests the contract, not a model. Holder and proposition must survive extraction, storage, query "
        "and answer, and the report carries the extractor stamp.",
        att,
        [
            op("observe_text", ref="r1", name="obs", connector="chat", at=d(2, 1), text="Bob told me that Alice works at Acme.",
               extractor_stub=[{
                   "key": K("alice", "employer"), "proposition": BO("bob", V("acme")), "cue": "assert", "target": None,
                   "source": {"id": "chat", "class": "standard"}, "origin": "attributed", "origin_group": "g_chat",
                   "actor": "connector:chat", "valid_from": None, "valid_to": None, "precision": "day",
                   "extractor": {"model": "stub", "version": "1", "prompt_hash": "sha256:0000"}}],
               expect={"reports": [{"report_id": ANY}]}),
            reports("rep", {"id": "$r1"}, {"rows": [{"proposition": BO("bob", V("acme")), "extractor": {"model": "stub"}}]}),
            query("q1", Q("alice", "employer"), {"_candidates": contains(c_belief_of("bob", V("acme")))}),
        ],
        requires=["extractor"], source=ROW.format(19) + " (extraction)"))

    # 20 ----------------------------------------------------------------------------------------
    out.append(scen(
        "ind-20-traversal-budget-store-dirty", "Traversal budget exhausted mid-cascade: store-wide dirty marker, every read ResourceLimited(store_dirty)",
        "G1", "resource_limits", ["design-row-20", "S-06", "H4"],
        "The per-append traversal (marking) budget is 1 key, but the dependency closure of employer(alice) has two derived keys (work_city, "
        "local_tax_city). The commit cannot mark the closure, so it sets the store-wide dirty marker instead of a partial marking: EVERY read, "
        "including a key in an unrelated component (site(bob)), returns ResourceLimited(store_dirty) until the completion job clears it. "
        "This is the design as written; the threat model's proposal to scope the marker to a component (SEC-22, H4) contradicts it and is "
        "tracked as a pending decision.",
        {**CHAIN, "site": A("single_stable")},
        [
            append("r1", d(2, 1), "alice", "employer", V("veltran"), source="press"),
            append("r2", d(2, 2), "veltran", "hq_city", V("tessaly")),
            append("r3", d(2, 3), "bob", "site", V("north")),
            op("configure", limits={"traversal_budget": 1}),
            withdraw("r4", d(2, 10), "$r1", "alice", "employer", source="press"),
            query("q_touched", Q("alice", "employer"), limited("store_dirty")),
            query("q_dependant", Q("alice", "local_tax_city"), limited("store_dirty")),
            query("q_unrelated", Q("bob", "site"), limited("store_dirty")),
            op("complete_jobs", name="jobs"),
            query("q_touched2", Q("alice", "employer"), NOT_FOUND),
            query("q_unrelated2", Q("bob", "site"), resolved("established", assertion=c_value("north"))),
        ],
        requires=["budget_control", "completion_jobs"],
        deps=["H4: component-scoped dirty marker (SEC-22) vs the store-wide marker of design row 20 is undecided."],
        source=ROW.format(20)))

    # 21 ----------------------------------------------------------------------------------------
    out.append(scen(
        "ind-21-completion-job-does-not-overwrite-newer", "A completion job for generation g that runs after g+1 completed the same key does not overwrite it",
        "G1", "resource_limits", ["design-row-21", "S-06"],
        "Two derived keys read employer(alice): work_city (also reads hq_city) and badge_site (also reads site). At generation g a correction "
        "changes the employer to globex with a revision budget of 1, so both derived keys are marked but incomplete. At generation g+1 "
        "(budget raised) a new hq_city(globex)=paris report recomputes work_city completely: paris, completed for g+1. badge_site is still "
        "stale at g. Running the job for g finishes badge_site (now unknown: no site for globex) and must NOT touch work_city: it would "
        "overwrite paris with the g-state answer (unknown). work_city keeps its version and stays paris.",
        {**BASE, "site": A("single_stable"),
         "work_city": WORK_CITY,
         "badge_site": A("derived", reads=["employer", "site"], fn="badge_site(e,s) <- employer(e,x), site(x,s)")},
        [
            append("r1", d(2, 1), "alice", "employer", V("veltran"), source="press"),
            append("r2", d(2, 2), "veltran", "hq_city", V("tessaly")),
            append("r3", d(2, 3), "veltran", "site", V("north")),
            query("q_b0", Q("alice", "work_city"), resolved("established", assertion=c_value("tessaly"))),
            op("configure", limits={"revision_budget": 1}),
            append("r4", d(2, 10), "alice", "employer", V("globex"), cue="correct", target="$r1", source="press"),
            op("configure", limits={"revision_budget": 100}),
            append("r5", d(2, 11), "globex", "hq_city", V("paris")),
            query("q_b1", Q("alice", "work_city"),
                  resolved("established", assertion=c_value("paris"), justified={"completed_generation": "$r5.generation"})),
            query("q_c1", Q("alice", "badge_site"), limited("stale_dependency", reason_key=K("alice", "badge_site"))),
            op("complete_jobs", generation="$r4.generation", name="job_g",
               expect={"completed": contains(K("alice", "badge_site")), "skipped": contains(K("alice", "work_city"))}),
            query("q_b2", Q("alice", "work_city"),
                  resolved("established", assertion=c_value("paris"), justified={"completed_generation": "$r5.generation"})),
            query("q_c2", Q("alice", "badge_site"), NOT_FOUND),
        ],
        requires=["budget_control", "completion_jobs"], source=ROW.format(21)))
    out[-1]["expect"] = {"equal": [{"a": "q_b1", "b": "q_b2", "fields": ["justified.version", "justified.completed_generation", "assertion"]}]}

    # 22 ----------------------------------------------------------------------------------------
    out.append(scen(
        "ind-22-belief-as-of-into-stale-snapshot", "belief_as_of into a snapshot that was stale at that time: ResourceLimited, not the later completed version",
        "G1", "resource_limits", ["design-row-22", "S-05"],
        "Same stale-C set-up as row 14. After the completion job C is resolved at 'now'. A historical query with belief_as_of = the LSN of "
        "the append that left C stale must apply the barrier of THAT snapshot: ResourceLimited(stale_dependency) for C, not the later "
        "completed (unknown) version and not a Resolved answer.",
        CHAIN,
        [
            append("r1", d(2, 1), "alice", "employer", V("veltran"), source="press"),
            append("r2", d(2, 2), "veltran", "hq_city", V("tessaly")),
            op("configure", limits={"revision_budget": 2}),
            withdraw("r3", d(2, 10), "$r1", "alice", "employer", source="press"),
            op("complete_jobs", name="jobs"),
            query("q_now", Q("alice", "local_tax_city"), NOT_FOUND),
            query("q_then", Q("alice", "local_tax_city", belief_as_of="$r3.lsn"), limited("stale_dependency")),
        ],
        requires=["budget_control", "completion_jobs"], source=ROW.format(22)))
    return out
