"""Tiny helpers for writing conformance fixtures (used only by ``build_fixtures.py``).

The fixtures themselves are plain JSON; this module exists so that 60-odd scenarios stay
consistent. Nothing here is imported by an implementation under test.
"""

from __future__ import annotations

import re
from typing import Any

# --- propositions and candidate forms (field names of palimem.types.values) --------------------


def V(v: Any) -> dict[str, Any]:
    return {"form": "value", "v": v}


def M(v: Any) -> dict[str, Any]:
    return {"form": "member", "v": v}


def NM(v: Any) -> dict[str, Any]:
    return {"form": "not_member", "v": v}


def NV(v: Any) -> dict[str, Any]:
    return {"form": "not_value", "v": v}


def EN(*values: Any) -> dict[str, Any]:
    return {"form": "enumeration", "values": list(values)}


def BO(holder: str, prop: dict[str, Any]) -> dict[str, Any]:
    return {"form": "belief_of", "holder": holder, "proposition": prop}


# candidate forms (what an Answer carries): value / set / empty / not_value / not_member / belief_of
def cand(form: dict[str, Any]) -> dict[str, Any]:
    """Subset matcher for a candidate: only its ``form`` is checked (the id is derived)."""
    return {"form": form}


def c_value(v: Any) -> dict[str, Any]:
    return cand({"form": "value", "v": v})


def c_set(*values: Any) -> dict[str, Any]:
    return cand({"form": "set", "values": list(values)})


def c_empty() -> dict[str, Any]:
    return cand({"form": "empty"})


def c_not_value(v: Any) -> dict[str, Any]:
    return cand({"form": "not_value", "v": v})


def c_not_member(v: Any) -> dict[str, Any]:
    return cand({"form": "not_member", "v": v})


def c_belief_of(holder: str, prop: dict[str, Any]) -> dict[str, Any]:
    return cand({"form": "belief_of", "holder": holder, "proposition": prop})


# --- keys, queries, schema ----------------------------------------------------------------------


def K(entity: str, attr: str) -> dict[str, str]:
    return {"entity": entity, "attr": attr}


def Q(entity: str, attr: str, *, valid_at: str | None = None, belief_as_of: Any = None,
      profile: str | None = None, budget: int | None = None) -> dict[str, Any]:
    q: dict[str, Any] = {"key": K(entity, attr)}
    if valid_at is not None:
        q["valid_at"] = valid_at
    if belief_as_of is not None:
        q["belief_as_of"] = belief_as_of
    if profile is not None:
        q["profile"] = profile
    if budget is not None:
        q["explanation_budget"] = budget
    return q


def A(cls: str, *, vt: str = "string", inertia: bool | None = None, comp: str = "open",
      reads: list[str] | None = None, fn: str | None = None,
      exceptions: list[str] | None = None) -> dict[str, Any]:
    """Compact attribute declaration. ``inertia`` defaults to true for single_changeable only."""
    a: dict[str, Any] = {"class": cls, "value_type": vt}
    a["inertia"] = (cls == "single_changeable") if inertia is None else inertia
    if comp != "open":
        a["completeness"] = comp
    if reads is not None:
        a["rule"] = {"reads": reads, "fn": fn or "rule"}
        if exceptions:
            a["rule"]["exceptions"] = exceptions
    return a


SOURCES = {
    "registry": {"class": "trusted", "origin_group": "g_registry"},
    "press": {"class": "standard", "origin_group": "g_press"},
    "directory": {"class": "standard", "origin_group": "g_directory"},
    "forum": {"class": "low", "origin_group": "g_forum"},
    "q1": {"class": "quarantined", "origin_group": "g_q1"},
    "q2": {"class": "quarantined", "origin_group": "g_q2"},
    "agent_a1": {"class": "standard", "origin_group": "g_agent_a1"},
    "chat": {"class": "standard", "origin_group": "g_chat"},
}


def src(name: str) -> dict[str, str]:
    return {"id": name, "class": SOURCES[name]["class"]}


# --- operations ---------------------------------------------------------------------------------


def append(ref: str, at: str, entity: str, attr: str, prop: dict[str, Any] | None, *,
           cue: str = "assert", target: str | None = None, source: str = "registry",
           origin: str = "external_observation", group: str | None = None,
           actor: str | None = None, valid_from: str | None = None, valid_to: str | None = None,
           idem: str | None = None, crash: str | None = None, expect: dict[str, Any] | None = None,
           source_class: str | None = None, source_id: str | None = None,
           raw_ref: str | None = None, name: str | None = None) -> dict[str, Any]:
    # numbered sources s1, s2, ... are the generic independent "standard" sources of the budget fixtures
    default_class = "standard" if re.fullmatch(r"s\d+", source) else "trusted"
    s = src(source) if source in SOURCES else {"id": source, "class": default_class}
    if source_class is not None:
        s = {"id": s["id"], "class": source_class}
    if source_id is not None:
        s = {"id": source_id, "class": s["class"]}
    report: dict[str, Any] = {
        "key": K(entity, attr),
        "proposition": prop,
        "cue": cue,
        "target": target,
        "source": s,
        "origin": origin,
        "origin_group": group or SOURCES.get(source, {}).get("origin_group", f"g_{source}"),
        "actor": actor or f"connector:{source}",
        "valid_from": valid_from,
        "valid_to": valid_to,
        "precision": "day",
    }
    if raw_ref is not None:
        report["raw_ref"] = raw_ref
    op: dict[str, Any] = {"op": "append", "ref": ref, "at": at, "report": report}
    if name:
        op["name"] = name
    if idem:
        op["idempotency_key"] = idem
    if crash:
        op["crash"] = {"point": crash}
    if expect is not None:
        op["expect"] = expect
    return op


def withdraw(ref: str, at: str, target: str, entity: str, attr: str, *, source: str = "registry",
             expect: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
    return append(ref, at, entity, attr, None, cue="withdraw", target=target, source=source,
                  expect=expect, **kw)


def query(name: str, q: dict[str, Any], expect: dict[str, Any] | None = None,
          at: str | None = None, as_: dict[str, Any] | None = None) -> dict[str, Any]:
    op: dict[str, Any] = {"op": "query", "name": name, "query": q}
    if at:
        op["at"] = at
    if as_:
        op["as"] = as_
    if expect is not None:
        op["expect"] = expect
    return op


def reports(name: str, flt: dict[str, Any], expect: dict[str, Any] | None = None,
            at: str | None = None) -> dict[str, Any]:
    op: dict[str, Any] = {"op": "reports", "name": name, "filter": flt}
    if at:
        op["at"] = at
    if expect is not None:
        op["expect"] = expect
    return op


def op(kind: str, **kw: Any) -> dict[str, Any]:
    o: dict[str, Any] = {"op": kind}
    o.update(kw)
    return o


# --- matcher sugar --------------------------------------------------------------------------------


def unordered(*items: Any) -> dict[str, Any]:
    return {"$unordered": list(items)}


def contains(m: Any) -> dict[str, Any]:
    return {"$contains": m}


def none(m: Any) -> dict[str, Any]:
    return {"$none": m}


def length(n: int) -> dict[str, Any]:
    return {"$len": n}


def oneof(*items: Any) -> dict[str, Any]:
    return {"$oneof": list(items)}


def envs(*envs_: list[str]) -> dict[str, Any]:
    """``_environments`` matcher: exactly these subset-minimal environments, in any order."""
    return unordered(*[unordered(*e) for e in envs_])


ABSENT = "$absent"
ANY = "$any"


def resolved(status: str, *, decision: str | None = None, **extra: Any) -> dict[str, Any]:
    m: dict[str, Any] = {"kernel_status": status}
    if decision:
        m["decision"] = decision
    m.update(extra)
    return m


def limited(reason: str, **extra: Any) -> dict[str, Any]:
    """A ResourceLimited answer: no segment, no kernel_status, no assertion (design v0.3)."""
    m: dict[str, Any] = {"decision": "resource_limited", "reason": reason, "kernel_status": ABSENT,
                         "segment": ABSENT, "assertion": ABSENT}
    m.update(extra)
    return m
