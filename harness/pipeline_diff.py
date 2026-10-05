"""Pipeline differential (Lane M): the FULL pipeline vs the frozen oracle gold (G1 parity through ``Memory``).

``harness.kernel_diff`` checks the kernel alone with a stub admission. This harness checks everything that ships:
for every frozen Setting 1 stream it

  1. converts the study stream (``harness.convert``) and appends it, report by report, through
     :class:`palimem.memory.Memory` onto a **fresh backend** (the in-memory reference and SQLite, both),
     with the real admission stage (:class:`palimem.compat.CompatAdmitter`), the real kernel and the real store;
  2. answers every query from the stored belief versions at the query's log position (``belief_as_of`` = LSN),
     so a historical question is a lookup, never a replay;
  3. projects the v2 ``Resolved`` onto the v1 contract with :mod:`palimem.compat.revise_stream_v1`; and
  4. compares status, assertion and alternatives with ``s1_NNNN.gold.json``.

The paper's source-level retraction has no counterpart in the contract; it is carried in the log as a compat-only
marker report (see the compat module). ``--source-retract expand`` uses the contract-expressible per-report withdraws
instead and is expected to leave the known ``source-retract:late-assert`` gap.

Two slots have no v2 query form and are answered from the audit paths of ``Memory`` (kernel replay over the admitted
evidence at the snapshot, not from stored segments): the yes/no slots (they need the set of admissible
interpretations, which a belief version does not carry) and ``reported``.

Provenance is informational and not yet comparable: ``Resolved.provenance`` is empty until per-candidate supports land
(T-B4), and the frozen gold files carry ``provenance`` only for ``reported`` queries (the study's oracle computes the rest at
scoring time). The harness therefore compares provenance where the gold has it and reports the count; the interim v1
provenance it derives for base-key value slots has nothing to be compared with. It is not part of the pass criterion.

Exit codes: 0 all agree · 1 disagreement · 2 setup error. ``--inject-bug`` is the self-test.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from harness import frozen, study
from harness.convert import Converted, to_converted
from harness.differential import signature
from palimem.admission import Admitter
from palimem.compat import (
    PROFILE,
    CompatAdmitter,
    answer_v1,
    change_from_of,
    compat_admission_config,
    compat_semantic,
    kernel_schema_with_marker,
    reported_v1,
    schema_from_kernel,
    source_retraction_report,
    with_change_from,
    yesno_v1,
)
from palimem.kernel import DerivedJustification, Justification, dt_of_day
from palimem.memory import Memory
from palimem.policy import JUSTIFIED
from palimem.store import Backend, InMemoryBackend, SQLiteBackend
from palimem.types import Cue, Key, LogEntry, Query, Resolved, ResourceLimited

STREAM_RE = re.compile(r"^s1_\d+\.json$")
BACKENDS = ("memory", "sqlite")
INJECTIONS = ("none", "mutate-answer", "no-source-retraction", "self-update")
VALUE_SLOTS = ("current", "asof", "belief_asof", "downstream")


class _Clock:
    """The log's clock: the harness sets it to the study report day before each append, so ``recorded_at`` (the day
    anchor of a report with no valid-time cue) equals the study's ``t_rep``."""

    def __init__(self) -> None:
        self.now = dt_of_day(0)

    def __call__(self) -> datetime:
        return self.now


@dataclass
class Ids:
    assigned: dict[str, str] = field(default_factory=dict)  # converter ulid -> store-assigned ulid
    study_of: dict[str, str] = field(default_factory=dict)  # store-assigned ulid -> study observation id


def stream_names(frozen_dir: Path, limit: int | None = None, stride: int = 1) -> list[str]:
    names = sorted(p.name for p in frozen_dir.iterdir() if STREAM_RE.match(p.name))[::stride]
    return names[:limit] if limit else names


def make_backend(kind: str, clock: _Clock) -> Backend:
    if kind == "memory":
        return InMemoryBackend(clock=clock)
    if kind == "sqlite":
        return SQLiteBackend(":memory:", clock=clock)
    raise ValueError(kind)


def build_memory(conv: Converted, backend: Backend, inject: str) -> Memory:
    ks = kernel_schema_with_marker(conv.kschema)
    cls: type[Admitter] = Admitter if inject == "no-source-retraction" else CompatAdmitter
    return Memory(
        backend, schema_from_kernel(ks), kernel_schema=ks, entities=conv.kschema.entities,
        semantic=compat_semantic(self_update=inject == "self-update"),
        admission=compat_admission_config(), policy=JUSTIFIED, change_from_of=change_from_of, admitter_class=cls,
    )


def replay(conv: Converted, mem: Memory, clock: _Clock) -> Ids:
    """Append the converted stream through ``Memory``, one study position per append (LSN = arrival index)."""
    ids = Ids()
    by_lsn = {e.lsn: e for e in conv.entries}
    retract = dict(conv.source_retractions)
    for lsn in range(1, len(conv.arrival_ts) + 1):
        clock.now = conv.arrival_ts[lsn - 1]
        entry: LogEntry | None = by_lsn.get(lsn)
        if entry is not None:
            r = entry.report
            rep = replace(r, id=None, target=ids.assigned[r.target] if r.target else None)
            if r.cue is Cue.CHANGE and r.id in conv.change_from:
                rep = with_change_from(rep, conv.change_from[r.id])
        else:
            rep = source_retraction_report(retract[lsn])
        res = mem.append(rep, idempotency_key=f"{conv.stream_id}:{lsn}")
        assert res.entry is not None and res.entry.lsn == lsn, (lsn, res.entry)
        if entry is not None and entry.report.id is not None:
            rid = res.entry.report.id
            assert rid is not None
            ids.assigned[entry.report.id] = rid
            if entry.report.id in conv.study_id:
                ids.study_of[rid] = conv.study_id[entry.report.id]
    return ids


def _provenance(mem: Memory, key: Key, lsn: int, ans: dict[str, Any], ids: Ids) -> list[str]:
    """Interim v1 provenance for a base key (the paper's rule): all admitted ids unless the answer is established,
    then those asserting an answered value (all ids if none match)."""
    es = mem.evidence(key, lsn).direct
    every = sorted(ids.study_of[e.report.id or ""] for e in es)
    if ans["status"] != "established":
        return every
    a = ans["assertion"]
    vals = set(a) if isinstance(a, list) else ({a} if a is not None else set())
    picked = sorted(
        ids.study_of[e.report.id or ""] for e in es
        if getattr(e.report.proposition, "value", None) in vals
    )
    return picked or every


def answer_query(mem: Memory, conv: Converted, ids: Ids, q: Any, counters: Counter[str]) -> tuple[dict[str, Any], list[str] | None]:
    """The v1 answer of one study query from the pipeline, plus interim provenance (``None`` where not computed)."""
    ks = mem.pipeline.kernel_schema(mem.pipeline.evaluate(0))
    if q.slot == "reported":
        lsn = conv.lsn_at_day(q.tau)
        key = Key(entity=q.entity, attr=q.attr)
        ans = reported_v1(mem.evidence(key, lsn).direct, lambda rid: ids.study_of[rid])
        return ans, sorted(x for v in ans["assertion"].values() for x in v)
    if q.slot == "yesno":
        p = q.prop
        lsn = conv.lsn_at_day(q.tau)
        if p["kind"] == "erroneous":
            u = ids.assigned[conv.ulid_of[p["obs"]]]
            admitted = mem.admitted(lsn)
            if u not in admitted:
                return yesno_v1([]), []
            j = mem.justification(admitted[u].report.key, lsn)
            assert isinstance(j, Justification)
            return yesno_v1(j.erroneous_truths(u)), [p["obs"]]
        key = Key(entity=p["entity"], attr=p["attr"])
        j2 = mem.justification(key, lsn)
        if p["kind"] == "changed":
            assert isinstance(j2, Justification)
            return yesno_v1(j2.changed_truths(p["from"], p["to"])), None
        assert isinstance(j2, Justification | DerivedJustification)
        return yesno_v1(j2.holds_truths(p["value"], p.get("t", q.tau))), None
    if q.slot == "belief_asof":
        lsn, t = conv.lsn_at_day(q.tau_prime), q.tau_prime
    elif q.slot == "asof":
        lsn, t = conv.lsn_at_day(q.tau), q.t
    else:  # current, downstream
        lsn, t = conv.lsn_at_day(q.tau), (q.t if q.t is not None else q.tau)
    key = Key(entity=q.entity, attr=q.attr)
    spec = ks.spec(q.attr)
    ans2 = mem.query(Query(key=key, valid_at=dt_of_day(t), belief_as_of=lsn, profile=PROFILE))
    if isinstance(ans2, ResourceLimited):
        counters["resource_limited"] += 1
        return {"status": "resource_limited", "assertion": None, "alternatives": []}, None
    assert isinstance(ans2, Resolved)
    v1 = answer_v1(ans2, multi=spec.cardinality == "multi")
    prov = None if spec.derived else _provenance(mem, key, lsn, v1, ids)
    return v1, prov


def _mutate(ans: dict[str, Any]) -> dict[str, Any]:
    out = dict(ans)
    out["status"] = "unresolved" if ans["status"] == "established" else "established"
    out["assertion"] = None if ans["status"] == "established" else ans.get("assertion")
    return out


def run(
    frozen_dir: Path, st: Any, *, limit: int | None = None, stride: int = 1, inject: str = "none",
    backends: tuple[str, ...] = BACKENDS, source_retract: str = "sidetable", manifest: dict[str, Any] | None = None,
    max_examples: int = 10, progress: bool = False,
) -> dict[str, Any]:
    if inject not in INJECTIONS:
        raise ValueError(f"unknown injection {inject!r}")
    manifest = manifest or frozen.load_manifest()
    names = stream_names(frozen_dir, limit, stride)
    if not names:
        raise frozen.FrozenError(f"no frozen streams found in {frozen_dir}")
    needed = [n for name in names for n in (name, name.replace(".json", ".gold.json"))]
    frozen.require_valid(frozen_dir, manifest, only=needed)
    t0 = time.time()
    out_by_backend: dict[str, dict[str, Any]] = {}
    mutated = False
    for kind in backends:
        per_slot: dict[str, Counter[str]] = {}
        totals: Counter[str] = Counter()
        classes: Counter[str] = Counter()
        examples: list[dict[str, Any]] = []
        for i, name in enumerate(names):
            stream = st.load_stream(str(frozen_dir / name))
            gold = json.loads((frozen_dir / name.replace(".json", ".gold.json")).read_text())
            conv = to_converted(stream, source_retract)
            clock = _Clock()
            backend = make_backend(kind, clock)
            try:
                mem = build_memory(conv, backend, inject)
                ids = replay(conv, mem, clock)
                for q in sorted(stream.queries, key=lambda q: (q.tau, q.id)):
                    slot_key = q.slot + (":" + q.prop["kind"] if q.slot == "yesno" else "")
                    c = per_slot.setdefault(slot_key, Counter())
                    ans, prov = answer_query(mem, conv, ids, q, totals)
                    if inject == "mutate-answer" and not mutated and ans["status"] == "established":
                        ans, mutated = _mutate(ans), True
                    c["queries"] += 1
                    totals["queries"] += 1
                    g = gold[q.id]
                    if prov is not None and "provenance" in g:
                        totals["provenance_compared"] += 1
                        if sorted(set(prov)) != sorted(set(g["provenance"])):
                            totals["provenance_differs"] += 1
                    if signature(ans, st.norm) != signature(g, st.norm):
                        c["disagreements"] += 1
                        totals["disagreements"] += 1
                        kind_ = "derived" if stream.attributes.get(q.attr or "") and stream.attributes[q.attr].derived else "base"
                        classes[f"{slot_key}/{kind_}: gold={g['status']} pipeline={ans['status']}"] += 1
                        if len(examples) < max_examples:
                            examples.append({
                                "backend": kind, "stream": name, "query": q.id, "slot": slot_key,
                                "pipeline": {k: ans.get(k) for k in ("status", "assertion", "alternatives")},
                                "gold": {k: g.get(k) for k in ("status", "assertion", "alternatives")},
                            })
                totals["streams"] += 1
                totals["appends"] += len(conv.arrival_ts)
            finally:
                backend.close()
            if progress and (i + 1) % 25 == 0:
                print(f"  [{kind}] {i + 1}/{len(names)} streams, {totals['disagreements']} disagreements, {time.time() - t0:.0f}s", flush=True)
        out_by_backend[kind] = {
            "streams": totals["streams"], "queries": totals["queries"], "appends": totals["appends"],
            "disagreements": totals["disagreements"], "resource_limited": totals["resource_limited"],
            "provenance_compared": totals["provenance_compared"], "provenance_differs": totals["provenance_differs"],
            "per_slot": {k: dict(v) for k, v in sorted(per_slot.items())},
            "disagreement_classes": dict(classes.most_common()), "examples": examples,
        }
    bad = sum(b["disagreements"] + b["resource_limited"] for b in out_by_backend.values())
    return {
        "backends": out_by_backend, "inject_bug": inject, "source_retract": source_retract, "profile": "revise-stream-v1",
        "policy": "P0c", "seconds": round(time.time() - t0, 1), "frozen_files_verified": len(needed),
        "study_commit": st.commit, "passed": bad == 0,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness.pipeline_diff", description=__doc__.split("\n\n")[0])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--inject-bug", choices=INJECTIONS, default="none")
    ap.add_argument("--backend", choices=("memory", "sqlite", "both"), default="both")
    ap.add_argument("--source-retract", choices=("sidetable", "expand"), default="sidetable")
    ap.add_argument("--study-dir")
    ap.add_argument("--frozen-dir")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--max-examples", type=int, default=10)
    a = ap.parse_args(argv)
    try:
        st = study.load(a.study_dir)
        frozen_dir = frozen.locate_frozen(st.dir, a.frozen_dir, a.fetch)
        backends = BACKENDS if a.backend == "both" else (a.backend,)
        report = run(frozen_dir, st, limit=a.limit, stride=a.stride, inject=a.inject_bug, backends=backends,
                     source_retract=a.source_retract, max_examples=a.max_examples, progress=True)
    except (FileNotFoundError, frozen.FrozenError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=1) + "\n")
    print(f"{'PASS' if report['passed'] else 'FAIL'}: pipeline vs gold [source-retract={report['source_retract']}, "
          f"inject={report['inject_bug']}] {report['seconds']}s")
    for kind, b in report["backends"].items():
        print(f"  backend {kind}: {b['streams']} streams, {b['queries']} queries, {b['appends']} appends, "
              f"{b['disagreements']} disagreements, {b['resource_limited']} resource-limited; "
              f"interim provenance differs {b['provenance_differs']}/{b['provenance_compared']}")
        for slot, c in b["per_slot"].items():
            print(f"    {slot:18s} {c['queries']:6d} queries  {c.get('disagreements', 0):5d} disagreements")
        for cls, n in list(b["disagreement_classes"].items())[:8]:
            print(f"    class: {cls}  x{n}")
        for ex in b["examples"][:3]:
            print("    e.g.", json.dumps(ex)[:300])
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
