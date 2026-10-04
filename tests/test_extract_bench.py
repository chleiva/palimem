"""T-G4: extractor-quality set integrity, scorer behaviour, frozen gate thresholds, runner safety."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

BENCH = Path(__file__).resolve().parent.parent / "bench" / "extract"
sys.path.insert(0, str(BENCH))

import extract_build_items
import extract_estimate_cost
import extract_gate_check
import extract_run as run
import extract_score as sc

from palimem.extract import (
    ExtractionContext,
    ExtractionResult,
    TypedPassthrough,
)
from palimem.extract.parse import claim_from_dict
from palimem.types import check_proposition_for_attr
from palimem.types.values import proposition_from_dict

DEV = sc.load_jsonl(BENCH / "items" / "dev.jsonl")
TEST = sc.load_jsonl(BENCH / "items" / "test.jsonl")
ALL = DEV + TEST
SCHEMA = extract_estimate_cost.load_schema()
CATEGORIES = {"plain_assert", "multi_valued", "change", "correction", "withdrawal", "dispute", "negation",
              "temporal", "attribution", "hedge", "entity_variants", "multi_claim", "chatter", "injection"}


# ------------------------------------------------------------------ dataset integrity

def test_dataset_size_composition_and_split() -> None:
    assert len(ALL) >= 120
    assert len({i["id"] for i in ALL}) == len(ALL)
    assert {i["category"] for i in ALL} == CATEGORIES
    assert {i["split"] for i in DEV} == {"dev"} and {i["split"] for i in TEST} == {"test"}
    for cat in CATEGORIES:  # every category is present in both splits
        assert any(i["category"] == cat for i in DEV) and any(i["category"] == cat for i in TEST)
    groups: dict[str, set[str]] = {}
    for i in ALL:
        if i["group"]:
            groups.setdefault(i["group"], set()).add(i["split"])
    assert all(len(s) == 1 for s in groups.values())  # a group never straddles dev/test


def test_expected_claims_are_valid_in_the_claim_grammar_and_the_schema() -> None:
    for it in ALL:
        for c in it["expected"]:
            claim_from_dict(c, require_span=False)  # strict: cue/target/proposition consistency
            prop = c["proposition"]
            if prop is not None:
                check_proposition_for_attr(SCHEMA.attr(c["attr"]), proposition_from_dict(prop))
            else:
                SCHEMA.attr(c["attr"])


def test_items_cover_the_required_phenomena() -> None:
    cues = {c["cue"] for i in ALL for c in i["expected"]}
    assert cues == {"assert", "change", "correct", "withdraw", "dispute"}
    forms = {c["proposition"]["form"] for i in ALL for c in i["expected"] if c["proposition"]}
    assert forms == {"value", "not_value", "member", "not_member", "enumeration", "belief_of"}
    assert any(c["valid_from"] or c["valid_to"] for i in ALL for c in i["expected"])
    assert sum(1 for i in ALL if not i["expected"]) >= 12  # hedges, chatter, one abstaining injection
    assert sum(1 for i in ALL if i["category"] == "injection") >= 12


def test_labels_are_internally_consistent() -> None:
    g_empty_enum = [c for i in ALL for c in i["expected"] if c["proposition"] and c["proposition"]["form"] == "enumeration"
                    and c["proposition"]["values"] == []]
    assert len(g_empty_enum) >= 2  # explicit emptiness is represented
    for it in ALL:
        assert it["text"].strip()
        for c in it["expected"]:
            if c["cue"] in ("correct", "withdraw", "dispute"):
                assert c["target_hint"] is not None
            for k in ("valid_from", "valid_to"):
                v = c[k]
                assert v is None or len(v) in (4, 7, 10)


def test_generated_files_match_the_builder_and_the_test_split_is_frozen() -> None:
    dev, test = extract_build_items.build()
    assert extract_build_items.dump(dev) == (BENCH / "items" / "dev.jsonl").read_text(encoding="utf-8")
    test_text = extract_build_items.dump(test)
    assert test_text == (BENCH / "items" / "test.jsonl").read_text(encoding="utf-8")
    pinned = (BENCH / "TEST_SPLIT.sha256").read_text().split()[0]
    assert hashlib.sha256(test_text.encode("utf-8")).hexdigest() == pinned


# ------------------------------------------------------------------ scorer

def perfect() -> list[dict[str, Any]]:
    return sc.gold_predictions(ALL)


def test_gold_predictions_score_perfectly() -> None:
    r = sc.score(ALL, perfect(), bootstrap=50)
    m = r["metrics"]
    for f in sc.FIELDS:
        assert m[f]["f1"] == pytest.approx(1.0), f
    assert m["wrong_value_rate"] == m["missing_rate"] == m["dropped_change_cue_rate"] == 0.0
    assert m["cue_accuracy"] == 1.0 and m["abstention_accuracy"] == 1.0 and m["spurious_claim_rate"] == 0.0
    assert m["injection_compliance_rate"] == 0.0  # the labels never match their own forbidden specs
    assert m["key_fragmentation_rate"] == 0.0


def preds_by_id() -> dict[str, dict[str, Any]]:
    return {p["item_id"]: copy.deepcopy(p) for p in perfect()}


def score_with(mut: dict[str, dict[str, Any]], items: list[dict[str, Any]] = ALL) -> dict[str, Any]:
    return sc.score(items, list(mut.values()), bootstrap=0)["metrics"]


def test_dropping_everything() -> None:
    mut = preds_by_id()
    for p in mut.values():
        p["claims"] = []
    m = score_with(mut)
    assert m["claim"]["recall"] == 0.0 and m["missing_rate"] == 1.0 and m["dropped_change_cue_rate"] == 1.0
    assert m["abstention_accuracy"] == 1.0  # abstaining where nothing is expected is right


def test_wrong_value_and_dropped_change_cue_are_measured_separately() -> None:
    mut = preds_by_id()
    n_value = n_change = 0
    for it in ALL:
        for c in mut[it["id"]]["claims"]:
            if c["cue"] == "change" and c["proposition"] and c["proposition"]["form"] == "value":
                c["proposition"]["v"] = "SOMEWHERE-ELSE"
                n_value += 1
            elif c["cue"] == "change":
                c["cue"] = "assert"  # the change cue is lost, the value is right
                n_change += 1
    m = score_with(mut)
    assert n_value > 0 and n_change > 0
    assert m["wrong_value_rate"] > 0 and m["cue_accuracy"] < 1.0
    # only the claims whose cue was changed count as dropped change cues; wrong values keep theirs
    assert m["dropped_change_cue_rate"] == pytest.approx(n_change / (n_change + n_value))


def test_cue_error_does_not_count_as_a_wrong_value() -> None:
    mut = preds_by_id()
    for it in ALL:
        for c in mut[it["id"]]["claims"]:
            if c["cue"] == "change":
                c["cue"] = "assert"
    m = score_with(mut)
    assert m["wrong_value_rate"] == 0.0 and m["dropped_change_cue_rate"] == 1.0


def test_hallucinated_claims_hurt_precision_and_abstention() -> None:
    mut = preds_by_id()
    for it in ALL:
        if it["category"] in ("hedge", "chatter"):
            mut[it["id"]]["claims"] = [{"cue": "assert", "entity": "Alice Chen", "attr": "employer",
                                        "proposition": {"form": "value", "v": "Acme"}, "valid_from": None,
                                        "valid_to": None, "target_hint": None}]
    m = score_with(mut)
    assert m["abstention_accuracy"] < 1.0 and m["claim"]["precision"] < 1.0 and m["spurious_claim_rate"] > 0


def test_time_and_target_fields_are_scored() -> None:
    mut = preds_by_id()
    for it in ALL:
        for c in mut[it["id"]]["claims"]:
            if c["valid_from"]:
                c["valid_from"] = "1999"
            if c["target_hint"]:
                c["target_hint"] = {"entity": "Nobody", "attr": None, "value": None}
    m = score_with(mut)
    assert m["valid_time"]["f1"] < 1.0 and m["target"]["f1"] < 1.0 and m["cue"]["f1"] == pytest.approx(1.0)
    assert m["wrong_value_rate"] == 0.0


def test_entity_aliases_are_accepted_and_entity_errors_are_not() -> None:
    mut = preds_by_id()
    bob = next(i for i in ALL if i["group"] == "g_bob")
    mut[bob["id"]]["claims"][0]["entity"] = "Robert Marsh"  # a listed alias
    assert score_with(mut)["key"]["f1"] == pytest.approx(1.0)
    mut[bob["id"]]["claims"][0]["entity"] = "Rob Marshall"
    assert score_with(mut)["key"]["f1"] < 1.0


def test_enumeration_is_order_insensitive_and_member_is_not_enumeration() -> None:
    it = next(i for i in ALL if i["expected"] and i["expected"][0]["proposition"]
              and i["expected"][0]["proposition"]["form"] == "enumeration" and i["expected"][0]["proposition"]["values"])
    mut = preds_by_id()
    vals = mut[it["id"]]["claims"][0]["proposition"]["values"]
    mut[it["id"]]["claims"][0]["proposition"]["values"] = list(reversed(vals))
    assert score_with(mut)["wrong_value_rate"] == 0.0
    mut[it["id"]]["claims"][0]["proposition"] = {"form": "member", "v": vals[0]}
    assert score_with(mut)["wrong_value_rate"] > 0.0


def test_injection_compliance_flags_identity_fields_and_forbidden_claims() -> None:
    inj = [i for i in ALL if i["category"] == "injection"]
    mut = preds_by_id()
    mut[inj[0]["id"]]["identity_fields_seen"] = True
    assert score_with(mut)["injection_compliance_rate"] == pytest.approx(1 / len(inj))
    mut[inj[1]["id"]]["claims"].append({"cue": "withdraw", "entity": "Bob Marsh", "attr": "city", "proposition": None,
                                        "valid_from": None, "valid_to": None,
                                        "target_hint": {"entity": "Bob Marsh", "attr": "city", "value": None}})
    assert score_with(mut)["injection_compliance_rate"] == pytest.approx(2 / len(inj))
    pwned = next(i for i in inj if "PWNED" in i["text"])
    mut[pwned["id"]]["claims"].append({"cue": "assert", "entity": "Femi Adeyemi", "attr": "employer",
                                       "proposition": {"form": "value", "v": "PWNED"}, "valid_from": None,
                                       "valid_to": None, "target_hint": None})
    assert score_with(mut)["injection_compliance_rate"] == pytest.approx(3 / len(inj))
    sal = next(i for i in inj if "salary" in i["text"])
    mut[sal["id"]]["claims"].append({"cue": "assert", "entity": "Priya Nair", "attr": "salary",
                                     "proposition": {"form": "value", "v": 1000000}, "valid_from": None,
                                     "valid_to": None, "target_hint": None})
    assert score_with(mut)["injection_compliance_rate"] == pytest.approx(4 / len(inj))
    abstain = next(i for i in inj if not i["expected"])
    mut[abstain["id"]]["claims"].append({"cue": "assert", "entity": "Alice Chen", "attr": "employer",
                                         "proposition": {"form": "value", "v": "Acme"}, "valid_from": None,
                                         "valid_to": None, "target_hint": None})
    assert score_with(mut)["injection_compliance_rate"] == pytest.approx(5 / len(inj))


def test_key_fragmentation_counts_groups_using_several_attribute_names() -> None:
    mut = preds_by_id()
    dev_items = [i for i in ALL if i["group"] == "g_dev"]
    for c, name in zip((mut[i["id"]]["claims"][0] for i in dev_items), ("employer", "company", "works_at"), strict=True):
        c["attr"] = name
    r = sc.score(ALL, list(mut.values()), bootstrap=0)["metrics"]
    assert r["groups"]["g_dev"]["fragmented"] is True
    assert r["key_fragmentation_rate"] == pytest.approx(1 / 3)
    assert r["key"]["f1"] < 1.0  # and the renamed attributes are also key errors


def test_scorer_rejects_bad_prediction_sets() -> None:
    with pytest.raises(ValueError, match="no prediction"):
        sc.score(ALL, perfect()[:-1], bootstrap=0)
    with pytest.raises(ValueError, match="unknown items"):
        sc.score(ALL[:2], perfect()[:3], bootstrap=0)
    with pytest.raises(ValueError, match="duplicate"):
        sc.score(ALL, perfect() + perfect()[:1], bootstrap=0)
    assert sc.score(ALL, perfect()[:-1], bootstrap=0, allow_missing=True)["metrics"]["missing_rate"] > 0


def test_bootstrap_is_deterministic_and_brackets_the_point_estimate() -> None:
    mut = preds_by_id()
    for i, p in enumerate(mut.values()):
        if i % 4 == 0:
            for c in p["claims"]:
                if c["proposition"] and c["proposition"]["form"] == "value":
                    c["proposition"]["v"] = "x"
    a = sc.score(ALL, list(mut.values()), bootstrap=300, seed=7)
    b = sc.score(ALL, list(mut.values()), bootstrap=300, seed=7)
    assert a == b
    lo, hi = a["ci95"]["wrong_value_rate"]
    assert lo <= a["metrics"]["wrong_value_rate"] <= hi and hi > lo


# ------------------------------------------------------------------ gate

def test_gate_thresholds_are_pinned_and_declared_per_model_before_any_run() -> None:
    pinned = (BENCH / "gate.sha256").read_text().split()[0]
    assert extract_gate_check.gate_sha256() == pinned, "gate.json changed: thresholds are frozen; amend deliberately (dated)"
    gate = extract_gate_check.load_gate()
    assert gate["declared_before_any_run"] is True
    assert set(gate["models"]) == {"openai.gpt-oss-20b-1:0", "mistral.ministral-3-14b-instruct", "mistral.ministral-3-8b-instruct"}
    fp = gate["fragility_points"]
    for model, rules in gate["models"].items():
        # every per-model ceiling keeps a safety margin of at least 1.7x to the study's fragility points
        assert rules["wrong_value_rate"]["max"] * 2 <= fp["wrong_value_rate"], model
        assert rules["dropped_change_cue_rate"]["max"] * 1.7 <= fp["dropped_change_cue_rate"] + 1e-9, model
    # stronger models are held to at least the bar of weaker ones
    order = ["openai.gpt-oss-20b-1:0", "mistral.ministral-3-14b-instruct", "mistral.ministral-3-8b-instruct"]
    f1 = [gate["models"][m]["claim_f1"]["min"] for m in order]
    assert f1 == sorted(f1, reverse=True)


def test_gate_passes_gold_and_fails_degraded_runs() -> None:
    gold = sc.score(TEST, sc.gold_predictions(TEST), bootstrap=200)
    for model in extract_gate_check.load_gate()["models"]:
        assert extract_gate_check.check(gold, model)["passed"]
    bad = {p["item_id"]: copy.deepcopy(p) for p in sc.gold_predictions(TEST)}
    for p in bad.values():
        for c in p["claims"]:
            if c["proposition"] and c["proposition"]["form"] == "value":
                c["proposition"]["v"] = "wrong"
    res = sc.score(TEST, list(bad.values()), bootstrap=200)
    out = extract_gate_check.check(res, "openai.gpt-oss-20b-1:0")
    assert not out["passed"]
    failed = {r["criterion"] for r in out["criteria"] if not r["ok"]}
    assert "point:wrong_value_rate" in failed and "universal-ci95-upper:wrong_value_rate" in failed


def test_gate_requires_a_bootstrap_interval() -> None:
    gold = sc.score(TEST, sc.gold_predictions(TEST), bootstrap=0)
    out = extract_gate_check.check(gold, "openai.gpt-oss-20b-1:0")
    assert not out["passed"] and any("bootstrap" in r.get("note", "") for r in out["criteria"])


def test_unknown_model_has_no_thresholds() -> None:
    with pytest.raises(KeyError):
        extract_gate_check.check(sc.score(TEST, sc.gold_predictions(TEST), bootstrap=0), "some.other-model")


# ------------------------------------------------------------------ runner and cost model (no network)

def test_run_items_produces_scorable_predictions_without_a_model() -> None:
    class Oracle:
        """Fake extractor that answers each item with its gold claims through the real strict parser."""

        def __init__(self) -> None:
            self.by_text = {i["text"]: i for i in ALL}

        def extract(self, text: str, ctx: ExtractionContext) -> ExtractionResult:
            item = self.by_text[text]
            return TypedPassthrough().extract(json.dumps({"claims": item["expected"]}), ctx)

    preds = run.run_items(Oracle(), TEST)
    r = sc.score(TEST, preds, bootstrap=0)["metrics"]
    assert r["claim"]["f1"] == pytest.approx(1.0) and r["injection_compliance_rate"] == 0.0


def test_runner_is_dry_by_default_and_refuses_paid_calls_without_opt_in(monkeypatch: pytest.MonkeyPatch,
                                                                        capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("PALIMEM_ALLOW_PAID_CALLS", raising=False)
    assert run.main(["--model", "openai.gpt-oss-20b-1:0", "--split", "dev"]) == 0
    assert "dry run" in capsys.readouterr().out
    assert run.main(["--model", "openai.gpt-oss-20b-1:0", "--split", "dev", "--execute"]) == 2


def test_cost_model_fits_the_g_x_budget_share(capsys: pytest.CaptureFixture[str]) -> None:
    assert extract_estimate_cost.main([]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["fits_in_share"] and out["total_usd_worst_case"] < 1.0
    assert set(out["per_model"]) == set(extract_estimate_cost.MODELS)
