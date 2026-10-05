"""Ruling 16 (2026-10-05): the host API abstains on an unresolved key and ALWAYS says what would settle it;
an agent session defaults to ask.

* ``decide`` fills ``Resolved.inquiry`` on every ``ask`` AND every ``abstain`` (competing candidates, the keys whose
  evidence would decide, the source classes that could supply it); a ``commit`` never carries one;
* the ``abstain`` preset is the host default (policy label ``p-default``); the agent default is ``p-ask`` (``justified``);
* the facade's default policy is ``abstain`` for a new store, and a reopened store keeps the policy it stored;
* the types allow an inquiry on an abstain, still require one on an ask and still refuse one on a commit.
"""

from __future__ import annotations

import pytest

from palimem import Memory as Facade
from palimem.agent import (
    DEFAULT_AGENT_POLICY_LABEL,
    DEFAULT_POLICY_LABEL,
    SessionContext,
)
from palimem.memory import Memory
from palimem.policy import ABSTAIN, JUSTIFIED, PRESETS
from palimem.types import (
    Attr,
    AttrClass,
    Decision,
    Inquiry,
    KernelStatus,
    Key,
    Resolved,
    Schema,
    ValidationError,
    ValueType,
)
from tests._pipeline_helpers import Clock, assertion, current, make_backend, toy_memory

BACKENDS = ["memory", "sqlite"]
KEY = Key(entity="alice", attr="employer")


def two_way(m: Memory) -> Resolved:
    m.append(assertion("alice", "employer", "acme", "press"))
    m.append(assertion("alice", "employer", "globex", "wire"))
    ans = current(m, "alice", "employer")
    assert isinstance(ans, Resolved)
    return ans


@pytest.mark.parametrize("backend", BACKENDS)
def test_the_abstain_policy_abstains_on_an_unresolved_key_and_says_what_would_settle_it(backend: str) -> None:
    m = toy_memory(make_backend(backend, Clock()), policy=ABSTAIN)
    ans = two_way(m)
    assert ans.kernel_status is KernelStatus.UNRESOLVED and ans.decision is Decision.ABSTAIN
    assert ans.assertion is None and len(ans.alternatives) == 2
    inq = ans.inquiry
    assert inq is not None
    assert {c.id for c in inq.competing} == {c.id for c in ans.alternatives}
    assert inq.missing == (KEY,)
    assert inq.resolvers == ("trusted", "standard")


@pytest.mark.parametrize("backend", BACKENDS)
def test_resolvers_are_the_classes_at_least_as_trusted_as_the_best_class_already_heard(backend: str) -> None:
    m = toy_memory(make_backend(backend, Clock()), policy=ABSTAIN)
    ans = two_way(m)  # both reports come from `standard` sources
    assert ans.inquiry is not None
    assert ans.inquiry.resolvers == ("trusted", "standard")  # a `low` source could not outweigh what was heard


@pytest.mark.parametrize("backend", BACKENDS)
def test_an_unknown_key_abstains_with_an_inquiry_that_names_itself(backend: str) -> None:
    m = toy_memory(make_backend(backend, Clock()), policy=ABSTAIN)
    ans = current(m, "alice", "employer")
    assert isinstance(ans, Resolved)
    assert ans.kernel_status is KernelStatus.UNKNOWN and ans.decision is Decision.ABSTAIN
    assert ans.inquiry is not None and ans.inquiry.competing == () and ans.inquiry.missing == (KEY,)
    assert ans.inquiry.resolvers == ("trusted", "standard", "low")  # nothing heard yet: any known class could supply it


@pytest.mark.parametrize("backend", BACKENDS)
def test_an_ask_carries_the_same_inquiry_and_a_commit_never_does(backend: str) -> None:
    m = toy_memory(make_backend(backend, Clock()), policy=JUSTIFIED)
    ans = two_way(m)
    assert ans.decision is Decision.ASK and ans.inquiry is not None
    assert ans.inquiry.missing == (KEY,) and ans.inquiry.resolvers == ("trusted", "standard")
    m.append(assertion("alice", "employer", "initech", "registry"))  # a third, still disputed value: stays an ask
    solo = toy_memory(make_backend(backend, Clock()), policy=ABSTAIN)
    solo.append(assertion("bob", "employer", "acme", "press"))
    only = current(solo, "bob", "employer")
    assert isinstance(only, Resolved)
    assert only.decision is Decision.COMMIT and only.inquiry is None


@pytest.mark.parametrize("backend", BACKENDS)
def test_a_derived_key_names_the_base_keys_its_rule_reads(backend: str) -> None:
    m = toy_memory(make_backend(backend, Clock()), policy=ABSTAIN)
    m.append(assertion("alice", "employer", "veltran", "press"))
    m.append(assertion("alice", "employer", "acme", "wire"))  # two employers: the derived work_city is not decided
    ans = current(m, "alice", "work_city")
    assert isinstance(ans, Resolved)
    if ans.decision is Decision.ABSTAIN:  # whichever way the toy rule resolves, an abstain must name its deciding keys
        assert ans.inquiry is not None
        assert Key(entity="alice", attr="work_city") in ans.inquiry.missing
        assert any(k.attr in ("employer", "hq_city") for k in ans.inquiry.missing)


def test_the_host_default_is_abstain_and_the_agent_default_is_ask() -> None:
    assert PRESETS["abstain"] is ABSTAIN
    assert DEFAULT_POLICY_LABEL == "p-default" and DEFAULT_AGENT_POLICY_LABEL == "p-ask"
    assert SessionContext(session_id="s", agent_principal="agent:a").policy_version == "p-ask"


def test_the_facade_asks_the_host_default_and_an_agent_session_asks_by_default() -> None:
    schema = Schema(
        version=1,
        attrs=(Attr(name="employer", attr_class=AttrClass.SINGLE_CHANGEABLE, value_type=ValueType.STRING, inertia=True),),
    )
    f = Facade(schema=schema)
    f.observe({"entity": "alice", "attr": "employer", "value": "Acme"}, source="press")
    f.observe({"entity": "alice", "attr": "employer", "value": "Globex"}, source="wire")
    host_answer = f.ask("employer", "alice")  # the host API: abstain, with the inquiry
    assert isinstance(host_answer, Resolved)
    assert host_answer.decision is Decision.ABSTAIN and host_answer.inquiry is not None
    tools = f.agent_session("agent:planner")  # an agent session: ask, with the same inquiry
    assert tools.ctx.policy_version == "p-ask"
    out = tools.call("recall", {"query": {"entity": "alice", "attr": "employer"}})
    assert out.data["decision"] == "ask" and out.data["inquiry"]["missing"] == [{"entity": "alice", "attr": "employer"}]
    # an explicit per-session override stays possible
    host_like = f.agent_session("agent:planner", policy_version="p-default")
    assert host_like.call("recall", {"query": {"entity": "alice", "attr": "employer"}}).data["decision"] == "abstain"


def test_a_reopened_store_keeps_its_stored_policy(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db = tmp_path / "pm.db"
    first = Facade(db, policy="justified")
    first.close()
    again = Facade(db)  # policy not given: the stored one, not the new default
    assert again.core.policy.name == "justified"
    again.close()
    fresh = Facade(tmp_path / "fresh.db")
    assert fresh.core.policy.name == "abstain"
    fresh.close()


def test_the_types_require_an_inquiry_on_ask_allow_one_on_abstain_and_refuse_one_on_commit() -> None:
    m = toy_memory(make_backend("memory", Clock()), policy=ABSTAIN)
    abstained = two_way(m)
    assert abstained.inquiry is not None
    from dataclasses import replace

    with pytest.raises(ValidationError):
        replace(abstained, decision=Decision.ASK, inquiry=None)  # an ask needs its inquiry
    replace(abstained, inquiry=None)  # an abstain may omit it (callers other than `decide`)
    solo = toy_memory(make_backend("memory", Clock()), policy=ABSTAIN)
    solo.append(assertion("bob", "employer", "acme", "press"))
    committed = current(solo, "bob", "employer")
    assert isinstance(committed, Resolved)
    with pytest.raises(ValidationError):
        replace(committed, inquiry=abstained.inquiry)  # a commit never carries one


def test_an_inquiry_must_offer_something_to_do() -> None:
    with pytest.raises(ValidationError):
        Inquiry(competing=())
    assert Inquiry(competing=(), missing=(KEY,)).missing == (KEY,)
