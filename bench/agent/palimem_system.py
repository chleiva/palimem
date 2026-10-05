"""palimem as a system under test in RETRACT-ACT (symbolic: no LLM, no network, no cost).

Replays each scenario's typed reports into a real ``palimem.memory.Memory`` (host API, in-memory or SQLite backend)
and maps the palimem ``Answer`` at every decision point to one of the benchmark's actions. The mapping is a fixed,
declared rule (below), written before any test-split run and never tuned on it.

What the host binds, and what it never reads
    * ``source`` (id and class) and ``origin_group`` come from the scenario's ``sources`` registry, i.e. connector
      metadata. The report's ``text`` is never read, and the per-report ``actor`` / ``origin`` fields are not copied:
      ``origin`` is *derived* (``agent_self`` class -> ``agent_hypothesis``; a ``belief_of`` proposition ->
      ``attributed``; otherwise ``external_observation``) and ``actor`` is ``connector:<source>`` or
      ``agent:<source>``. A disagreement with the scenario's own field is recorded as a warning.
    * ``recorded_at`` is set by the log's clock, which the harness advances to the scenario's day number.

Mapping from an ``Answer`` to an action (the agent harness, identical for every palimem preset)
    * ``decision = commit`` and the asserted candidate is a plain value -> ``act`` with that value.
    * anything else (``abstain``, ``ask``, a commit to a non-value candidate such as an attribution, ``unknown``)
      -> a deferral, which is ``ask`` for a ``required`` task and ``abstain`` for an ``optional`` one. This is the
      benchmark's own gold rule 2 and the same rule the scripted reference policies use; palimem's ``abstain`` vs
      ``ask`` distinction (no evidence vs competing evidence) is therefore not scored here.
    * ``ResourceLimited`` -> no response (scored as ``abstain`` and counted in ``missing``).
    * A decision point with ``plan_formed_after``: the host subscribes the plan to the used key at plan time
      (``subscribe`` / outbox). If the belief changed version since, the plan-time and current answers are compared
      (``belief_as_of`` = the plan's log position): a changed value that is still a commit -> ``revalidate`` with
      the current value; a changed answer that is no longer a commit -> the deferral above; unchanged -> ``act``.
    * A ``post_hoc_review`` point: the host subscribes each executed action to the review points' used keys when it
      ran (the scenario format has no explicit link, so the latest earlier action is the one reviewed). If the key's
      belief changed since AND the answer at the point's ``valid_at`` differs from the answer that was justified
      when the action ran, the gap is surfaced -> ``ask``; an action that ran when memory held no justified value
      is surfaced too; otherwise ``act`` ("proceed unchanged"). (This last clause was added once, on dev, after the
      ``justified_su_off`` sensitivity run showed an action executed on an unresolved belief being waved through.)

Usage
    python bench/agent/palimem_system.py --split dev --system justified --out /tmp/dev-justified.json
    python bench/agent/palimem_system.py --split test --system justified --out ...   # ONCE per system (registry guard);
    # one invocation runs the in-memory backend (scored) and SQLite (equivalence check)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import traceback
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:  # direct use: python bench/agent/palimem_system.py
    sys.path.insert(0, str(HERE))

import score as _score

from palimem.admission import AdmissionConfig
from palimem.compat import schema_from_kernel
from palimem.kernel import AttrSpec, KernelSchema, RuleSpec
from palimem.memory import Memory
from palimem.policy import PRESETS, PolicyObject
from palimem.store import InMemoryBackend, SQLiteBackend
from palimem.types import (
    BeliefOfProp,
    Cue,
    Decision,
    Key,
    NotValueProp,
    Origin,
    Profile,
    Query,
    Report,
    Resolved,
    ResourceLimited,
    SemanticConfig,
    Source,
    ValueForm,
    ValueProp,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
STORE_SECRET = b"retract-act-symbolic-run-secret!"  # a fixed 32-byte key: nothing here is private
RESULTS_DIR = HERE / "results"
TEST_RUN_REGISTRY = RESULTS_DIR / "test_runs.json"
SYSTEMS: dict[str, dict[str, Any]] = {
    # name: policy preset and semantic self-update. The gold's default profile is P0cSU (self-update on).
    "justified": {"policy": "justified", "self_update": True},
    "recency": {"policy": "recency", "self_update": True},
    "lww": {"policy": "lww", "self_update": True},
    # sensitivity run, declared in advance: the same kernel with the same-origin self-update rule switched off,
    # scored against the `self_update_off` gold profile of RA-023
    "justified_su_off": {"policy": "justified", "self_update": False},
}


def day(n: int) -> datetime:
    return T0 + timedelta(days=int(n))


class _Clock:
    """The log's clock: the harness sets it to the scenario's day number before each append and query."""

    def __init__(self) -> None:
        self.day = 0

    def __call__(self) -> datetime:
        return day(self.day)


# ---------------------------------------------------------------------------------------------- scenario -> palimem


def kernel_schema(scn: dict) -> KernelSchema:
    attrs: dict[str, AttrSpec] = {}
    for name, spec in scn["attrs"].items():
        cls = spec["class"]
        if cls == "single_changeable":
            attrs[name] = AttrSpec(name, "single", True)
        elif cls == "single_stable":
            attrs[name] = AttrSpec(name, "single", False)
        elif cls == "derived":
            attrs[name] = AttrSpec(name, "single", True, error_allowed=False, derived=True)
        else:  # pragma: no cover - the scenario schema allows exactly these three today
            raise ValueError(f"unsupported attribute class {cls!r}")
    rules: list[RuleSpec] = []
    for i, d in enumerate(scn["derivations"]):
        if d["kind"] == "lookup":
            rules.append(RuleSpec(
                id=f"d{i}", head=(d["head"], "?e", "?c"),
                body=((d["via"], "?e", "?x"), (d["lookup"], "?x", "?c")),
            ))
        elif d["kind"] == "copy":
            rules.append(RuleSpec(id=f"d{i}", head=(d["head"], "?e", "?v"), body=((d["from"], "?e", "?v"),)))
        else:  # pragma: no cover
            raise ValueError(f"unsupported derivation {d['kind']!r}")
    ents: set[str] = set()
    for r in scn["reports"]:
        ents.add(r["key"]["entity"])
        p = r.get("proposition")
        while p and p["form"] == "belief_of":
            p = p["inner"]
        if p and p.get("value") is not None:
            ents.add(str(p["value"]))
    return KernelSchema(attrs=attrs, rules=tuple(rules), entities=tuple(sorted(ents)))


def _proposition(p: dict | None) -> Any:
    if p is None:
        return None
    if p["form"] == "value":
        return ValueProp(value=p["value"])
    if p["form"] == "not_value":
        return NotValueProp(value=p["value"])
    if p["form"] == "belief_of":
        return BeliefOfProp(holder=p["holder"], proposition=_proposition(p["inner"]))
    raise ValueError(f"unsupported proposition form {p['form']!r}")


def bind_report(scn: dict, r: dict, ids: dict[str, str], warnings: list[str]) -> Report:
    """Build the palimem ``Report`` the way a host would: identity from the connector registry, never from text."""
    sid = r["source"]["id"]
    reg = scn["sources"][sid]
    prop = r.get("proposition")
    if reg["class"] == "agent_self":
        origin, actor = Origin.AGENT_HYPOTHESIS, f"agent:{sid}"
    elif prop is not None and prop["form"] == "belief_of":
        origin, actor = Origin.ATTRIBUTED, f"connector:{sid}"
    else:
        origin, actor = Origin.EXTERNAL_OBSERVATION, f"connector:{sid}"
    if origin.value != r["origin"]:
        warnings.append(f"{scn['id']}/{r['id']}: bound origin {origin.value} differs from the scenario field {r['origin']}")
    if reg["origin_group"] != r["origin_group"]:
        warnings.append(f"{scn['id']}/{r['id']}: registry origin_group differs from the scenario field")
    return Report(
        key=Key(entity=r["key"]["entity"], attr=r["key"]["attr"]), cue=Cue(r["cue"]), proposition=_proposition(prop),
        target=None if r.get("target") is None else ids[r["target"]],
        source=Source(id=sid, cls=reg["class"]), origin=origin, origin_group=reg["origin_group"], actor=actor,
        valid_from=None if r.get("valid_from") is None else day(r["valid_from"]),
    )


# ---------------------------------------------------------------------------------------------- answer -> action


@dataclass(frozen=True)
class View:
    """What the agent harness sees from one palimem answer."""

    kind: str  # "value" | "defer" | "limited"
    value: str | None = None
    kernel_status: str | None = None
    decision: str | None = None
    note: str = ""

    def same_as(self, other: View) -> bool:
        return (self.kind, self.value, self.kernel_status) == (other.kind, other.value, other.kernel_status)


def view_of(ans: Any) -> View:
    if isinstance(ans, ResourceLimited):
        return View("limited", note=f"resource_limited:{ans.reason.value}")
    assert isinstance(ans, Resolved)
    st, dec = ans.kernel_status.value, ans.decision.value
    if ans.decision is Decision.COMMIT and ans.assertion is not None:
        form = ans.assertion.form
        if isinstance(form, ValueForm):
            return View("value", str(form.value), st, dec)
        return View("defer", None, st, dec, note=f"commit to a non-value candidate ({type(form).__name__})")
    return View("defer", None, st, dec)


def _defer(mode: str) -> dict:
    return {"action": "ask" if mode == "required" else "abstain", "value": None}


@dataclass
class Trace:
    scenario: str
    point: str
    action: str | None
    value: str | None
    now: View | None
    reference: View | None = None  # the plan-time or execution-time view, when one was compared
    events: int = 0
    note: str = ""


@dataclass
class ScenarioRun:
    scenario: str
    responses: dict[str, dict | None] = field(default_factory=dict)
    traces: list[Trace] = field(default_factory=list)
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


def _events(mem: Memory, plan_id: str, key: Key) -> int:
    return sum(1 for e in mem.backend.pending_events(10_000) if e.plan_id == plan_id and e.key == key)


def run_scenario(scn: dict, system: str, backend: str = "memory") -> ScenarioRun:
    cfg = SYSTEMS[system]
    run = ScenarioRun(scenario=scn["id"])
    clock = _Clock()
    be = InMemoryBackend(clock=clock, store_secret=STORE_SECRET) if backend == "memory" else SQLiteBackend(
        ":memory:", clock=clock, store_secret=STORE_SECRET)
    ks = kernel_schema(scn)
    policy: PolicyObject = PRESETS[cfg["policy"]]
    mem = Memory(
        be, schema_from_kernel(ks), kernel_schema=ks, entities=ks.entities,
        semantic=SemanticConfig(self_update=cfg["self_update"], profile=Profile.OPEN_WORLD),
        admission=AdmissionConfig(profile=Profile.OPEN_WORLD), policy=policy,
    )
    ids: dict[str, str] = {}
    lsns: dict[str, int] = {}
    points = scn["decision_points"]
    executed = list(enumerate(scn.get("executed_actions", [])))

    def q(key: Key, valid_at: int | None, as_of: int | None = None) -> View:
        return view_of(mem.query(Query(
            key=key, valid_at=None if valid_at is None else day(valid_at), belief_as_of=as_of, profile=Profile.OPEN_WORLD)))

    try:
        for r in scn["reports"]:
            clock.day = r["recorded_at"]
            res = mem.append(bind_report(scn, r, ids, run.warnings), idempotency_key=f"{scn['id']}:{r['id']}")
            assert res.entry is not None and res.entry.report.id is not None
            ids[r["id"]], lsns[r["id"]] = res.entry.report.id, res.entry.lsn
            for i, ex in executed:  # an executed action rests on the beliefs the scenario's review points use
                if ex["after_report"] == r["id"]:
                    keys = {Key(**dp["tool"]["use_key"]) for dp in points if dp["kind"] == "post_hoc_review"}
                    mem.backend.subscribe(f"exec:{scn['id']}:{i}", sorted(keys, key=lambda k: (k.entity, k.attr)))
            for dp in points:
                if dp.get("plan_formed_after") == r["id"]:
                    mem.backend.subscribe(f"plan:{dp['id']}", [Key(**dp["tool"]["use_key"])])
            for dp in points:
                if dp["after_report"] == r["id"]:
                    resp, tr = _decide_point(mem, scn, dp, q, lsns, executed)
                    run.responses[dp["id"]] = resp
                    run.traces.append(tr)
    except Exception as e:  # noqa: BLE001 - a contract gap or an unsupported form: recorded, never hidden, never worked around
        run.error = f"{type(e).__name__}: {e}"
        run.warnings.append(traceback.format_exc(limit=3))
    for dp in points:  # points never reached (after an error) are missing responses
        run.responses.setdefault(dp["id"], None)
    mem.close()
    return run


def _decide_point(mem: Memory, scn: dict, dp: dict, q: Any, lsns: dict[str, int], executed: list) -> tuple[dict | None, Trace]:
    key = Key(**dp["tool"]["use_key"])
    now = q(key, dp.get("valid_at"))
    tr = Trace(scn["id"], dp["id"], None, None, now)
    if now.kind == "limited":
        tr.note = now.note
        return None, tr
    if dp["kind"] == "post_hoc_review":
        # the scenario format does not link a review point to its executed action by name (tool names differ), so the
        # rule is positional: the latest action executed at or before the review point's report
        done = [(i, e) for i, e in executed if lsns[e["after_report"]] <= lsns[dp["after_report"]]]
        ex = max(done, key=lambda ie: lsns[ie[1]["after_report"]]) if done else None
        if ex is None:
            tr.note = "no executed action for this review point"
            resp = {"action": "act", "value": now.value}
        else:
            i, e = ex
            then = q(key, dp.get("valid_at"), lsns[e["after_report"]])
            tr.reference, tr.events = then, _events(mem, f"exec:{scn['id']}:{i}", key)
            if then.kind != "value":
                resp = {"action": "ask", "value": None}
                tr.note = "the action was executed without a justified value: surfaced"
            elif tr.events and not then.same_as(now):
                resp = {"action": "ask", "value": None}
                tr.note = "the belief an executed action rested on changed: gap surfaced"
            else:
                resp = {"action": "act", "value": now.value}
    elif dp.get("plan_formed_after"):
        then = q(key, dp.get("valid_at"), lsns[dp["plan_formed_after"]])
        tr.reference, tr.events = then, _events(mem, f"plan:{dp['id']}", key)
        changed = bool(tr.events) and not then.same_as(now)
        if now.kind != "value":
            resp = _defer(dp["mode"])
        elif changed:
            resp = {"action": "revalidate", "value": now.value}
            tr.note = "a subscribed belief changed since planning: re-read, then act on the current value"
        else:
            resp = {"action": "act", "value": now.value}
    elif now.kind == "value":
        resp = {"action": "act", "value": now.value}
    else:
        resp = _defer(dp["mode"])
    tr.action, tr.value = resp["action"], resp["value"]
    return resp, tr


# ---------------------------------------------------------------------------------------------- whole-split runs


def adapter_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def scenarios_hash(scenarios: list[dict]) -> str:
    return hashlib.sha256(json.dumps(scenarios, sort_keys=True).encode()).hexdigest()


def run_system(system: str, scenarios: list[dict], backend: str = "memory") -> dict:
    responses: dict[str, dict] = {}
    traces: list[dict] = []
    errors: dict[str, str] = {}
    warnings: list[str] = []
    for scn in scenarios:
        run = run_scenario(scn, system, backend)
        responses[scn["id"]] = {k: v for k, v in run.responses.items() if v is not None}
        traces += [t.__dict__ | {"now": t.now.__dict__ if t.now else None,
                                 "reference": t.reference.__dict__ if t.reference else None} for t in run.traces]
        if run.error:
            errors[scn["id"]] = run.error
        warnings += [w for w in run.warnings if not w.startswith("Traceback")]
    return {"system": system, "backend": backend, "config": SYSTEMS[system], "adapter_sha256": adapter_hash(),
            "scenarios_sha256": scenarios_hash(scenarios), "scenario_ids": [s["id"] for s in scenarios],
            "responses": responses, "traces": traces, "errors": errors, "warnings": warnings}


def _registry() -> dict:
    return json.loads(TEST_RUN_REGISTRY.read_text()) if TEST_RUN_REGISTRY.exists() else {}


def run_both(system: str, scenarios: list[dict]) -> dict:
    """One invocation: the primary in-memory run, plus the SQLite backend as an implementation-equivalence check."""
    primary = run_system(system, scenarios, "memory")
    other = run_system(system, scenarios, "sqlite")
    diffs = [(sid, pid) for sid, rs in primary["responses"].items() for pid in set(rs) | set(other["responses"].get(sid, {}))
             if rs.get(pid) != other["responses"].get(sid, {}).get(pid)]
    primary["backend"] = "memory (primary) + sqlite (equivalence check)"
    primary["sqlite_responses_identical"] = not diffs and primary["errors"] == other["errors"]
    primary["sqlite_differences"] = [f"{s}/{p}" for s, p in diffs]
    return primary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    ap.add_argument("--system", choices=sorted(SYSTEMS), required=True)
    ap.add_argument("--backend", choices=["memory", "sqlite", "both"], default="both")
    ap.add_argument("--out", required=True)
    ap.add_argument("--allow-test-rerun", action="store_true",
                    help="only to re-run a test split on purpose; the rerun is recorded and must be disclosed")
    a = ap.parse_args(argv)
    scn = _score.load_scenarios(split=a.split)
    touches_test = a.split in ("test", "all")
    if touches_test:
        reg = _registry()
        if a.system in reg and not a.allow_test_rerun:
            print(f"REFUSED: system {a.system!r} already ran on the test split ({reg[a.system]['utc']}); "
                  "the pre-registered protocol allows one run. Use --allow-test-rerun and disclose it.", file=sys.stderr)
            return 2
    out = run_both(a.system, scn) if a.backend == "both" else run_system(a.system, scn, a.backend)
    if touches_test:
        reg = _registry()
        prev = reg.get(a.system)
        reg[a.system] = {"utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "split": a.split,
                         "adapter_sha256": out["adapter_sha256"], "scenarios_sha256": out["scenarios_sha256"],
                         "reruns": 0 if prev is None else prev.get("reruns", 0) + 1}
        RESULTS_DIR.mkdir(exist_ok=True)
        TEST_RUN_REGISTRY.write_text(json.dumps(reg, indent=1, sort_keys=True) + "\n")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    res = _score.score(scn, out["responses"])
    print(_score.format_table({a.system: res["overall"]}))
    if "sqlite_responses_identical" in out:
        print("sqlite equivalent:", out["sqlite_responses_identical"], out["sqlite_differences"])
    if out["errors"]:
        print("errors:", json.dumps(out["errors"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
