"""FROZEN copy of ``palimem.agent.render`` as of commit 034d520 (registered product v1).

Do not edit. The registered RETRACT-ACT runs (docs/eval/AGENT_BENCHMARK_RESULTS.md, AGENT_BENCHMARK_LLM_RESULTS.md) were
produced with this rendering of ``recall`` answers. The product's renderer changed afterwards (attribution-only answers
are now marked and listed apart, Lane Q); the prompts built from the new text differ, so the cached model replies
of the registered runs only match under this frozen module. It is used by ``registered_product_v1`` and nothing else.

Original module docstring:

Rendering an :class:`~palimem.types.Answer` for an LLM (docs/AGENT_GUIDE.md).

Two outputs per answer: a JSON-able dict (what the tools return as structured content) and a short text that tells
a model what the answer licenses. The text is a *contract with the prompt*: golden-snapshot tests
(``tests/test_agent_render.py``) pin it, because a change in wording changes agent behaviour.

Single-origin marking (the decided poisoning gate, SEC-39): every commit or established belief that rests on **one
origin group** is marked ``single_origin: true`` and the text says so. Only a second origin group raises it above that.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from palimem.admission import normalise_value
from palimem.types import (
    Answer,
    BeliefAsOf,
    Candidate,
    Decision,
    Explanation,
    KernelStatus,
    Key,
    LogEntry,
    Resolved,
    ResourceLimited,
    SetForm,
    Support,
    ValueForm,
)
from palimem.types._codec import ts_to_str
from palimem.types.values import (
    EnumerationProp,
    MemberProp,
    NotMemberProp,
    NotValueProp,
    ValueProp,
)

if TYPE_CHECKING:
    from .host import Host, Notice


def candidate_json(c: Candidate) -> dict[str, Any]:
    out: dict[str, Any] = {"id": c.id}
    out.update(c.form.to_dict())
    return out


def _prop_values(p: Any) -> list[Any]:
    if isinstance(p, ValueProp | MemberProp | NotMemberProp | NotValueProp):
        return [p.value]
    if isinstance(p, EnumerationProp):
        return list(p.values)
    return []


def origin_groups(host: Host, ans: Resolved, key: Key, as_of: BeliefAsOf | None) -> tuple[str, ...] | None:
    """The origin groups the answered belief rests on, or ``None`` when that cannot be told.

    From the segment's supports when they exist (report ids in the environments), otherwise from the key's own
    admissible evidence. A derived key with no supports yet returns ``None``: unknown, never a guess."""
    seg = ans.justified.segment
    c = ans.assertion or seg.established
    if c is None:
        return None
    backend = host.mem.backend
    ids = {rid for s in seg.support.get(c.id, ()) for rid in s.environment}
    groups: set[str] = set()
    for rid in ids:
        e = backend.get_entry(rid)
        if isinstance(e, LogEntry):
            groups.add(e.report.origin_group)
    if groups:
        return tuple(sorted(groups))
    if host.mem.schema.attr(key.attr).attr_class.value == "derived":
        return None
    form = c.form
    wanted: list[Any] | None
    if isinstance(form, ValueForm):
        wanted = [normalise_value(form.value)]
    elif isinstance(form, SetForm):
        wanted = [normalise_value(v) for v in form.values]
    else:
        wanted = None
    for e in host.mem.evidence(key, as_of).direct:
        vals = [normalise_value(v) for v in _prop_values(e.report.proposition)]
        if wanted is None or any(v in wanted for v in vals):
            groups.add(e.report.origin_group)
    return tuple(sorted(groups)) if groups else None


def answer_json(
    ans: Answer, *, host: Host, key: Key, as_of: BeliefAsOf | None, policy_label: str, max_alternatives: int,
    notices: Sequence[Notice] = (),
) -> dict[str, Any]:
    """The agent-facing answer. ``kernel_status`` and ``decision`` are both present (a policy commitment on an
    unresolved key stays visible as exactly that)."""
    nj = [n.to_dict() for n in notices]
    base: dict[str, Any] = {"key": {"entity": key.entity, "attr": key.attr}}
    if isinstance(ans, ResourceLimited):
        lc = ans.last_complete
        return {
            **base, "kind": "resource_limited", "decision": "resource_limited", "reason": ans.reason.value,
            "reason_key": None if ans.reason_key is None else {"entity": ans.reason_key.entity, "attr": ans.reason_key.attr},
            "required_generation": ans.required_generation, "completed_generation": ans.completed_generation,
            "last_complete": None if lc is None else {
                "belief_as_of": lc.belief_as_of, "label": "older snapshot: not the current belief",
                "kernel_status": lc.view.segment.kernel_status.value,
            },
            "notices": nj,
        }
    assert isinstance(ans, Resolved)
    groups = origin_groups(host, ans, key, as_of)
    settled = ans.kernel_status in (
        KernelStatus.ESTABLISHED, KernelStatus.ESTABLISHED_EMPTY, KernelStatus.ESTABLISHED_FALSE
    ) or ans.decision is Decision.COMMIT
    single: bool | None = None if (groups is None or not settled or ans.assertion is None) else len(groups) == 1
    alts = list(ans.alternatives)
    out: dict[str, Any] = {
        **base, "kind": "resolved",
        "segment": {
            "valid_from": None if ans.segment.valid_from is None else ts_to_str(ans.segment.valid_from),
            "valid_to": None if ans.segment.valid_to is None else ts_to_str(ans.segment.valid_to),
        },
        "kernel_status": ans.kernel_status.value, "decision": ans.decision.value,
        "assertion": None if ans.assertion is None else candidate_json(ans.assertion),
        "alternatives": [candidate_json(c) for c in alts[:max_alternatives]],
        "alternatives_truncated": len(alts) > max_alternatives,
        "origin_groups": None if groups is None else list(groups),
        "single_origin": single,
        "inquiry": None,
        "policy": {"version": policy_label, "rule_fired": ans.policy.rule_fired.value},
        "confidence": ans.confidence,
        "explanation": ans.explanation.value,
        "notices": nj,
    }
    if ans.inquiry is not None:
        out["inquiry"] = {
            "competing": [candidate_json(c) for c in ans.inquiry.competing[:max_alternatives]],
            "missing": [{"entity": k.entity, "attr": k.attr} for k in ans.inquiry.missing],
            "resolvers": list(ans.inquiry.resolvers),
        }
    return out


def answer_text(d: Mapping[str, Any]) -> str:
    """The short text a model reads. Stable wording (golden-tested)."""
    k = d["key"]
    head = f"{k['entity']}/{k['attr']}"
    if d["kind"] == "resource_limited":
        lines: list[str] = [f"{head}: NO ANSWER (resource_limited: {d['reason']})."]
        rk = d.get("reason_key")
        if rk:
            lines.append(f"  Blocked on {rk['entity']}/{rk['attr']}. Do not treat any older value as current; retry later.")
        else:
            lines.append("  Do not treat any older value as current; retry later.")
        if d.get("last_complete"):
            lines.append("  An older snapshot exists; it is labelled as such and is not the current belief.")
        return "\n".join(lines)
    status, decision = d["kernel_status"], d["decision"]
    a = d["assertion"]
    lines = []
    if status == "established":
        lines.append(f"{head}: ESTABLISHED = {_assertion_text(a)}.")
    elif status == "established_empty":
        lines.append(f"{head}: ESTABLISHED EMPTY (explicitly no members).")
    elif status == "established_false":
        lines.append(f"{head}: ESTABLISHED AGAINST ({_assertion_text(a)}).")
    elif status == "unknown":
        lines.append(f"{head}: UNKNOWN (no admissible evidence). Do not guess; say it is unknown or ask.")
    else:
        alts = [_alt_text(c) for c in ([a] if a else []) + list(d["alternatives"])]
        lines.append(f"{head}: UNRESOLVED between {', '.join(alts)}.")
        if decision == "commit" and a is not None:
            lines.append(f"  Policy committed to {_assertion_text(a)}; this is a policy choice, not an established fact.")
        elif decision == "ask":
            lines.append("  Evidence does not decide. Ask a source that can, or tell the user it is unsettled.")
        else:
            lines.append("  Evidence does not decide and policy abstains. Do not pick one.")
    if d.get("alternatives_truncated"):
        lines.append("  (more alternatives exist than are listed)")
    if d.get("single_origin") is True:
        g = d["origin_groups"][0]
        lines.append(f"  SINGLE ORIGIN: rests on one origin group ({g}); uncorroborated. Corroboration from a second origin group is what raises it.")
    elif d.get("single_origin") is False:
        lines.append(f"  Corroborated by {len(d['origin_groups'])} origin groups.")
    if d.get("inquiry") and d["inquiry"]["resolvers"]:
        lines.append(f"  Resolvers that could decide: {', '.join(d['inquiry']['resolvers'])}.")
    lines.append(f"  decision={decision}; policy={d['policy']['version']}.")
    return "\n".join(lines)


def _assertion_text(a: Mapping[str, Any] | None) -> str:
    if a is None:
        return "(none)"
    form = a["form"]
    if form == "value":
        return repr(a["v"])
    if form == "set":
        return "{" + ", ".join(repr(v) for v in a["values"]) + "}"
    rest = {k: v for k, v in a.items() if k not in ("id", "form")}
    inner = ", ".join(f"{v!r}" for v in rest.values())
    return f"{form}({inner})" if inner else form


def _alt_text(a: Mapping[str, Any]) -> str:
    return _assertion_text(a)


def _environment_json(host: Host, s: Support) -> dict[str, Any]:
    reports = []
    for rid in s.environment:
        e = host.mem.backend.get_entry(rid)
        if isinstance(e, LogEntry):
            reports.append({
                "id": rid, "source": e.report.source.id, "origin_group": e.report.origin_group,
                "origin": e.report.origin.value,
            })
        else:
            reports.append({"id": rid})
    return {
        "reports": reports,
        "valid_from": None if s.valid_from is None else ts_to_str(s.valid_from),
        "valid_to": None if s.valid_to is None else ts_to_str(s.valid_to),
    }


def explanation_json(
    exp: Explanation, *, host: Host, depth_applied: int, notices: Sequence[Notice] = (), key: Key,
    answer: Answer | None = None,
) -> dict[str, Any]:
    """Environments as lists of reports with who said each (source id, origin group, origin), capped.

    When the answered ``Resolved`` is given, the environments are also grouped by the candidate they support
    (``by_candidate``), so an unresolved key shows which reports back which alternative."""
    out: dict[str, Any] = {
        "kind": "explanation", "key": {"entity": key.entity, "attr": key.attr}, "mode": exp.mode.value,
        "depth_applied": depth_applied, "state": exp.state.value,
        "environments": [_environment_json(host, s) for s in exp.environments],
        "notices": [n.to_dict() for n in notices],
    }
    if isinstance(answer, Resolved):
        seg = answer.justified.segment
        cands = ([seg.established] if seg.established is not None else []) + list(seg.alternatives)
        groups = []
        for c in cands:
            sups = seg.support.get(c.id, ())
            if exp.mode.value == "one":
                sups = sups[:1]
            groups.append({"candidate": candidate_json(c), "environments": [_environment_json(host, s) for s in sups]})
        out["by_candidate"] = groups
    return out


def explanation_text(d: Mapping[str, Any]) -> str:
    k = d["key"]
    head = f"{k['entity']}/{k['attr']}"
    groups = d.get("by_candidate") or []
    if not d["environments"] and not any(g["environments"] for g in groups):
        return f"{head}: no supporting reports in the explained segment (depth {d['depth_applied']})."
    lines = [f"{head}: justification (depth {d['depth_applied']}):"]

    def who(env: Mapping[str, Any]) -> str:
        return "; ".join(f"{r['id']} from {r.get('source', '?')} ({r.get('origin_group', '?')})" for r in env["reports"])

    if groups:
        for g in groups:
            lines.append(f"  {_assertion_text(g['candidate'])}:")
            lines.extend(f"    - {who(env)}" for env in g["environments"]) if g["environments"] else lines.append("    - (no supporting reports)")
    else:
        lines.extend(f"  {i}. {who(env)}" for i, env in enumerate(d["environments"], 1))
    return "\n".join(lines)
