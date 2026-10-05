"""The recorded live runs can be re-scored offline, bit for bit, from the raw model responses alone."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parent.parent / "bench" / "extract"
sys.path.insert(0, str(BENCH))

import extract_dev_report as rep
from extract_score import load_jsonl

RUN_DIRS = sorted(p for p in (BENCH / "runs").glob("*") if p.is_dir() and list(p.glob("results-*-dev.json")))


@pytest.mark.skipif(not RUN_DIRS, reason="no recorded dev runs in bench/extract/runs/")
@pytest.mark.parametrize("run_dir", RUN_DIRS, ids=lambda p: p.name)
def test_rescoring_from_the_raw_cache_reproduces_the_committed_results(run_dir: Path, tmp_path: Path) -> None:
    work = tmp_path / run_dir.name
    shutil.copytree(run_dir, work)
    for f in work.glob("results-*.json"):
        f.unlink()
    produced = rep.process(work, "dev")
    assert produced, "expected at least one model run in the directory"
    for name in produced:
        committed = json.loads((run_dir / f"results-{name}-dev.json").read_text(encoding="utf-8"))
        again = json.loads((work / f"results-{name}-dev.json").read_text(encoding="utf-8"))
        assert again == committed, f"{name}: re-scored results differ from the committed results"


@pytest.mark.skipif(not RUN_DIRS, reason="no recorded dev runs in bench/extract/runs/")
def test_the_recorded_runs_stayed_inside_the_spend_authorisation() -> None:
    total = 0.0
    for run_dir in RUN_DIRS:
        for f in run_dir.glob("results-*-dev.json"):
            total += json.loads(f.read_text(encoding="utf-8"))["run"]["cost_usd"]
    assert total < 1.0


def test_failure_analysis_classifies_cue_confusion_wrong_value_and_missing() -> None:
    items = load_jsonl(BENCH / "items" / "dev.jsonl")
    by_id = {i["id"]: i for i in items}
    change = next(i for i in items if i["category"] == "change" and len(i["expected"]) == 1)
    plain = next(i for i in items if i["category"] == "plain_assert" and len(i["expected"]) == 1)
    e_change, e_plain = change["expected"][0], plain["expected"][0]
    wrong = {"form": "value", "v": "NOT-THE-VALUE"}
    preds = []
    for it in items:
        if it["id"] == change["id"]:
            # right key, but the change cue came back as a plain assert
            preds.append({"item_id": it["id"], "claims": [dict(e_change, cue="assert")], "identity_fields_seen": False})
        elif it["id"] == plain["id"]:
            preds.append({"item_id": it["id"], "claims": [dict(e_plain, proposition=wrong)], "identity_fields_seen": False})
        else:
            preds.append({"item_id": it["id"], "claims": [], "identity_fields_seen": False})
    fa = rep.failure_analysis(items, preds)
    assert fa["cue_confusion"] == {"change->assert": 1}
    assert [w["item"] for w in fa["wrong_values"]] == [plain["id"]]
    assert {d["item"] for d in fa["dropped_change_cues"]} >= {change["id"]}
    # every other expected claim got no prediction at all, and is reported as missing with its cause
    assert len(fa["missing_claims"]) == sum(len(i["expected"]) for i in items) - 2
    assert {m["cause"] for m in fa["missing_claims"]} == {"no claim returned"}
    assert plain["id"] in by_id
