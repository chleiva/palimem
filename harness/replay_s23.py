"""Replay of the study's Settings 2 and 3 through the palimem pipeline (T-J5, the G2 gap).

What the study's systems saw in those settings is not the stream but **extracted claims**: an LLM read the natural
language rendering of every report once and the study cached the result (``extracted.json``). This harness feeds the
very same cached claims, in the study's own order, through ``palimem.memory.Memory`` under the compat profile on both
backends, answers every query of the stream from the stored belief versions at the query's log position, and compares

  1. with the study's own ``BeliefStore`` answers on the same claims (``answers.json['palimpsest']``): the primary
     differential, exactly as ``harness.pipeline_diff`` does it for Setting 1 (status, assertion, alternatives);
  2. with the study's symbolic replay on the same claims (``answers.json['log_rule_adjudicator']``): secondary, so a
     difference can be attributed to the study's own store/replay gap rather than to palimem;
  3. with the frozen oracle gold (``frozen.json``): **informational only** (extraction errors make the claims disagree
     with the gold in the study's store too; that is the measured subject of the paper, not a conformance question).

No model is called. Provenance is not compared (the cached answers carry the store's interim provenance, which the
study measured as differing from replay on 2.3 to 5.3% of queries).

It also runs the authority-coincidence check of decision S-02 on both the extracted claims and the stream's original
reports: corrections by a different source of the same origin as their target (zero means origin-based and
source-based authority coincide for the paper's A-SELF rule), plus the retractions that cross sources (the product
default, authority of the target's own source, would refuse those).

Exit codes: 0 all agree on every replayed query · 1 disagreement · 2 setup error.
``--inject-bug mutate-answer`` is the self-test: the gate must then exit 1.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from harness import frozen, study, studydata
from harness.convert import to_converted
from harness.differential import signature
from harness.pipeline_diff import (
    _Clock,
    answer_query,
    build_memory,
    make_backend,
    replay,
)

BACKENDS = ("memory", "sqlite")
DATASETS = tuple(studydata.DATASETS)
INJECTIONS = ("none", "mutate-answer")


def _mutate(ans: dict[str, Any]) -> dict[str, Any]:
    out = dict(ans)
    out["status"] = "unresolved" if ans["status"] == "established" else "established"
    out["assertion"] = None if ans["status"] == "established" else ans.get("assertion")
    return out


def _obs_order(o: Any) -> tuple[int, int]:
    try:
        return (o.t_rep, int(o.id[1:]))
    except ValueError:
        return (o.t_rep, 0)


def extracted_stream(base: Path, extracted: Path) -> Any:
    """The stream every system saw: the stream's queries and domain with the extracted claims as its observations
    (failed extractions dropped), in the study's order (``nl.pipeline.Setting2.extracted_stream``)."""
    from revise_stream.model import Observation, stream_from_dict

    es = stream_from_dict(json.loads(base.read_text()))
    ex = json.loads(extracted.read_text())
    obs = [Observation(**v["obs"]) for v in ex.values() if v["obs"]]
    obs.sort(key=_obs_order)
    es.observations = obs
    es.invalidate()
    return es


def normalise_references(stream: Any) -> Counter[str]:
    """Make references to non-assertions explicit no-ops, as the study's own semantics treat them.

    ``Stream.admitted`` only ever returns *assertions*; a retraction whose target is neither an assertion nor a source,
    and a correction whose ``op_of`` is not an assertion, therefore have no effect (the extractor produces such claims
    when it points at a retraction or invents an id). The contract cannot express a withdraw or a correction of
    something that is not a report, so the replay turns the first into nothing (dropped: it changes no belief) and the
    second into a plain assertion. Counted, never silent; the differential tests that this preserves behaviour."""
    c: Counter[str] = Counter()
    asserts = {o.id for o in stream.observations if o.kind == "assert"}
    kept = []
    for o in stream.observations:
        if o.kind == "retract" and o.target not in asserts and o.target not in stream.sources:
            c["retract_of_non_assertion_dropped"] += 1
            continue
        if o.kind == "assert" and o.op_cue == "correction" and o.op_of not in asserts:
            o = dataclasses.replace(o, op_cue="none", op_of=None)
            c["correction_of_non_assertion_as_plain_assertion"] += 1
        kept.append(o)
    stream.observations = kept
    stream.invalidate()
    return c


def feature_mix(stream: Any) -> Counter[str]:
    """Features of a stream's observations the kernel may not support: recorded for coverage reporting."""
    c: Counter[str] = Counter()
    for o in stream.observations:
        c["observations"] += 1
        if o.kind == "assert":
            c["assert"] += 1
        else:
            c["retract"] += 1
        if o.valid_cue in ("until", "interval"):
            c[f"cue:{o.valid_cue}"] += 1
        if o.polarity is False:
            c["negative_polarity"] += 1
        if o.ctx:
            c["ctx"] += 1
        if o.op_cue == "correction":
            c["correction"] += 1
        if o.op_cue == "change":
            c["change"] += 1
    return c


def authority_check(stream: Any) -> Counter[str]:
    """Classify every correction and every retraction of a stream by the relation of its source to its target's.

    Origin-based authority (the paper's A-SELF: same *origin*) and source-based authority (design default: same
    *source*) coincide on the stream iff there is no ``correction:cross_source_same_origin``."""
    c: Counter[str] = Counter()
    by_id = {o.id: o for o in stream.observations}

    def origin(s: str) -> Any:
        sp = stream.sources.get(s)
        return getattr(sp, "origin", None) if sp is not None else None

    for o in stream.observations:
        if o.kind == "assert" and o.op_cue == "correction":
            t = by_id.get(o.op_of) if o.op_of else None
            if t is None:
                c["correction:dangling_target"] += 1
            elif t.source == o.source:
                c["correction:same_source"] += 1
            elif origin(t.source) is not None and origin(t.source) == origin(o.source):
                c["correction:cross_source_same_origin"] += 1
            else:
                c["correction:cross_origin"] += 1
        elif o.kind == "retract":
            if o.target in stream.sources:
                c["retract:source_level"] += 1
                continue
            t = by_id.get(o.target) if o.target else None
            if t is None:
                c["retract:dangling_target"] += 1
            elif t.source == o.source:
                c["retract:same_source"] += 1
            elif origin(t.source) is not None and origin(t.source) == origin(o.source):
                c["retract:cross_source_same_origin"] += 1
            else:
                c["retract:cross_origin"] += 1
    return c


def _slot_key(q: Any) -> str:
    return q.slot + (":" + q.prop["kind"] if q.slot == "yesno" else "")


def run_dataset(
    root: Path, st: Any, dataset: str, *, limit: int | None = None, stride: int = 1,
    backends: tuple[str, ...] = BACKENDS, inject: str = "none", max_examples: int = 12, progress: bool = False,
) -> dict[str, Any]:
    ids_all = studydata.stream_ids(root, dataset)[::stride]
    if limit:
        ids_all = ids_all[:limit]
    if not ids_all:
        raise frozen.FrozenError(f"no streams found for dataset {dataset!r} under {root}")
    manifest = studydata.load_manifest()
    for sid in ids_all:
        studydata.require_valid(root, manifest, only=studydata.needed_rel(dataset, sid))
    t0 = time.time()
    auth_extracted: Counter[str] = Counter()
    auth_original: Counter[str] = Counter()
    feats_extracted: Counter[str] = Counter()
    normalised: Counter[str] = Counter()
    per_backend: dict[str, dict[str, Any]] = {}
    mutated = False
    for kind in backends:
        totals: Counter[str] = Counter()
        per_slot: dict[str, Counter[str]] = {}
        classes: Counter[str] = Counter()
        failed: dict[str, str] = {}
        examples: list[dict[str, Any]] = []
        for i, sid in enumerate(ids_all):
            p = studydata.paths_of(root, dataset, sid)
            es = extracted_stream(p["stream"], p["extracted"])
            if kind == backends[0]:
                auth_extracted.update(authority_check(es))  # on the claims as extracted, before normalisation
                feats_extracted.update(feature_mix(es))
            norm_counts = normalise_references(es)
            if kind == backends[0]:
                normalised.update(norm_counts)
                base = st.load_stream(str(p["stream"]))
                auth_original.update(authority_check(base))
            answers = json.loads(p["answers"].read_text())
            study_store, study_replay = answers["palimpsest"], answers["log_rule_adjudicator"]
            gold = json.loads(p["frozen"].read_text())["gold"]
            try:
                conv = to_converted(es, "sidetable")
                clock = _Clock()
                mem = build_memory(conv, make_backend(kind, clock), "none")
                ids = replay(conv, mem, clock)
            except Exception as e:  # noqa: BLE001 - classified, never hidden
                failed[sid] = f"{type(e).__name__}: {str(e)[:110]}"
                totals["streams_not_replayed"] += 1
                totals["queries_not_replayed"] += len(es.queries)
                continue
            totals["streams_replayed"] += 1
            for q in sorted(es.queries, key=lambda q: (q.tau, q.id)):
                slot = _slot_key(q)
                c = per_slot.setdefault(slot, Counter())
                totals["queries"] += 1
                c["queries"] += 1
                try:
                    ans, _prov = answer_query(mem, conv, ids, q, totals)
                except Exception as e:  # noqa: BLE001
                    totals["query_errors"] += 1
                    c["query_errors"] += 1
                    classes[f"{slot}: query error {type(e).__name__}"] += 1
                    continue
                if inject == "mutate-answer" and not mutated and ans["status"] == "established":
                    ans, mutated = _mutate(ans), True
                sig = signature(ans, st.norm)
                ref_s, ref_r, g = study_store[q.id], study_replay[q.id], gold[q.id]
                sig_s, sig_r, sig_g = signature(ref_s, st.norm), signature(ref_r, st.norm), signature(g, st.norm)
                if sig == sig_g:
                    totals["agrees_with_gold"] += 1
                if sig_s == sig_g:
                    totals["study_store_agrees_with_gold"] += 1
                if sig_s != sig_r:
                    totals["study_store_differs_from_study_replay"] += 1
                if sig == sig_r:
                    totals["agrees_with_study_replay"] += 1
                if sig != sig_s:
                    c["disagreements"] += 1
                    totals["disagreements"] += 1
                    derived = bool(es.attributes.get(q.attr or "") and es.attributes[q.attr].derived)
                    why = "study store != study replay" if sig_s != sig_r else "study store == study replay"
                    classes[f"{slot}/{'derived' if derived else 'base'}: study={ref_s['status']} palimem={ans['status']} ({why})"] += 1
                    if len(examples) < max_examples:
                        examples.append({
                            "stream": sid, "query": q.id, "slot": slot, "study_store": {k: ref_s.get(k) for k in ("status", "assertion", "alternatives")},
                            "study_replay": {k: ref_r.get(k) for k in ("status", "assertion", "alternatives")},
                            "palimem": {k: ans.get(k) for k in ("status", "assertion", "alternatives")},
                            "gold": {k: g.get(k) for k in ("status", "assertion", "alternatives")},
                        })
            if progress and (i + 1) % 25 == 0:
                print(f"  [{dataset}/{kind}] {i + 1}/{len(ids_all)} streams, {totals['disagreements']} disagreements", flush=True)
        per_backend[kind] = {
            "totals": dict(totals), "per_slot": {k: dict(v) for k, v in sorted(per_slot.items())},
            "disagreement_classes": dict(classes.most_common()), "not_replayed": failed, "examples": examples,
        }
    return {
        "dataset": dataset, "streams": len(ids_all), "backends": per_backend,
        "authority_extracted_claims": dict(sorted(auth_extracted.items())),
        "authority_original_reports": dict(sorted(auth_original.items())),
        "extracted_feature_mix": dict(sorted(feats_extracted.items())),
        "reference_normalisation": dict(sorted(normalised.items())),
        "seconds": round(time.time() - t0, 1),
    }


def verdict(report: dict[str, Any]) -> int:
    bad = 0
    for ds in report["datasets"].values():
        for b in ds["backends"].values():
            bad += b["totals"].get("disagreements", 0)
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness.replay_s23", description=__doc__.split("\n")[0])
    ap.add_argument("--dataset", default="all", help="s2, s2_strong, s3 or all")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--backends", default=",".join(BACKENDS))
    ap.add_argument("--s23-dir", help="directory laid out as the deposit's data/ tree (default: cached, verified)")
    ap.add_argument("--study-dir", default=None)
    ap.add_argument("--fetch", action="store_true", help="download the deposit if no local copy is found")
    ap.add_argument("--inject-bug", choices=INJECTIONS, default="none")
    ap.add_argument("--out")
    ap.add_argument("--progress", action="store_true")
    a = ap.parse_args(argv)
    try:
        st = study.load(a.study_dir)
        root = studydata.locate(st.dir, a.s23_dir, a.fetch)
        names = list(DATASETS) if a.dataset == "all" else a.dataset.split(",")
        backends = tuple(x for x in a.backends.split(",") if x)
        report: dict[str, Any] = {
            "harness": "replay_s23", "study_commit": st.commit, "profile": "revise-stream-v1",
            "datasets": {n: run_dataset(root, st, n, limit=a.limit, stride=a.stride, backends=backends,
                                        inject=a.inject_bug, progress=a.progress) for n in names},
        }
    except (frozen.FrozenError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    for n, ds in report["datasets"].items():
        for kind, b in ds["backends"].items():
            t = b["totals"]
            print(f"{n:10s} {kind:7s} streams {t.get('streams_replayed', 0)}/{ds['streams']} replayed, "
                  f"{t.get('queries', 0)} queries, {t.get('disagreements', 0)} disagreements with the study store"
                  f" (replay-attributed: {t.get('study_store_differs_from_study_replay', 0)} study store/replay gaps), "
                  f"{t.get('query_errors', 0)} query errors, gold agreement {t.get('agrees_with_gold', 0)}"
                  f" (study store {t.get('study_store_agrees_with_gold', 0)})")
            for cls, k in list(b["disagreement_classes"].items())[:6]:
                print(f"    {k:5d}  {cls}")
            for sid, why in list(b["not_replayed"].items())[:4]:
                print(f"    not replayed {sid}: {why}")
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
        print(f"wrote {a.out}")
    return verdict(report)


if __name__ == "__main__":
    raise SystemExit(main())
