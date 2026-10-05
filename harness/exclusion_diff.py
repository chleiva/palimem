"""The product's ``exclude_source`` against the study's source-level retraction (author ruling 2 of 2026-10-05).

The paper's source retraction (``retract`` whose target is a *source* id) removes every assertion of that source, those
made before the retraction and those made after it (106 frozen streams, 241 assertions made after). The contract has no
source-scope ``withdraw``, so the per-report expansion leaves the 85-query ``source-retract:late-assert`` gap
(``harness.pipeline_diff --source-retract expand``). The product answers the same need with an *admission operation*:
``exclude_source(source, from_lsn=1, reason)`` (:mod:`palimem.admission.exclusion`).

This script replays every frozen Setting 1 stream through ``Memory`` under the ``revise-stream-v1`` configuration with the
study's retraction recorded as that operation instead of the compat marker, and compares every query with the study's gold
exactly as ``pipeline_diff`` does (same builder, same comparison; only the admitter and the retraction record differ).
Target: 0 disagreements, i.e. the product operation closes the gap that per-report withdraws cannot.

    python -m harness.exclusion_diff [--limit N] [--stride K] [--backend memory|sqlite|both] [--inject-bug forward-only|none]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from palimem.admission import (
    ExclusionAdmitter,
    exclude_source,
    exclusion_attr_spec,
)
from palimem.admission.exclusion import SOURCE_EXCLUSION_ATTR
from palimem.compat import (
    compat_admission_config,
    compat_semantic,
    kernel_schema_with_marker,
    schema_from_kernel,
    with_change_from,
)
from palimem.compat.revise_stream_v1 import change_from_of
from palimem.kernel import KernelSchema
from palimem.memory import Memory
from palimem.policy import JUSTIFIED
from palimem.store import Backend
from palimem.types import Cue, LogEntry

from . import frozen, study
from . import pipeline_diff as pd
from .convert import Converted

REASON = "study source retraction, replayed as the product's admission operation"
INJECTIONS = ("none", "forward-only")


def build_memory(conv: Converted, backend: Backend, inject: str) -> Memory:
    ks = kernel_schema_with_marker(conv.kschema)
    attrs = dict(ks.attrs)
    attrs[SOURCE_EXCLUSION_ATTR] = exclusion_attr_spec()  # type: ignore[assignment]
    ks = KernelSchema(attrs=attrs, rules=ks.rules, entities=ks.entities)
    return Memory(
        backend, schema_from_kernel(ks), kernel_schema=ks, entities=conv.kschema.entities,
        semantic=compat_semantic(self_update=False), admission=compat_admission_config(), policy=JUSTIFIED,
        change_from_of=change_from_of, admitter_class=ExclusionAdmitter,
    )


def replay(conv: Converted, mem: Memory, clock: Any, inject: str = "none") -> pd.Ids:
    """As ``pipeline_diff.replay``, but a study source retraction is recorded with ``exclude_source``."""
    ids = pd.Ids()
    by_lsn = {e.lsn: e for e in conv.entries}
    retract = dict(conv.source_retractions)
    for lsn in range(1, len(conv.arrival_ts) + 1):
        clock.now = conv.arrival_ts[lsn - 1]
        entry: LogEntry | None = by_lsn.get(lsn)
        if entry is None:
            # the paper's retraction removes the source's assertions from the start of the log, so from_lsn = 1
            # (the injected bug excludes only from the decision on, so the source's earlier assertions are still heard)
            res = exclude_source(mem, retract[lsn], lsn if inject == "forward-only" else 1, REASON, idempotency_key=f"{conv.stream_id}:{lsn}")
            assert res.entry is not None and res.entry.lsn == lsn, (lsn, res.entry)  # type: ignore[attr-defined]
            continue
        r = entry.report
        rep = replace(r, id=None, target=ids.assigned[r.target] if r.target else None)
        if r.cue is Cue.CHANGE and r.id in conv.change_from:
            rep = with_change_from(rep, conv.change_from[r.id])
        res = mem.append(rep, idempotency_key=f"{conv.stream_id}:{lsn}")
        assert res.entry is not None and res.entry.lsn == lsn, (lsn, res.entry)
        if entry.report.id is not None:
            rid = res.entry.report.id
            assert rid is not None
            ids.assigned[entry.report.id] = rid
            if entry.report.id in conv.study_id:
                ids.study_of[rid] = conv.study_id[entry.report.id]
    return ids


def run(frozen_dir: Path, st: Any, *, limit: int | None, stride: int, inject: str, backends: tuple[str, ...]) -> dict[str, Any]:
    if inject not in INJECTIONS:
        raise ValueError(f"unknown injection {inject!r}")
    saved = (pd.build_memory, pd.replay)
    pd.build_memory = build_memory  # type: ignore[assignment]
    pd.replay = lambda conv, mem, clock: replay(conv, mem, clock, inject)  # type: ignore[assignment]
    try:
        return pd.run(frozen_dir, st, limit=limit, stride=stride, inject="none", backends=backends, source_retract="sidetable", progress=True)
    finally:
        pd.build_memory, pd.replay = saved


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness.exclusion_diff", description=__doc__.split("\n\n")[0])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--backend", choices=("memory", "sqlite", "both"), default="memory")
    ap.add_argument("--inject-bug", choices=INJECTIONS, default="none")
    ap.add_argument("--study-dir")
    ap.add_argument("--frozen-dir")
    ap.add_argument("--fetch", action="store_true")
    a = ap.parse_args(argv)
    try:
        st = study.load(a.study_dir)
        frozen_dir = frozen.locate_frozen(st.dir, a.frozen_dir, a.fetch)
        backends = pd.BACKENDS if a.backend == "both" else (a.backend,)
        report = run(frozen_dir, st, limit=a.limit, stride=a.stride, inject=a.inject_bug, backends=backends)
    except (FileNotFoundError, frozen.FrozenError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"{'PASS' if report['passed'] else 'FAIL'}: exclude_source vs gold [inject={a.inject_bug}] {report['seconds']}s")
    for kind, b in report["backends"].items():
        print(f"  backend {kind}: {b['streams']} streams, {b['queries']} queries, {b['appends']} appends, {b['disagreements']} disagreements")
        for slot, c in b["per_slot"].items():
            print(f"    {slot:<22} {c['queries']:>6} queries {c.get('disagreements', 0):>5} disagreements")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
