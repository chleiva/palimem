"""Kernel differential (Lane B): the palimem kernel vs the frozen oracle gold (G1 parity, kernel half).

For every query of every frozen Setting 1 stream the harness

  1. converts the study stream to palimem log entries (``harness.convert``),
  2. admits entries at the query's belief point with the compat-profile admission stub,
  3. justifies the queried key with ``palimem.kernel`` (``justify_key`` / ``justify_derived``) and reads the
     answer from the contract ``Segment`` containing the valid day (the product path),
  4. projects the segment onto the paper's v1 answer shape (status, assertion, alternatives) per the S-04
     per-slot-type profile table, and
  5. compares it with ``s1_NNNN.gold.json``.

Any difference in status, assertion or alternatives fails the run. Yes/no slots (``holds``, ``changed``,
``erroneous``) are answered from the kernel's truth sets, since the v2 ``Query`` has no yes/no form (the
adapter, T-E3, owns that projection).

Also checked, not gated by the gold: the segment path equals the candidate-family path at every queried
day (``segment_inconsistent``), and the static exactness check accepts every stream's schema.

``--provenance strict`` (T-B4, decision S-12) adds the provenance gate. The reference is the study's own
``eval.scorer.supporting_ids`` (the oracle's flat set; the frozen gold files only record provenance for
``reported`` queries). For every query the kernel's profile projection (``oracle_flat_ids``) must equal it
exactly, per slot type: any difference fails the run. Also reported, never gating: how the product's
principled rule (``flatten`` of the segment's subset-minimal environments) relates to the oracle's set,
classified by slot, key kind and status, and ``support_inconsistent`` (the environments' worlds must equal
the candidate family at every queried day).

Exit codes: 0 all agree · 1 disagreement · 2 setup error. ``--inject-bug`` is the self-test.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from harness import frozen, study
from harness.convert import CompatAdmission, Converted, to_converted
from harness.differential import signature
from palimem.kernel import (
    DerivedJustification,
    Justification,
    KernelSchema,
    ResourceLimitedResult,
    check_schema,
    classify,
    justify_derived,
    justify_key,
)
from palimem.kernel.derive import Provider
from palimem.kernel.justify import Family
from palimem.kernel.provenance import Dist, EnvBudget
from palimem.types import (
    Cue,
    EmptyForm,
    KernelStatus,
    Key,
    Profile,
    SemanticConfig,
    SetForm,
    ValueForm,
)
from palimem.types import Segment as PSegment

STREAM_RE = re.compile(r"^s1_\d+\.json$")
INJECTIONS = ("none", "ignore-corrections", "self-update", "mutate-answer", "drop-provenance")
SEMANTIC = SemanticConfig(self_update=False, profile=Profile.REVISE_STREAM_V1)  # the study's P0c
SEMANTIC_SU = SemanticConfig(self_update=True, profile=Profile.REVISE_STREAM_V1)  # P0cSU: wrong for P0c gold


def stream_names(frozen_dir: Path, limit: int | None = None, stride: int = 1) -> list[str]:
    names = sorted(p.name for p in frozen_dir.iterdir() if STREAM_RE.match(p.name))[::stride]
    return names[:limit] if limit else names


# ---- segment -> study answer shape (S-04 per-slot-type table)


def _val(f: ValueForm | SetForm | EmptyForm, multi: bool) -> Any:
    if isinstance(f, EmptyForm):
        return [] if multi else None
    if isinstance(f, ValueForm):
        return f.value
    return sorted(f.values, key=str)


def segment_answer(seg: PSegment, multi: bool) -> dict[str, Any]:
    st = seg.kernel_status
    if st is KernelStatus.UNKNOWN:
        return {"status": "unknown", "assertion": None, "alternatives": []}
    if st is KernelStatus.UNRESOLVED:
        return {"status": "unresolved", "assertion": None,
                "alternatives": [_val(c.form, multi) for c in seg.alternatives]}  # type: ignore[arg-type]
    assert seg.established is not None
    return {"status": "established", "assertion": _val(seg.established.form, multi), "alternatives": []}  # type: ignore[arg-type]


def yesno_answer(truths: list[bool]) -> dict[str, Any]:
    if truths and all(truths):
        return {"status": "established", "assertion": True, "alternatives": []}
    if truths and not any(truths):
        return {"status": "established", "assertion": False, "alternatives": []}
    if not truths:
        return {"status": "unknown", "assertion": None, "alternatives": []}
    return {"status": "possible", "assertion": True, "alternatives": []}


# ---- per-stream evaluation


class _Lazy:
    """Provider that justifies base keys on demand at one belief point."""

    def __init__(self, ev: StreamEval, lsn: int) -> None:
        self.ev, self.lsn = ev, lsn

    def candidates(self, key: Key, t: int) -> Family:
        return self.ev.base(self.lsn, key).candidates_at(t)

    def breakpoints(self, key: Key) -> frozenset[int]:
        return self.ev.base(self.lsn, key).breakpoints()

    # SupportProvider (explanations, T-B4)
    def world_envs(self, key: Key, t: int, budget: EnvBudget) -> Dist:
        return self.ev.base(self.lsn, key).world_envs_at(t, budget=budget)

    def reports(self, key: Key) -> list[tuple[str, Any]]:
        return [(e.id, e.value) for e in self.ev.base(self.lsn, key).evidence]


class StreamEval:
    def __init__(self, conv: Converted, inject: str = "none", live_corrections: bool = False) -> None:
        self.conv = conv
        self.adm = CompatAdmission(conv, acting_reports_must_be_live=live_corrections)
        self.inject = inject
        self._base: dict[tuple[int, Key], Justification] = {}
        self._derived: dict[tuple[int, Key], DerivedJustification] = {}
        self.relax: Counter[int] = Counter()
        self.resource_limited = 0

    def base(self, lsn: int, key: Key) -> Justification:
        hit = self._base.get((lsn, key))
        if hit is not None:
            return hit
        entries = self.adm.admitted_by_key(lsn).get(key, [])
        if self.inject == "ignore-corrections":
            entries = [
                dataclasses.replace(e, report=dataclasses.replace(e.report, cue=Cue.ASSERT, target=None))
                if e.report.cue is Cue.CORRECT else e
                for e in entries
            ]
        sem = SEMANTIC_SU if self.inject == "self-update" else SEMANTIC
        j = justify_key(self.conv.kschema, key, entries, sem)
        if isinstance(j, ResourceLimitedResult):
            self.resource_limited += 1
            raise RuntimeError(f"resource limited: {j.detail}")  # noqa: TRY004
        self.relax[j.relax_level] += 1
        self._base[(lsn, key)] = j
        return j

    def justification(self, lsn: int, key: Key) -> Justification | DerivedJustification:
        if not self.conv.kschema.spec(key.attr).derived:
            return self.base(lsn, key)
        hit = self._derived.get((lsn, key))
        if hit is None:
            prov: Provider = _Lazy(self, lsn)
            hit = justify_derived(self.conv.kschema, key, prov, SEMANTIC)
            self._derived[(lsn, key)] = hit
        return hit


def answer_query(ev: StreamEval, q: Any, counters: Counter[str]) -> dict[str, Any]:
    conv = ev.conv
    ks: KernelSchema = conv.kschema
    if q.slot == "reported":
        lsn = conv.lsn_at_day(q.tau)
        key = Key(entity=q.entity, attr=q.attr)
        rep: dict[str, list[str]] = {}
        for e in ev.adm.admitted_by_key(lsn).get(key, []):
            p = e.report.proposition
            assert p is not None and hasattr(p, "value")
            rep.setdefault(str(p.value), []).append(conv.study_id[e.report.id or ""])
        return {"status": "established", "assertion": {k: sorted(v) for k, v in sorted(rep.items())}, "alternatives": []}
    if q.slot == "yesno":
        p = q.prop
        lsn = conv.lsn_at_day(q.tau)
        if p["kind"] == "erroneous":
            u = conv.ulid_of[p["obs"]]
            admitted = {e.report.id: e for es in ev.adm.admitted_by_key(lsn).values() for e in es}
            if u not in admitted:
                return yesno_answer([])
            j = ev.base(lsn, admitted[u].report.key)
            return yesno_answer(j.erroneous_truths(u))
        key = Key(entity=p["entity"], attr=p["attr"])
        j2 = ev.justification(lsn, key)
        if p["kind"] == "changed":
            assert isinstance(j2, Justification)
            return yesno_answer(j2.changed_truths(p["from"], p["to"]))
        return yesno_answer(j2.holds_truths(p["value"], p.get("t", q.tau)))
    # value slots
    if q.slot == "belief_asof":
        lsn, t = conv.lsn_at_day(q.tau_prime), q.tau_prime
    elif q.slot == "asof":
        lsn, t = conv.lsn_at_day(q.tau), q.t
    else:  # current, downstream
        lsn, t = conv.lsn_at_day(q.tau), (q.t if q.t is not None else q.tau)
    key = Key(entity=q.entity, attr=q.attr)
    spec = ks.spec(q.attr)
    j3 = ev.justification(lsn, key)
    seg = j3.segment_at(t)
    st, est, alts = classify(key, spec, Profile.REVISE_STREAM_V1, j3.candidates_at(t))
    if (st, est, alts) != (seg.kernel_status, seg.established, seg.alternatives):
        counters["segment_inconsistent"] += 1
    return segment_answer(seg, spec.cardinality == "multi")


def segment_flat_ids(seg: PSegment) -> set[str]:
    """``flatten`` of a segment's per-candidate supports (the adapter projection of S-12)."""
    return {r for sl in seg.support.values() for s in sl for r in s.environment}


def provenance_query(
    ev: StreamEval, q: Any, counters: Counter[str]
) -> tuple[set[str], set[str] | None, tuple[Any, int] | None]:
    """Provenance of one query, as study observation ids: ``(profile set, principled flatten, target)``.

    The profile set is what the compat profile reports (the oracle's flat rule, reproduced from the kernel's
    own structures); the principled flatten is the product rule (``None`` for slots that are not about a
    segment: ``reported``, ``changed``, ``erroneous``, where the oracle's set is the key's admitted ids);
    ``target`` is the ``(justification, valid day)`` the segment slots read, for diagnostics.
    Slot handling mirrors ``eval.scorer.supporting_ids``."""
    conv = ev.conv

    def study(ids: Any) -> set[str]:
        return {conv.study_id.get(i, i) for i in ids}

    def key_ids(lsn: int, key: Key) -> set[str]:
        return study(e.report.id for e in ev.adm.admitted_by_key(lsn).get(key, []) if e.report.id)

    if q.slot == "reported":
        return key_ids(conv.lsn_at_day(q.tau), Key(entity=q.entity, attr=q.attr)), None, None
    if q.slot == "yesno":
        p = q.prop
        lsn = conv.lsn_at_day(q.tau)
        if p["kind"] == "erroneous":
            u = conv.ulid_of[p["obs"]]
            for es in ev.adm.admitted_by_key(lsn).values():
                for e in es:
                    if e.report.id == u:
                        return key_ids(lsn, e.report.key), None, None
            return set(), None, None
        key = Key(entity=p["entity"], attr=p["attr"])
        if p["kind"] == "changed":
            return key_ids(lsn, key), None, None
        t = p.get("t", q.tau)
        j = ev.justification(lsn, key)
        return study(j.oracle_flat_ids(t)), study(segment_flat_ids(j.segment_at(t))), (j, t)
    if q.slot == "belief_asof":
        lsn, t = conv.lsn_at_day(q.tau_prime), q.tau_prime
    elif q.slot == "asof":
        lsn, t = conv.lsn_at_day(q.tau), q.t
    else:  # current, downstream
        lsn, t = conv.lsn_at_day(q.tau), (q.t if q.t is not None else q.tau)
    key = Key(entity=q.entity, attr=q.attr)
    j2 = ev.justification(lsn, key)
    if set(j2.world_envs_at(t)) != set(j2.candidates_at(t)):
        counters["support_inconsistent"] += 1
    return study(j2.oracle_flat_ids(t)), study(segment_flat_ids(j2.segment_at(t))), (j2, t)


def flat_variants(j: Any, t: int) -> dict[str, set[str]]:
    """Diagnostic flattenings of a justification's environments at ``t``: P = the product rule (subset-minimal
    environments of every candidate world); P_ne = the same without the empty world; N / N_ne = the same
    keeping every environment, not only the minimal ones (``EnvBudget(minimal=False)``)."""
    mini = j.world_envs_at(t, budget=EnvBudget())
    full = j.world_envs_at(t, budget=EnvBudget(cap=10**6, minimal=False))

    def fl(d: Dist, empty: bool) -> set[str]:
        return {r for w, es in d.items() if empty or w for e in es for r in e}

    return {"P": fl(mini, True), "P_ne": fl(mini, False), "N": fl(full, True), "N_ne": fl(full, False),
            "E": set(j.oracle_exception_ids(t)), "ERR": set(j.always_err_ids())}


def relation(principled: set[str], oracle: set[str]) -> str:
    if principled == oracle:
        return "equal"
    if principled < oracle:
        return "subset"
    if principled > oracle:
        return "superset"
    return "overlap" if principled & oracle else "disjoint"


def _mutate(ans: dict[str, Any]) -> dict[str, Any]:
    out = dict(ans)
    out["status"] = "unresolved" if ans["status"] == "established" else "established"
    out["assertion"] = None if ans["status"] == "established" else ans.get("assertion")
    return out


KNOWN_GAP = "source-retract:late-assert"


def run(frozen_dir: Path, st: Any, limit: int | None = None, stride: int = 1, inject: str = "none",
        manifest: dict[str, Any] | None = None, max_examples: int = 10, progress: bool = False,
        source_retract: str = "expand", live_corrections: bool = False, provenance: bool = False) -> dict[str, Any]:
    """``source_retract`` is how the paper's source-level retraction is represented (see
    ``harness.convert.to_converted``). Under ``expand`` the contract has no source-scope withdraw, so
    assertions a source makes *after* its retraction stay admitted, unlike the paper. A disagreement is
    classed ``source-retract:late-assert`` only when re-running the same query with the exact (sidetable)
    representation reproduces the gold; every other disagreement is ``other`` and fails the run."""
    if inject not in INJECTIONS:
        raise ValueError(f"unknown injection {inject!r}")
    manifest = manifest or frozen.load_manifest()
    names = stream_names(frozen_dir, limit, stride)
    if not names:
        raise frozen.FrozenError(f"no frozen streams found in {frozen_dir}")
    needed = [n for name in names for n in (name, name.replace(".json", ".gold.json"))]
    frozen.require_valid(frozen_dir, manifest, only=needed)
    t0 = time.time()
    per_slot: dict[str, Counter[str]] = {}
    totals: Counter[str] = Counter()
    classes: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []
    prov_classes: Counter[str] = Counter()
    prov_relation: Counter[str] = Counter()
    prov_examples: list[dict[str, Any]] = []
    prov_cause: Counter[str] = Counter()
    prov_unexplained: list[dict[str, Any]] = []
    mutated = False
    for i, name in enumerate(names):
        stream = st.load_stream(str(frozen_dir / name))
        gold = json.loads((frozen_dir / name.replace(".json", ".gold.json")).read_text())
        conv = to_converted(stream, source_retract)
        check_schema(conv.kschema)
        totals["cross_key_corrections"] += conv.stats["cross_key_corrections"]
        totals["source_retract_expanded_withdraws"] += conv.stats["source_retract_expanded_withdraws"]
        ev = StreamEval(conv, inject, live_corrections)
        alt: StreamEval | None = None
        for q in sorted(stream.queries, key=lambda q: (q.tau, q.id)):
            slot_key = q.slot + (":" + q.prop["kind"] if q.slot == "yesno" else "")
            c = per_slot.setdefault(slot_key, Counter())
            ans = answer_query(ev, q, totals)
            if inject == "mutate-answer" and not mutated and ans["status"] == "established":
                ans, mutated = _mutate(ans), True
            c["queries"] += 1
            totals["queries"] += 1
            if signature(ans, st.norm) != signature(gold[q.id], st.norm):
                cause = "other"
                if inject == "none" and source_retract == "expand" and conv.stats["source_retractions"]:
                    alt = alt or StreamEval(to_converted(stream, "sidetable"), "none", live_corrections)
                    if signature(answer_query(alt, q, Counter()), st.norm) == signature(gold[q.id], st.norm):
                        cause = KNOWN_GAP
                c["disagreements"] += 1
                totals["disagreements"] += 1
                totals["by_cause:" + cause] += 1
                kind = "derived" if stream.attributes.get(q.attr or "", None) and stream.attributes[q.attr].derived else "base"
                classes[f"{cause}/{slot_key}/{kind}: gold={gold[q.id]['status']} kernel={ans['status']}"] += 1
                if len(examples) < max_examples:
                    examples.append({"stream": name, "query": q.id, "slot": slot_key,
                                     "kernel": {k: ans.get(k) for k in ("status", "assertion", "alternatives")},
                                     "gold": {k: gold[q.id].get(k) for k in ("status", "assertion", "alternatives")}})
            if provenance:
                attr = q.attr or (q.prop or {}).get("attr")  # erroneous yes/no names an observation, not an attribute
                pkind = "derived" if attr is not None and stream.attributes[attr].derived else "base"
                compat, principled, target = provenance_query(ev, q, totals)
                if inject == "drop-provenance" and compat and not totals["prov_mutated"]:
                    compat, totals["prov_mutated"] = set(sorted(compat)[1:]), 1
                oracle = set(st.supporting_ids(stream, q, gold[q.id]))
                c["prov_queries"] += 1
                totals["prov_queries"] += 1
                if compat != oracle:
                    c["prov_disagreements"] += 1
                    totals["prov_disagreements"] += 1
                    rel = relation(compat, oracle)
                    prov_classes[f"profile-{rel}/{slot_key}/{pkind}: gold={gold[q.id]['status']}"] += 1
                    if len(prov_examples) < max_examples:
                        prov_examples.append({"stream": name, "query": q.id, "slot": slot_key, "kernel": sorted(compat),
                                              "oracle": sorted(oracle)})
                if principled is not None:
                    rel_p = relation(principled, oracle)
                    prov_relation[f"{rel_p}/{slot_key}/{pkind}/{gold[q.id]['status']}"] += 1
                    cause = "equal"
                    if rel_p != "equal" and target is not None:
                        fv = {k: {conv.study_id.get(i, i) for i in v} for k, v in flat_variants(*target).items()}
                        # Why the oracle's flat set differs from the product's flatten. Every report the oracle lists
                        # that the product does not is put in exactly one category; anything left is UNEXPLAINED.
                        p_ids = fv["P"]
                        if pkind == "derived":  # erroneous-in-every-interpretation, over every base key at this belief point
                            lsn_q = conv.lsn_at_day(q.tau_prime if q.slot == "belief_asof" else q.tau)
                            fv["ERR"] = {conv.study_id.get(i, i) for k in ev.adm.admitted_by_key(lsn_q)
                                         for i in ev.base(lsn_q, k).always_err_ids()}
                        rest = set(oracle - p_ids)
                        cats: set[str] = set()
                        for cat, pool in (("redundant-supporters", fv["N_ne"]),
                                          ("exception-evidence-of-other-worlds", fv["E"]),
                                          ("erroneous-in-every-interpretation", fv["ERR"])):
                            hit = rest & pool
                            if hit:
                                cats.add("oracle-lists-" + cat)
                                rest -= hit
                        if rest:  # not erroneous everywhere, so TRUE in some interpretation, but in no environment of a
                            # candidate containing its value: it only supports interpretations yielding other worlds
                            cats.add("oracle-lists-reports-of-other-intervals-or-worlds")
                            rest = set()
                        if p_ids - oracle:
                            cats.add("product-lists-empty-alternative")
                            if not (p_ids - oracle) <= (fv["P"] - fv["P_ne"]):
                                rest.add("<product-only-nonempty>")
                        cause = ("explained:" + "+".join(sorted(cats))) if not rest and cats else "UNEXPLAINED"
                        if cause == "UNEXPLAINED" and len(prov_unexplained) < max_examples:
                            prov_unexplained.append({"stream": name, "query": q.id, "slot": slot_key, "kind": pkind,
                                                     "oracle": sorted(oracle),
                                                     **{k: sorted(v) for k, v in fv.items()}})
                    prov_cause[f"{rel_p}|{cause}|{pkind}"] += 1
        totals["streams"] += 1
        totals["relax_events"] += sum(n for lv, n in ev.relax.items() if lv)
        totals["resource_limited"] += ev.resource_limited
        if progress and (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(names)} streams, {totals['disagreements']} disagreements, {time.time() - t0:.0f}s", flush=True)
    return {
        "streams": totals["streams"], "queries": totals["queries"], "disagreements": totals["disagreements"],
        "segment_inconsistent": totals["segment_inconsistent"], "cross_key_corrections": totals["cross_key_corrections"],
        "relax_events": totals["relax_events"], "resource_limited": totals["resource_limited"],
        "per_slot": {k: dict(v) for k, v in sorted(per_slot.items())},
        "disagreement_classes": dict(classes.most_common()),
        "seconds": round(time.time() - t0, 1),
        "unexplained": totals["by_cause:other"],
        "explained_source_retract_gap": totals["by_cause:" + KNOWN_GAP],
        "source_retract_expanded_withdraws": totals["source_retract_expanded_withdraws"],
        "source_retract": source_retract, "live_corrections": live_corrections,
        "provenance": {
            "enabled": provenance,
            "queries": totals["prov_queries"],
            "disagreements": totals["prov_disagreements"],
            "support_inconsistent": totals["support_inconsistent"],
            "per_slot": {k: {"queries": v["prov_queries"], "disagreements": v["prov_disagreements"]}
                         for k, v in sorted(per_slot.items()) if v["prov_queries"]},
            "disagreement_classes": dict(prov_classes.most_common()),
            "principled_vs_oracle": dict(sorted(prov_relation.items())),
            "principled_difference_causes": dict(sorted(prov_cause.items())),
            "principled_unexplained_examples": prov_unexplained,
            "examples": prov_examples,
        },
        "provenance_passed": (not provenance) or (totals["prov_disagreements"] == 0 and totals["support_inconsistent"] == 0),
        "passed": totals["by_cause:other"] == 0 and totals["segment_inconsistent"] == 0 and totals["resource_limited"] == 0
        and ((not provenance) or (totals["prov_disagreements"] == 0 and totals["support_inconsistent"] == 0)),
        "strict_passed": totals["disagreements"] == 0 and totals["segment_inconsistent"] == 0 and totals["resource_limited"] == 0
        and ((not provenance) or (totals["prov_disagreements"] == 0 and totals["support_inconsistent"] == 0)),
        "examples": examples, "inject_bug": inject, "policy": "P0c", "profile": "revise-stream-v1",
        "frozen_files_verified": len(needed), "study_commit": st.commit,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness.kernel_diff", description=__doc__.split("\n\n")[0])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--inject-bug", choices=INJECTIONS, default="none")
    ap.add_argument("--study-dir")
    ap.add_argument("--frozen-dir")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--source-retract", choices=("expand", "sidetable"), default="expand",
                    help="representation of source-level retraction (default: expand into per-report withdraws)")
    ap.add_argument("--live-corrections", action="store_true",
                    help="product semantics: only a live correction withdraws its target (the compat profile needs it off)")
    ap.add_argument("--provenance", choices=("off", "strict"), default="off",
                    help="strict: the kernel's profile provenance must equal the study's oracle set on every query (T-B4)")
    ap.add_argument("--strict", action="store_true", help="exit 1 on ANY disagreement, including the classified known gap")
    ap.add_argument("--out")
    ap.add_argument("--max-examples", type=int, default=10)
    a = ap.parse_args(argv)
    try:
        st = study.load(a.study_dir)
        frozen_dir = frozen.locate_frozen(st.dir, a.frozen_dir, a.fetch)
        report = run(frozen_dir, st, a.limit, a.stride, a.inject_bug, max_examples=a.max_examples, progress=True,
                     source_retract=a.source_retract, live_corrections=a.live_corrections,
                     provenance=a.provenance == "strict")
    except (FileNotFoundError, frozen.FrozenError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=1) + "\n")
    ok = report["strict_passed"] if a.strict else report["passed"]
    verdict = "PASS" if ok else "FAIL"
    print(f"{verdict}: {report['streams']} streams, {report['queries']} queries; kernel vs gold "
          f"{report['disagreements']} disagreements ({report['explained_source_retract_gap']} explained by the "
          f"source-retract representation gap, {report['unexplained']} unexplained) "
          f"[source-retract={report['source_retract']}, live-corrections={report['live_corrections']}]; "
          f"segment-vs-family {report['segment_inconsistent']}; "
          f"totality-ladder events {report['relax_events']}; {report['seconds']}s [inject={report['inject_bug']}]")
    for slot, c in report["per_slot"].items():
        prov = f"  {c.get('prov_disagreements', 0):5d} provenance disagreements" if report["provenance"]["enabled"] else ""
        print(f"  {slot:18s} {c['queries']:6d} queries  {c.get('disagreements', 0):5d} disagreements{prov}")
    if report["provenance"]["enabled"]:
        pv = report["provenance"]
        print(f"  provenance (profile vs study oracle set): {pv['queries']} queries, {pv['disagreements']} disagreements, "
              f"support-vs-family inconsistent {pv['support_inconsistent']}")
        for cls, n in list(pv["disagreement_classes"].items())[:8]:
            print(f"    provenance class: {cls}  x{n}")
        rel: dict[str, int] = {}
        for k, n in pv["principled_vs_oracle"].items():
            rel[k.split("/")[0]] = rel.get(k.split("/")[0], 0) + n
        print(f"  principled flatten vs oracle (informational): {rel}")
        for cls, n in pv["principled_difference_causes"].items():
            print(f"    {cls}  x{n}")
    for cls, n in list(report["disagreement_classes"].items())[:8]:
        print(f"  class: {cls}  x{n}")
    for ex in report["examples"][:3]:
        print("  e.g.", json.dumps(ex)[:300])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
