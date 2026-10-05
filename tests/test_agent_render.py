"""Golden snapshots of what a model reads (docs/AGENT_GUIDE.md). A change in wording changes agent behaviour, so the
text of every answer shape is pinned here; update a snapshot only on purpose and say so in the changelog."""

from __future__ import annotations

import re

from palimem import Memory
from palimem.agent import answer_text, explanation_text
from palimem.types import Attr, AttrClass, Schema, ValueType

Q = {"query": {"entity": "alice", "attr": "employer"}}


def schema() -> Schema:
    return Schema(version=1, attrs=(
        Attr(name="employer", attr_class=AttrClass.SINGLE_CHANGEABLE, value_type=ValueType.STRING, inertia=True),
        Attr(name="skills", attr_class=AttrClass.MULTI_SET, value_type=ValueType.STRING, inertia=True),
    ))


def obs(m: Memory, value: str, source: str, group: str | None = None, attr: str = "employer") -> None:
    m.observe({"entity": "alice", "attr": attr, "value": value}, source=source, origin_group=group)


def ids_masked(text: str) -> str:
    return re.sub(r"\b[0-9A-Z]{26}\b", "<id>", text)


def test_unknown_says_do_not_guess() -> None:
    out = Memory(schema=schema()).agent_session("agent:a1").call("recall", Q)
    assert out.text == (
        "alice/employer: UNKNOWN (no admissible evidence). Do not guess; say it is unknown or ask.\n"
        "  decision=abstain; policy=p-default."
    )


def test_established_single_origin_is_marked_uncorroborated() -> None:
    m = Memory(schema=schema())
    obs(m, "Acme", "hr", "g_hr")
    out = m.agent_session("agent:a1").call("recall", Q)
    assert out.text == (
        "alice/employer: ESTABLISHED = 'Acme'.\n"
        "  SINGLE ORIGIN: rests on one origin group (g_hr); uncorroborated. "
        "Corroboration from a second origin group is what raises it.\n"
        "  decision=commit; policy=p-default."
    )
    assert out.data["single_origin"] is True and out.data["kernel_status"] == "established"


def test_established_with_two_origin_groups_is_corroborated() -> None:
    m = Memory(schema=schema())
    obs(m, "Acme", "hr", "g_hr")
    obs(m, "Acme", "press", "g_press")
    out = m.agent_session("agent:a1").call("recall", Q)
    assert out.text == (
        "alice/employer: ESTABLISHED = 'Acme'.\n"
        "  Corroborated by 2 origin groups.\n"
        "  decision=commit; policy=p-default."
    )


def test_unresolved_lists_alternatives_and_does_not_commit() -> None:
    m = Memory(schema=schema())
    obs(m, "Acme", "hr")
    obs(m, "Globex", "press")
    out = m.agent_session("agent:a1").call("recall", Q)
    assert out.text == (
        "alice/employer: UNRESOLVED between 'Acme', 'Globex'.\n"
        "  Evidence does not decide. Ask a source that can, or tell the user it is unsettled.\n"
        "  decision=ask; policy=p-default."
    )


def test_set_valued_established() -> None:
    m = Memory(schema=schema())
    obs(m, "go", "hr", attr="skills")
    out = m.agent_session("agent:a1").call("recall", {"query": {"entity": "alice", "attr": "skills"}})
    assert out.text.splitlines()[0] == "alice/skills: ESTABLISHED = {'go'}."


def test_explain_groups_reports_by_candidate() -> None:
    m = Memory(schema=schema())
    obs(m, "Acme", "hr", "g_hr")
    obs(m, "Globex", "press", "g_press")
    out = m.agent_session("agent:a1").call("explain", {**Q, "mode": "all"})
    assert ids_masked(out.text) == (
        "alice/employer: justification (depth 8):\n"
        "  'Acme':\n"
        "    - <id> from hr (g_hr)\n"
        "  'Globex':\n"
        "    - <id> from press (g_press)"
    )


def test_resource_limited_never_offers_an_old_value_as_current() -> None:
    d = {
        "kind": "resource_limited", "key": {"entity": "alice", "attr": "work_city"}, "reason": "stale_dependency",
        "reason_key": {"entity": "alice", "attr": "employer"}, "last_complete": {"belief_as_of": 3},
    }
    assert answer_text(d) == (
        "alice/work_city: NO ANSWER (resource_limited: stale_dependency).\n"
        "  Blocked on alice/employer. Do not treat any older value as current; retry later.\n"
        "  An older snapshot exists; it is labelled as such and is not the current belief."
    )


def test_write_tool_texts() -> None:
    m = Memory(schema=schema())
    t = m.agent_session("agent:a1")
    rem = t.call("remember", {"entity": "alice", "attr": "employer", "value": "Initech"})
    assert rem.text == (
        "Recorded 1 report(s) as your statement. "
        "This is not evidence: it will not be treated as established or as corroboration."
    )
    assert t.call("retract", {"report_id": "missing"}).text == (
        "retract: not applied (logged only). You may only act on your own reports or where the host granted you authority."
    )
    own = rem.data["report_ids"][0]
    assert t.call("retract", {"report_id": own}).text == "retract: applied."


def test_explanation_text_with_no_supports() -> None:
    d = {"kind": "explanation", "key": {"entity": "a", "attr": "b"}, "depth_applied": 8, "environments": []}
    assert explanation_text(d) == "a/b: no supporting reports in the explained segment (depth 8)."


def test_resolved_json_always_carries_kernel_status_and_decision_together() -> None:
    m = Memory(schema=schema())
    obs(m, "Acme", "hr")
    obs(m, "Globex", "press")
    d = m.agent_session("agent:a1").call("recall", Q).data
    assert {"kernel_status", "decision", "alternatives", "single_origin", "policy", "notices"} <= set(d)
    assert d["assertion"] is None and d["kernel_status"] == "unresolved"  # a policy commitment would stay visible here
