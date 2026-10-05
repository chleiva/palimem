"""Attribution safety (design v0.3 rows 15 and 19; S-11; RETRACT-ACT RA-018).

An attributed claim ``belief_of(holder, P)`` establishes the attribution and never ``P``. The answer to a query on a
key that has only attributed evidence therefore keeps ``kernel_status`` and the ``belief_of`` candidates (the design's
own rows and the conformance fixtures read them there), but:

* the **decision** is never ``commit`` and there is no ``assertion`` (a consumer reading only ``decision`` / ``assertion``
  can never be handed an attribution as if it were the value): the policy **asks**;
* the attributions are available through a separate host-level call, :meth:`palimem.memory.Memory.attributions`;
* the agent tool API marks the answer ``attribution_only``, says the content is unknown, and lists the attributions apart.

Every pipeline test runs on both backends.
"""

from __future__ import annotations

import pytest

from palimem import Memory as Facade
from palimem.memory import AttributedClaim, Memory
from palimem.policy import JUSTIFIED, LWW, RECENCY
from palimem.types import (
    Attr,
    AttrClass,
    BeliefOfForm,
    BeliefOfProp,
    Cue,
    Decision,
    KernelStatus,
    Key,
    Origin,
    Report,
    Resolved,
    RuleFired,
    Schema,
    ValueProp,
    ValueType,
)
from tests._pipeline_helpers import (
    Clock,
    assertion,
    current,
    make_backend,
    src,
    toy_memory,
)

BACKENDS = ["memory", "sqlite"]


@pytest.fixture(params=BACKENDS)
def mem(request: pytest.FixtureRequest) -> Memory:
    return toy_memory(make_backend(request.param, Clock()))


def attributed(source: str, group: str, *, holder: str = "alice", value: str = "acme") -> Report:
    return Report(
        key=Key(entity="bob", attr="employer"), cue=Cue.ASSERT,
        proposition=BeliefOfProp(holder=holder, proposition=ValueProp(value=value)), source=src(source),
        origin=Origin.ATTRIBUTED, origin_group=group, actor=f"connector:{source}",
    )


def test_a_value_query_over_attribution_only_evidence_never_commits(mem: Memory) -> None:
    mem.append(attributed("press", "g_press"))
    mem.append(attributed("wire", "g_wire"))
    ans = current(mem, "bob", "employer")
    assert isinstance(ans, Resolved)
    # the design rows: the attribution is established and its candidate is in the answer ...
    assert ans.kernel_status is KernelStatus.ESTABLISHED
    cands = list(ans.alternatives) + ([ans.justified.segment.established] if ans.justified.segment.established else [])
    assert cands and all(isinstance(c.form, BeliefOfForm) for c in cands)
    # ... but nothing is committed: no assertion, no value candidate, the policy asks, and says what is missing
    assert ans.decision is Decision.ASK and ans.assertion is None
    assert ans.policy.rule_fired is RuleFired.ASK
    assert ans.inquiry is not None and ans.inquiry.missing == (Key(entity="bob", attr="employer"),)


@pytest.mark.parametrize("policy", [JUSTIFIED, RECENCY, LWW], ids=["justified", "recency", "lww"])
def test_no_preset_commits_to_an_attribution(mem: Memory, policy: object) -> None:
    mem.append(attributed("press", "g_press"))
    m = mem.with_policy(policy)  # type: ignore[arg-type]
    ans = current(m, "bob", "employer")
    assert isinstance(ans, Resolved)
    assert ans.decision is not Decision.COMMIT and ans.assertion is None


def test_two_different_attributions_are_asked_not_picked(mem: Memory) -> None:
    mem.append(attributed("press", "g_press", holder="alice", value="acme"))
    mem.append(attributed("wire", "g_wire", holder="carol", value="globex"))
    ans = current(mem, "bob", "employer")
    assert isinstance(ans, Resolved) and ans.kernel_status is KernelStatus.UNRESOLVED
    assert ans.decision is Decision.ASK and ans.assertion is None


def test_direct_evidence_still_commits_when_attributions_exist_beside_it(mem: Memory) -> None:
    mem.append(attributed("press", "g_press"))
    mem.append(assertion("bob", "employer", "acme", source="hr"))
    ans = current(mem, "bob", "employer")
    assert isinstance(ans, Resolved)
    assert ans.decision is Decision.COMMIT and ans.assertion is not None
    assert not isinstance(ans.assertion.form, BeliefOfForm)


def test_attributions_are_a_separate_host_call(mem: Memory) -> None:
    mem.append(attributed("press", "g_press"))
    mem.append(attributed("wire", "g_wire"))
    mem.append(attributed("press2", "g_press"))  # a copy: the same origin group
    got = mem.attributions(Key(entity="bob", attr="employer"))
    assert len(got) == 1 and isinstance(got[0], AttributedClaim)
    a = got[0]
    assert a.holder == "alice" and a.proposition == ValueProp(value="acme")
    assert a.origin_groups == ("g_press", "g_wire") and len(a.report_ids) == 3
    assert len(a.supports) == 2  # one environment per origin group: a copy corroborates nothing
    # a key with direct evidence only has no attributions
    mem.append(assertion("carol", "employer", "globex", source="hr"))
    assert mem.attributions(Key(entity="carol", attr="employer")) == ()


def test_attributions_follow_withdrawal(mem: Memory) -> None:
    r1 = mem.append(attributed("press", "g_press"))
    assert r1.entry is not None and r1.entry.report.id is not None
    assert len(mem.attributions(Key(entity="bob", attr="employer"))) == 1
    mem.withdraw(r1.entry.report.id, source=src("press"), actor="connector:press", origin_group="g_press")
    assert mem.attributions(Key(entity="bob", attr="employer")) == ()
    ans = current(mem, "bob", "employer")
    assert isinstance(ans, Resolved) and ans.kernel_status is KernelStatus.UNKNOWN and ans.decision is Decision.ABSTAIN


# --------------------------------------------------------------------------- the agent tool API


def facade() -> Facade:
    return Facade(schema=Schema(version=1, attrs=(
        Attr(name="employer", attr_class=AttrClass.SINGLE_CHANGEABLE, value_type=ValueType.STRING, inertia=True),
    )))


def test_recall_marks_attribution_only_and_lists_attributions_apart() -> None:
    m = facade()
    m.observe(attributed("press", "g_press"), source="press", origin=Origin.ATTRIBUTED)
    m.observe(attributed("wire", "g_wire"), source="wire", origin=Origin.ATTRIBUTED)
    tools = m.agent_session("agent:a1")
    out = tools.call("recall", {"query": {"entity": "bob", "attr": "employer"}})
    d = out.data
    assert d["kind"] == "resolved" and d["attribution_only"] is True
    assert d["decision"] == "ask" and d["assertion"] is None and d["single_origin"] is None
    assert len(d["attributions"]) == 1
    at = d["attributions"][0]
    assert at["holder"] == "alice" and at["origin_groups"] == ["g_press", "g_wire"]
    assert at["proposition"] == {"form": "value", "v": "acme"}
    assert "CONTENT UNKNOWN" in out.text and "alice is reported to believe 'acme' (2 origin groups)" in out.text
    assert "Do not state it as a fact" in out.text


def test_recall_without_attributions_is_unchanged() -> None:
    m = facade()
    m.observe({"entity": "bob", "attr": "employer", "value": "Acme"}, source="hr")
    d = m.agent_session("agent:a1").call("recall", {"query": {"entity": "bob", "attr": "employer"}}).data
    assert d["attribution_only"] is False and "attributions" not in d
    assert d["decision"] == "commit" and d["assertion"]["form"] == "value"
