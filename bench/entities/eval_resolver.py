"""Scores the lexical resolver on the labelled pairs: precision, recall, F1 and the FALSE-MERGE RATE per threshold,
with Wilson 95% intervals. `alias_only` positives are excluded from the lexical recall (no rule can resolve them) and
reported separately. Usage: `python bench/entities/eval_resolver.py --split dev|test [--json out.json]`."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))

from palimem.entities.resolver import (
    RESOLVER_VERSION,
    AliasTable,
    similarity,
)

THRESHOLDS = (0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0)


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def load(split: str) -> list[dict[str, str]]:
    path = HERE / "items" / f"{split}.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def checksum_ok(split: str) -> bool:
    want = json.loads((HERE / "items" / "checksums.json").read_text())[split]
    return hashlib.sha256((HERE / "items" / f"{split}.jsonl").read_bytes()).hexdigest() == want


def score_rows(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for r in rows:
        s = similarity(r["a"], r["b"], kind=r["kind"])
        out.append({**r, "score": s.score, "reason": s.reason})
    return out


def metrics(scored: list[dict[str, object]], t: float) -> dict[str, object]:
    lexical = [r for r in scored if r["category"] != "alias_only"]
    pos = [r for r in lexical if r["label"] == "same"]
    neg = [r for r in lexical if r["label"] == "different"]
    tp = sum(1 for r in pos if float(r["score"]) >= t)  # type: ignore[arg-type]
    fn = len(pos) - tp
    fp = sum(1 for r in neg if float(r["score"]) >= t)  # type: ignore[arg-type]
    prec_n = tp + fp
    return {
        "threshold": t, "tp": tp, "fp": fp, "fn": fn, "tn": len(neg) - fp,
        "precision": tp / prec_n if prec_n else None, "recall": tp / len(pos) if pos else None,
        "false_merge_rate": fp / len(neg) if neg else None,
        "wilson_recall": wilson(tp, len(pos)), "wilson_false_merge_rate": wilson(fp, len(neg)),
        "wilson_precision": wilson(tp, prec_n),
    }


def _f(x: object) -> str:
    return "  n/a" if x is None else f"{float(x):.3f}"  # type: ignore[arg-type]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test"], required=True)
    ap.add_argument("--json")
    ap.add_argument("--errors", action="store_true", help="print misclassified pairs at threshold 0.6")
    a = ap.parse_args()
    if not checksum_ok(a.split):
        print("refusing: items checksum differs from checksums.json", file=sys.stderr)
        return 2
    rows = load(a.split)
    scored = score_rows(rows)
    n_same = sum(1 for r in rows if r["label"] == "same")
    n_alias = sum(1 for r in rows if r["category"] == "alias_only")
    print(f"resolver {RESOLVER_VERSION} on {a.split}: {len(rows)} pairs ({n_same} same, {len(rows) - n_same} different; "
          f"{n_alias} alias_only excluded from lexical recall)")
    table = [metrics(scored, t) for t in THRESHOLDS]
    print(f"{'t':>5} {'prec':>6} {'recall':>7} {'FMR':>6}  FMR 95% Wilson     recall 95% Wilson   (TP/FP/FN)")
    for m in table:
        wf = m["wilson_false_merge_rate"]
        wr = m["wilson_recall"]
        print(f"{m['threshold']:>5} {_f(m['precision']):>6} {_f(m['recall']):>7} {_f(m['false_merge_rate']):>6}  "
              f"[{wf[0]:.3f}, {wf[1]:.3f}]   [{wr[0]:.3f}, {wr[1]:.3f}]   ({m['tp']}/{m['fp']}/{m['fn']})")  # type: ignore[index]
    al = AliasTable.of(same=[(r["a"], r["b"]) for r in rows if r["category"] == "alias_only"])
    resolved = sum(1 for r in rows if r["category"] == "alias_only"
                   and similarity(r["a"], r["b"], kind=r["kind"], aliases=al).score == 1.0)
    bare = sum(1 for r in scored if r["category"] == "alias_only" and float(r["score"]) >= 0.6)  # type: ignore[arg-type]
    print(f"alias_only pairs: {n_alias}; resolved with a declared alias {resolved}; by lexical rules at 0.6: {bare}")
    if a.errors:
        for r in scored:
            if r["category"] == "alias_only":
                continue
            pred = float(r["score"]) >= 0.6  # type: ignore[arg-type]
            if pred != (r["label"] == "same"):
                kind = "MISS " if r["label"] == "same" else "FALSE"
                print(f"  {kind} {float(r['score']):.3f} {r['reason']:<34} {r['a']!r} ~ {r['b']!r} ({r['category']})")  # type: ignore[arg-type]
    if a.json:
        Path(a.json).write_text(json.dumps({"split": a.split, "resolver": RESOLVER_VERSION, "table": table}, indent=2, default=list) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
