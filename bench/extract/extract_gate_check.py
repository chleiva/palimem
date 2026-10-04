"""Evaluate a score.py result against the frozen G-X thresholds (bench/extract/gate.json).

    python bench/extract/extract_gate_check.py --results results.json --model openai.gpt-oss-20b-1:0

Exit code 0 = gate passed for that model, 1 = failed. The thresholds were declared before any run
and are pinned by ``gate.sha256``; changing them is a dated, reviewed act, not a tuning knob.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent


def load_gate(path: str | Path | None = None) -> dict[str, Any]:
    gate: dict[str, Any] = json.loads(Path(path or HERE / "gate.json").read_text(encoding="utf-8"))
    return gate


def gate_sha256(path: str | Path | None = None) -> str:
    return hashlib.sha256(Path(path or HERE / "gate.json").read_bytes()).hexdigest()


def _metric(results: dict[str, Any], name: str) -> float:
    m = results["metrics"]
    return float(m["claim"]["f1"]) if name == "claim_f1" else float(m[name])


def _ok(value: float, rule: dict[str, float]) -> bool:
    if "min" in rule and value < rule["min"]:
        return False
    return not ("max" in rule and value > rule["max"])


def check(results: dict[str, Any], model: str, gate: dict[str, Any] | None = None) -> dict[str, Any]:
    gate = gate or load_gate()
    if model not in gate["models"]:
        raise KeyError(f"no declared G-X thresholds for model {model!r}")
    rows: list[dict[str, Any]] = []
    for name, rule in gate["models"][model].items():
        v = _metric(results, name)
        rows.append({"criterion": f"point:{name}", "value": v, "rule": rule, "ok": _ok(v, rule)})
    for name, rule in gate["universal"]["point"].items():
        v = _metric(results, name)
        rows.append({"criterion": f"universal-point:{name}", "value": v, "rule": rule, "ok": _ok(v, rule)})
    for name, rule in gate["universal"]["ci95_upper"].items():
        ci = results.get("ci95", {}).get(name)
        if not ci:
            rows.append({"criterion": f"universal-ci95-upper:{name}", "value": None, "rule": rule, "ok": False,
                         "note": "no bootstrap interval in the results; rerun score.py with --bootstrap"})
            continue
        rows.append({"criterion": f"universal-ci95-upper:{name}", "value": ci[1], "rule": rule, "ok": _ok(ci[1], rule)})
    return {"model": model, "passed": all(r["ok"] for r in rows), "criteria": rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--model", required=True)
    a = ap.parse_args(argv)
    res = json.loads(Path(a.results).read_text(encoding="utf-8"))
    out = check(res, a.model)
    print(json.dumps(out, indent=2))
    return 0 if out["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
