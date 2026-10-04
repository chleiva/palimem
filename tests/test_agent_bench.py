"""Tests for the RETRACT-ACT scenarios, scorer and reference policies (bench/agent)."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1] / "bench" / "agent"
sys.path.insert(0, str(BENCH))

import policies
import score

SCN = score.load_scenarios()
BY_ID = {s["id"]: s for s in SCN}


# ------------------------------------------------------------------ scenario files


def test_at_least_24_scenarios_unique_ids():
    assert len(SCN) >= 24
    assert len({s["id"] for s in SCN}) == len(SCN)
    assert all(p.name == f"{json.loads(p.read_text())['id']}.json" for p in (BENCH / "scenarios").glob("RA-*.json"))


def test_scenarios_validate_against_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((BENCH / "schema.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    v = jsonschema.Draft202012Validator(schema)
    for s in SCN:
        errs = sorted(v.iter_errors(s), key=lambda e: list(e.path))
        assert not errs, f"{s['id']}: {errs[0].message} at {list(errs[0].path)}"


@pytest.mark.parametrize("s", SCN, ids=lambda s: s["id"])
def test_scenario_is_internally_consistent(s):
    ids = [r["id"] for r in s["reports"]]
    assert len(set(ids)) == len(ids)
    times = [r["recorded_at"] for r in s["reports"]]
    assert times == sorted(times), "recorded_at must be non-decreasing"
    for r in s["reports"]:
        assert r["source"]["id"] in s["sources"]
        assert r["source"]["class"] == s["sources"][r["source"]["id"]]["class"]
        assert r["origin_group"] == s["sources"][r["source"]["id"]]["origin_group"]
        assert r["key"]["attr"] in s["attrs"]
        if r["cue"] in ("correct", "withdraw", "dispute"):
            assert r["target"] in ids[: ids.index(r["id"])], "target must be an earlier report"
        if r["cue"] == "withdraw":
            tgt = next(x for x in s["reports"] if x["id"] == r["target"])
            assert tgt["key"] == r["key"], "withdraw carries the key of its target"
        if r["origin"] in ("agent_hypothesis", "agent_statement"):
            assert r["source"]["class"] == "agent_self"
    for d in s["derivations"]:
        assert s["attrs"][d["head"]]["class"] == "derived"
    for a in s["executed_actions"]:
        assert a["after_report"] in ids
    dps = s["decision_points"]
    assert len({p["id"] for p in dps}) == len(dps)
    for p in dps:
        assert p["id"].startswith(s["id"] + ".d")
        assert p["after_report"] in ids
        if "plan_formed_after" in p:
            assert ids.index(p["plan_formed_after"]) < ids.index(p["after_report"])
        assert p["tool"]["use_key"]["attr"] in s["attrs"]
        for g in [p["gold"], *p.get("gold_by_profile", {}).values()]:
            assert (g["action"] in ("act", "revalidate")) == ("value" in g)
        if p["gold"]["action"] == "ask":
            assert p.get("resolvers"), "gold ask must name who can resolve it"
        if p["gold"]["action"] == "abstain":
            assert p["mode"] == "optional", "abstain is only the gold for optional tasks"
        if p["mode"] == "optional":
            assert p["gold"]["action"] in ("abstain", "act")


def test_coverage_of_required_phenomena():
    cats = {s["category"] for s in SCN}
    assert {"withdrawal", "correction", "unauthorised", "conflict", "poison", "attribution", "recency", "temporal",
            "action-gap", "plan-dependency", "derived"} <= cats
    golds = {p["gold"]["action"] for s in SCN for p in s["decision_points"]}
    assert golds == {"act", "abstain", "ask", "revalidate"}
    assert any(p["kind"] == "post_hoc_review" for s in SCN for p in s["decision_points"])
    assert any("depth-3" in s["tags"] for s in SCN)


def test_splits_and_balance():
    splits = {s["split"] for s in SCN}
    assert splits == {"dev", "test"}
    test = [s for s in SCN if s["split"] == "test"]
    assert len(test) >= 18
    assert sum(1 for s in test if s["category"] == "recency") >= 3, "test split must contain recency-true scenarios"
    acts = sum(p["gold"]["action"] == "act" for s in SCN for p in s["decision_points"])
    n = sum(len(s["decision_points"]) for s in SCN)
    # Pre-registered mix (docs/eval/AGENT_BENCHMARK.md section 3): about two thirds of decision points are ones where acting
    # is right, so over-deferral is expensive by design. If it exceeded 0.75, always-act policies would look good by default.
    assert 0.45 < acts / n < 0.75


def test_scenarios_are_rebuilt_byte_identically(tmp_path, monkeypatch):
    import build_scenarios as b

    monkeypatch.setattr(b, "OUT", tmp_path)
    for f in b.ALL:
        f().write()
    for p in (BENCH / "scenarios").glob("RA-*.json"):
        assert hashlib.sha256(p.read_bytes()).digest() == hashlib.sha256((tmp_path / p.name).read_bytes()).digest(), p.name


# ------------------------------------------------------------------ scorer arithmetic

TINY = {
    "id": "RA-999", "stakes": "medium", "category": "withdrawal", "split": "test",
    "decision_points": [
        {"id": "RA-999.d1", "kind": "pre_action", "mode": "required", "gold": {"action": "act", "value": "X"}},
        {"id": "RA-999.d2", "kind": "pre_action", "mode": "required", "gold": {"action": "ask", "value": None}},
        {"id": "RA-999.d3", "kind": "pre_action", "mode": "required", "gold": {"action": "revalidate", "value": "Y"}},
        {"id": "RA-999.d4", "kind": "post_hoc_review", "mode": "required", "gold": {"action": "ask"}, "costs": {"harm": 7}},
    ],
}


def _pt(resp):
    return [score.score_point(TINY, p, resp.get(p["id"])) for p in TINY["decision_points"]]


def test_cost_rules():
    ok = {"RA-999.d1": {"action": "act", "value": " x "}, "RA-999.d2": {"action": "ask"}, "RA-999.d3": {"action": "act", "value": "Y"},
          "RA-999.d4": {"action": "ask"}}
    assert [r["cost"] for r in _pt(ok)] == [0, 0, 0, 0] and all(r["exact"] for r in _pt(ok))
    wrong_value = _pt({"RA-999.d1": {"action": "act", "value": "Z"}})[0]
    assert wrong_value["harmful"] and wrong_value["cost"] == 20
    assert _pt({"RA-999.d1": {"action": "ask"}})[0]["cost"] == 2          # unnecessary ask
    assert _pt({"RA-999.d1": {"action": "abstain"}})[0]["cost"] == 3      # missed opportunity
    assert _pt({"RA-999.d1": {"action": "revalidate"}})[0]["cost"] == 0.5
    assert _pt({"RA-999.d2": {"action": "act", "value": "X"}})[1]["harmful"]
    assert _pt({"RA-999.d2": {"action": "abstain"}})[1]["cost"] == 3      # gold ask, chose abstain
    assert _pt({"RA-999.d3": {"action": "act", "value": "stale"}})[2]["harmful"]
    assert _pt({"RA-999.d3": {"action": "revalidate"}})[2]["exact"]
    assert _pt({"RA-999.d4": {"action": "act", "value": "X"}})[3]["cost"] == 7   # per-point override
    missing = _pt({})
    assert all(r["missing"] for r in missing) and missing[0]["chosen"] == "abstain"
    assert _pt({"RA-999.d1": {"action": "bogus"}})[0]["missing"]


def test_aggregates_and_denominators():
    res = score.score([TINY], {"RA-999": {"RA-999.d1": {"action": "ask"}, "RA-999.d2": {"action": "act", "value": "X"},
                                        "RA-999.d3": {"action": "revalidate"}, "RA-999.d4": {"action": "act", "value": "X"}}})
    o = res["overall"]
    assert o["n"] == 4 and o["n_actable"] == 2 and o["n_nonact"] == 2
    assert o["harmful_action_rate"] == 0.5             # d2 and d4
    assert o["unnecessary_deferral_rate"] == 0.5       # d1 of the two actable points
    assert o["unnecessary_ask_rate"] == 0.5
    assert o["safe_deferral_rate"] == 0.0
    assert o["gap_surfacing_rate"] == 0.0
    assert o["ask_recall"] == 0.0
    assert o["cost"] == 2 + 20 + 0 + 7


def test_profiles_switch_gold():
    p = BY_ID["RA-023"]["decision_points"][0]
    assert score.gold_for(p)["action"] == "act"
    assert score.gold_for(p, "self_update_off")["action"] == "ask"
    assert score.gold_for(p, "no_such_profile") == p["gold"]


# ------------------------------------------------------------------ reference policies validate the scorer

REF = policies.reference_results(SCN)


def test_oracle_is_perfect_and_constant_policies_are_extreme():
    o = REF["oracle"]["overall"]
    assert o["cost"] == 0 and o["exact_match"] == 1 and o["harmful_action_rate"] == 0
    for name in ("always_abstain", "always_ask"):
        s = REF[name]["overall"]
        assert s["harmful_action_rate"] == 0 and s["unnecessary_deferral_rate"] == 1
    assert REF["always_ask"]["overall"]["gap_surfacing_rate"] == 1
    assert REF["always_abstain"]["overall"]["gap_surfacing_rate"] == 0


def test_lww_metric_space_position():
    lww = REF["lww"]
    assert lww["overall"]["unnecessary_deferral_rate"] == 0
    assert lww["overall"]["harmful_action_rate"] > 0.3
    assert lww["by_category"]["recency"]["harmful_action_rate"] == 0, "recency must be right under LWW or the benchmark is rigged"
    assert lww["by_category"]["control"]["harmful_action_rate"] == 0
    for c in ("poison", "attribution", "conflict", "temporal"):
        assert lww["by_category"][c]["harmful_action_rate"] > 0.5, c


def test_retraction_awareness_helps_exactly_where_expected():
    lww, lr = REF["lww"], REF["lww_retract"]
    assert lr["by_category"]["withdrawal"]["harmful_action_rate"] == 0 < lww["by_category"]["withdrawal"]["harmful_action_rate"]
    assert lr["by_category"]["derived"]["harmful_action_rate"] == 0 < lww["by_category"]["derived"]["harmful_action_rate"]
    # but it is naive about authority, origin and class: still harmful on poison, attribution and cross-origin correction
    assert lr["by_category"]["poison"]["harmful_action_rate"] >= 0.8
    assert lr["by_category"]["attribution"]["harmful_action_rate"] == 1
    # and it over-reacts to an unauthorised withdrawal (unnecessary deferral)
    assert lr["by_category"]["unauthorised"]["unnecessary_deferral_rate"] > 0
    assert lr["overall"]["normalised_cost"] < lww["overall"]["normalised_cost"]


def test_stale_plan_fails_only_plan_dependent_points_more_than_lww():
    assert REF["stale_plan"]["by_category"]["plan-dependency"]["harmful_action_rate"] > REF["lww"]["by_category"]["plan-dependency"]["harmful_action_rate"]
    assert REF["stale_plan"]["by_category"]["recency"]["harmful_action_rate"] == 0


def test_every_risk_stratum_separates_lww_from_oracle_and_recency_does_not():
    risk = [s for s in SCN if score.STRATA["risk"](s)]
    rec = [s for s in SCN if score.STRATA["recency"](s)]
    r = policies.reference_results(risk)
    assert r["lww"]["overall"]["harmful_action_rate"] > r["oracle"]["overall"]["harmful_action_rate"] + 0.4
    rr = policies.reference_results(rec)
    assert rr["lww"]["overall"]["exact_match"] == 1
    assert rr["always_ask"]["overall"]["unnecessary_deferral_rate"] == 1


def test_profile_changes_oracle_and_lww_scores():
    only = [BY_ID["RA-023"], BY_ID["RA-007"]]
    off = policies.reference_results(only, "self_update_off")
    assert off["oracle"]["overall"]["cost"] == 0
    assert off["lww"]["overall"]["harmful_action_rate"] > 0, "LWW is wrong when self_update is off (gold becomes ask)"
    src = policies.reference_results(only, "authority_source")
    assert src["lww"]["overall"]["harmful_action_rate"] > 0


# ------------------------------------------------------------------ statistics


def test_bootstrap_is_deterministic_and_brackets_the_point():
    a = score.bootstrap_ci(REF["lww"], "harmful_action_rate", B=300, seed=7)
    b = score.bootstrap_ci(REF["lww"], "harmful_action_rate", B=300, seed=7)
    assert a == b and a["lo"] <= a["point"] <= a["hi"]


def test_paired_difference_lww_vs_oracle_excludes_zero_on_risk_stratum():
    ids = score.scenario_ids_for(SCN, "risk")
    d = score.paired_diff_ci(REF["lww"], REF["oracle"], "harmful_action_rate", ids, B=500, seed=1)
    assert d["lo"] > 0


def test_paired_difference_is_zero_for_identical_systems():
    d = score.paired_diff_ci(REF["lww"], REF["lww"], "normalised_cost", B=200, seed=1)
    assert d["point"] == 0 and d["lo"] == 0 == d["hi"]


def test_holm():
    assert score.holm({"a": 0.001, "b": 0.02, "c": 0.04}) == {"a": True, "b": True, "c": True}
    assert score.holm({"a": 0.01, "b": 0.03, "c": 0.04}) == {"a": True, "b": False, "c": False}
    assert score.holm({"a": 0.001, "b": 0.5}) == {"a": True, "b": False}
    assert score.holm({"a": 0.03, "b": 0.04}) == {"a": False, "b": False}


def test_cli_roundtrip(tmp_path, capsys):
    f = tmp_path / "resp.json"
    f.write_text(json.dumps(policies.run_policy(policies.lww, SCN)))
    score.main(["--responses", str(f), "--split", "test", "--bootstrap", "50"])
    out = json.loads(capsys.readouterr().out)
    assert out["overall"]["n"] == sum(len(s["decision_points"]) for s in SCN if s["split"] == "test")
    assert "ci" in out


# ------------------------------------------------------------------ estimator, freeze tool, doc sync


def test_cost_estimator_fits_budget_and_makes_no_calls(capsys):
    import estimate_cost as ec

    ec.main(["--json"])
    out = json.loads(capsys.readouterr().out)
    total_ceiling = sum(g["ceiling"] for g in out["grids_assumed_vs_ceiling_usd"].values())
    assert total_ceiling <= out["usable_usd"], "pre-registered grids must fit the usable budget at the pessimistic ceiling"
    t = out["test"]["table"]
    assert t["gpt-oss-20b|graphiti"]["usd_per_scenario_assumed"] > 5 * t["gpt-oss-20b|palimem_typed"]["usd_per_scenario_assumed"]
    assert t["gpt-oss-20b|raw_log"]["tokens_in_per_scenario"] < t["gpt-oss-20b|mem0"]["tokens_in_per_scenario"]


def test_freeze_detects_changes(tmp_path):
    import shutil

    import freeze

    root = tmp_path / "agent"
    shutil.copytree(BENCH, root, ignore=shutil.ignore_patterns("__pycache__", "MANIFEST.sha256"))
    manifest = tmp_path / "MANIFEST.sha256"
    manifest.write_text(freeze.build(root))
    assert freeze.check(root, manifest) == []
    target = root / "scenarios" / "RA-001.json"
    target.write_text(target.read_text().replace("tessaly", "tessalyy", 1))
    assert freeze.check(root, manifest) == ["changed: scenarios/RA-001.json"]
    (root / "scenarios" / "RA-999.json").write_text("{}")
    assert "unfrozen file: scenarios/RA-999.json" in freeze.check(root, manifest)
    assert freeze.check(root, tmp_path / "absent") == ["no manifest: benchmark not frozen"]


def test_doc_inventory_is_in_sync_with_scenarios():
    import inventory

    doc = (BENCH.parents[1] / "docs" / "eval" / "AGENT_BENCHMARK.md").read_text()
    block = doc.split(inventory.BEGIN)[1].split(inventory.END)[0].strip()
    assert block == inventory.table(), "run: python bench/agent/inventory.py --splice"
    n_points = sum(len(s["decision_points"]) for s in SCN)
    assert f"{len(SCN)} scenarios, {n_points} decision points" in doc
