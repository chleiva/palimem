"""Exploratory trace of one RETRACT-ACT scenario on the product as it is now (never a registered run).

Prints, for each report of the scenario, what admission decided (outcome, reason, effective cue, authority check), what
the kernel reads (the evidence view), what the kernel concluded (status, candidates, supports) and what the policy did.
It builds the same Memory as ``palimem_system.run_scenario`` and calls the product's public query path; it neither
reads nor writes the one-run registry. Usage: ``python bench/agent/ra_trace.py RA-007 [--system justified] [--md]``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import palimem_system as ps
import score

from palimem.admission import AdmissionConfig, kernel_view
from palimem.memory import Memory
from palimem.store import InMemoryBackend
from palimem.types import Key, Profile, Query, Resolved, SemanticConfig


def trace(scn_id: str, system: str = "justified") -> list[str]:
    scns = {s["id"]: s for s in score.load_scenarios()}
    scn = scns[scn_id]
    cfg = ps.SYSTEMS[system]
    clock = ps._Clock()
    be = InMemoryBackend(clock=clock, store_secret=ps.STORE_SECRET)
    ks = ps.kernel_schema(scn)
    mem = Memory(
        be, ps.schema_from_kernel(ks), kernel_schema=ks, entities=ks.entities,
        semantic=SemanticConfig(self_update=cfg["self_update"], profile=Profile.OPEN_WORLD),
        admission=AdmissionConfig(profile=Profile.OPEN_WORLD), policy=ps.PRESETS[cfg["policy"]],
    )
    out: list[str] = []
    ids: dict[str, str] = {}
    names: dict[str, str] = {}
    warnings: list[str] = []
    for r in scn["reports"]:
        clock.day = r["recorded_at"]
        res = mem.append(ps.bind_report(scn, r, ids, warnings), idempotency_key=f"{scn['id']}:{r['id']}")
        assert res.entry is not None and res.entry.report.id is not None
        ids[r["id"]] = res.entry.report.id
        names[res.entry.report.id] = r["id"]
    head = mem.backend.head().lsn
    ev = mem.pipeline.evaluate(head)
    out.append(f"### Reports in (system `{system}`, semantic self_update={cfg['self_update']}, policy `{cfg['policy']}`)\n")
    out.append("| LSN | id | source (origin group) | cue | target | proposition | recorded day |")
    out.append("|---|---|---|---|---|---|---|")
    for e in ev.entries:
        rp = e.report
        prop = "-" if rp.proposition is None else f"{type(rp.proposition).__name__}({getattr(rp.proposition, 'value', '')})"
        tgt = names.get(rp.target, rp.target) if rp.target else "-"
        out.append(f"| {e.lsn} | {names[rp.id]} | {rp.source.id} ({rp.origin_group}) | {rp.cue.value} | {tgt} | {prop} | {clock_day(e)} |")
    out.append("\n### Admission decisions\n")
    out.append("| id | outcome | reason | effective cue | authority check | withdraws |")
    out.append("|---|---|---|---|---|---|")
    for e in ev.entries:
        d = ev.decisions[e.report.id]
        a = "-" if d.authority is None else f"{'allowed' if d.authority.allowed else 'FAILED'} ({d.authority.reason if hasattr(d.authority, 'reason') else ''})"
        out.append(f"| {names[e.report.id]} | {d.record.outcome.value} | {d.record.reason.value} | {d.effective_cue.value} | {a} | "
                   f"{[names.get(w, w) for w in d.withdraws] or '-'} |")
    out.append("\n### What the kernel reads (`kernel_view` of the admitted reports)\n")
    for dp in scn["decision_points"]:
        key = Key(**dp["tool"]["use_key"])
        direct = [kernel_view(e, ev.decisions[e.report.id]) for e in ev.entries
                  if e.report.key == key and e.report.id in ev.decisions and e.report.id not in ev.withdrawn
                  and ev.decisions[e.report.id].record.outcome.value == "admissible"]
        for e in direct:
            rp = e.report
            out.append(f"- {names[rp.id]}: cue **{rp.cue.value}**, value {getattr(rp.proposition, 'value', None)!r}, target {names.get(rp.target, rp.target)}")
        out.append("\n### Kernel conclusion and policy action\n")
        ans = mem.query(Query(key=key, valid_at=None if dp.get("valid_at") is None else ps.day(dp["valid_at"]),
                              profile=Profile.OPEN_WORLD))
        assert isinstance(ans, Resolved)
        form = ans.assertion.form if ans.assertion is not None else None
        out.append(f"- kernel_status: **{ans.kernel_status.value}**; alternatives: "
                   f"{[type(c.form).__name__ + '(' + str(getattr(c.form, 'value', getattr(c.form, 'values', ''))) + ')' for c in ans.alternatives]}")
        out.append(f"- decision: **{ans.decision.value}**; assertion: {None if form is None else getattr(form, 'value', form)!r}; "
                   f"provenance: {[[names.get(i, i) for i in s.environment] for s in ans.provenance]}")
        out.append(f"- gold (default): {dp['gold']['action']} {dp['gold'].get('value')!r}")
        for prof, g in dp.get("gold_by_profile", {}).items():
            out.append(f"- gold (`{prof}`): {g['action']} {g.get('value')!r} (rationale: {g['rationale']})")
    mem.close()
    return out


def clock_day(e: object) -> int:
    return (e.recorded_at.date() - ps.day(0).date()).days  # type: ignore[attr-defined,no-any-return]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--system", default="justified")
    a = ap.parse_args(argv)
    print("\n".join(trace(a.scenario, a.system)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
