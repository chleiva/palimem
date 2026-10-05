"""Gold v1.1 (the RETRACT-ACT erratum overlay): what it changes, and, as important, what it must not.

Offline and symbolic only: no model, no network, no money. The registered scenario files, `score.py`, the adapters and the
registered runs are never edited; the overlay is applied to a deep copy. These tests pin:

* the machine-readable errata and the human record (`gold_errata.md`) say the same thing;
* the overlay changes exactly one decision point (RA-026.d1), in every gold profile, and refuses a scenario set it was not
  written against;
* the registered hashes are intact, and the test split is byte-identical under v1.1 (RA-026 is a dev scenario);
* re-scoring under the REGISTERED gold reproduces the stored registered tables exactly (so the re-scoring path is trusted);
* every cell that moves on dev moves because of RA-026.d1 and nothing else;
* the annotation's agreement with the gold, under both golds.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1] / "bench" / "agent"
for p in (BENCH, BENCH / "annotation"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import gold_overlay as go
import palimem_system as ps
import score

ITEM = "RA-026.d1"
SCENARIO_FILES = sorted((BENCH / "scenarios").glob("RA-*.json"))


@pytest.fixture(scope="module", autouse=True)
def _few_bootstrap_draws():
    """The tests compare point estimates and registered-vs-v1.1 equality, not interval endpoints: 200 draws are plenty."""
    old = go.B
    go.B = 200
    yield
    go.B = old


@pytest.fixture(scope="module")
def reg() -> list[dict]:
    return go.registered_scenarios()


@pytest.fixture(scope="module")
def v11() -> list[dict]:
    return go.v1_1_scenarios()


def _points(scns: list[dict]) -> dict[str, dict]:
    return {p["id"]: p for s in scns for p in s["decision_points"]}


def _sha(paths: list[Path]) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


# ------------------------------------------------------------------------------------------------- the record


def test_the_json_companion_and_the_markdown_record_agree():
    js, md = go.load_errata(), go.parse_markdown()
    assert [(e["item"], e["old"], e["new"]) for e in js["errata"]] == [(e["item"], e["old"], e["new"]) for e in md["errata"]]
    assert [(k["item"], k["gold"], k["annotator"]) for k in js["kept"]] == [(k["item"], k["gold"], k["annotator"]) for k in md["kept"]]
    assert all(e["adjudicator"] == "author" for e in md["errata"]) and all(k["adjudicator"] == "author" for k in md["kept"])
    assert go.check() == []


def test_the_erratum_is_exactly_the_one_the_author_adjudicated():
    e = go.load_errata()
    assert [x["item"] for x in e["errata"]] == [ITEM]
    assert e["errata"][0]["old"] == {"action": "act", "value": "london"}
    assert e["errata"][0]["new"] == {"action": "ask", "value": None}
    assert {k["item"] for k in e["kept"]} == {"RA-006.d1", "RA-023.d1"}


# ------------------------------------------------------------------------------------------------- the overlay


def test_the_overlay_changes_exactly_one_point_in_every_gold_profile(reg, v11):
    a, b = _points(reg), _points(v11)
    assert a.keys() == b.keys()
    changed = []
    for pid in a:
        for profile in go.PROFILES:
            ga, gb = score.gold_for(a[pid], profile), score.gold_for(b[pid], profile)
            if (ga["action"], score._norm(ga.get("value"))) != (gb["action"], score._norm(gb.get("value"))):
                changed.append((pid, profile))
    assert sorted(changed) == [(ITEM, p) for p in sorted(go.PROFILES)]
    for profile in go.PROFILES:
        g = score.gold_for(b[ITEM], profile)
        assert g["action"] == "ask" and score._norm(g.get("value")) is None
    # the adjudicated-and-kept items and the profile-specific golds are carried through unchanged
    assert score.gold_for(b["RA-006.d1"]) == score.gold_for(a["RA-006.d1"])
    assert score.gold_for(b["RA-023.d1"]) == score.gold_for(a["RA-023.d1"]) == a["RA-023.d1"]["gold"]
    assert score.gold_for(b["RA-023.d1"], "self_update_off") == a["RA-023.d1"]["gold_by_profile"]["self_update_off"]
    assert score.gold_for(b["RA-007.d1"], "authority_source") == a["RA-007.d1"]["gold_by_profile"]["authority_source"]


def test_the_overlay_works_on_a_copy_and_leaves_the_registered_files_alone(reg, tmp_path):
    before = _sha(SCENARIO_FILES)
    snapshot = copy.deepcopy(reg)
    out = go.apply(reg)
    assert reg == snapshot, "apply() must not modify its input"
    assert _points(out)[ITEM]["gold"]["action"] == "ask" and _points(reg)[ITEM]["gold"]["action"] == "act"
    d = go.materialise(tmp_path / "v1.1")
    assert _sha(SCENARIO_FILES) == before, "the registered scenario files are untouched"
    written = {s["id"]: s for s in score.load_scenarios(d)}
    assert _points(list(written.values()))[ITEM]["gold"]["action"] == "ask"
    assert sorted(p.name for p in d.glob("RA-*.json")) == [p.name for p in SCENARIO_FILES]


def test_the_overlay_refuses_a_scenario_set_it_was_not_written_against(reg):
    wrong = copy.deepcopy(reg)
    _points(wrong)[ITEM]["gold"] = {"action": "act", "value": "paris", "rationale": "x"}
    with pytest.raises(go.OverlayError):
        go.apply(wrong)
    only_test = [s for s in reg if s["split"] == "test"]
    with pytest.raises(go.OverlayError):
        go.apply(only_test)  # strict: the erratum's scenario is missing
    assert go.apply(only_test, strict=False) == only_test  # the test split carries no erratum


def test_the_loader_swap_is_confined_and_always_restored():
    original = score.load_scenarios
    with go.gold_v1_1():
        assert _points(score.load_scenarios(split="dev"))[ITEM]["gold"]["action"] == "ask"
        assert _points(score.load_scenarios(split="test")).keys() <= _points(go.registered_scenarios()).keys()
    assert score.load_scenarios is original
    assert _points(score.load_scenarios(split="dev"))[ITEM]["gold"]["action"] == "act"
    with pytest.raises(RuntimeError), go.gold_v1_1():
        raise RuntimeError("boom")
    assert score.load_scenarios is original


# ------------------------------------------------------------------------------------------------- registration intact


def test_registered_hashes_are_unchanged_and_the_test_split_is_identical_under_v1_1(reg, v11):
    stored = json.loads((BENCH / "results" / "test_runs.json").read_text())
    test_reg = [s for s in reg if s["split"] == "test"]
    test_new = [s for s in v11 if s["split"] == "test"]
    for name, rec in stored.items():
        assert rec["scenarios_sha256"] == ps.scenarios_hash(test_reg), f"registered scenario hash of run {name}"
    assert ps.scenarios_hash(test_new) == ps.scenarios_hash(test_reg), "v1.1 does not touch a single test scenario"
    assert ps.scenarios_hash([s for s in v11 if s["split"] == "dev"]) != ps.scenarios_hash([s for s in reg if s["split"] == "dev"])
    assert json.loads((BENCH / "results" / "test_runs.json").read_text()) == stored  # the one-run registry is not rewritten


# ------------------------------------------------------------------------------------------------- re-scoring


@pytest.fixture(scope="module")
def sym() -> dict[str, dict]:
    return {split: go.symbolic(split) for split in ("dev", "test")}


def test_rescoring_under_the_registered_gold_reproduces_the_stored_test_tables(sym):
    stored = json.loads((BENCH / "results" / "tables-test.json").read_text())
    mine = sym["test"]["profiles"]["default"]["registered"]
    checked = 0
    for name, entry in stored["systems"].items():
        for metric in ("harmful_action_rate", "unnecessary_deferral_rate", "exact_match", "normalised_cost"):
            a, b = entry["ci"]["all"][metric]["point"], mine[name]["all"][metric]["point"]
            assert (a is None and b is None) or abs(a - b) < 1e-12, (name, metric, a, b)
            checked += 1
    assert checked >= 36


def test_no_test_split_cell_moves_under_v1_1(sym):
    for profile, golds in sym["test"]["profiles"].items():
        for system, reg in golds["registered"].items():
            assert reg == golds[go.VERSION][system], (profile, system)


def test_every_dev_cell_that_moves_moves_because_of_ra_026_d1_alone(reg, v11):
    """Drop RA-026 from both scenario sets: every stored symbolic run then scores identically under the two golds."""
    dev_reg = [s for s in reg if s["split"] == "dev" and s["id"] != "RA-026"]
    dev_new = [s for s in v11 if s["split"] == "dev" and s["id"] != "RA-026"]
    assert dev_reg == dev_new
    runs = [json.loads(p.read_text())["responses"] for p in sorted((BENCH / "results").glob("dev-*.json"))
            if (BENCH / "results" / p.name).exists() and "responses" in json.loads(p.read_text())]
    assert len(runs) >= 4
    for resp in runs:
        for profile in go.PROFILES:
            assert score.score(dev_reg, resp, profile)["overall"] == score.score(dev_new, resp, profile)["overall"]


def test_the_expected_dev_cells_move_and_the_rest_do_not(sym):
    g = sym["dev"]["profiles"]["default"]
    moved = {s for s in g["registered"] if g["registered"][s]["all"] != g[go.VERSION][s]["all"]}
    assert moved == {"always_ask", "palimem_justified", "palimem_justified_su_off"}
    pj_old, pj_new = g["registered"]["palimem_justified"], g[go.VERSION]["palimem_justified"]
    assert pj_old["RA-026.d1"]["chosen"] == pj_new["RA-026.d1"]["chosen"] == "ask"
    assert not pj_old["RA-026.d1"]["exact"] and pj_new["RA-026.d1"]["exact"]
    assert pj_old["all"]["exact_match"]["point"] == pytest.approx(15 / 16) and pj_new["all"]["exact_match"]["point"] == 1.0
    for acting in ("lww", "lww_retract", "stale_plan", "palimem_lww", "palimem_recency"):
        assert g[go.VERSION][acting]["RA-026.d1"]["harmful"] and g["registered"][acting]["RA-026.d1"]["harmful"]


def test_llm_runs_the_test_split_is_unchanged_and_dev_moves_only_through_ra_026_d1():
    test, dev = go.llm("test"), go.llm("dev")
    assert len(test["runs"]) == 6
    for name, e in test["runs"].items():
        assert e["registered"] == e[go.VERSION], name
    changed = [n for n, e in dev["runs"].items() if e["registered"] != e[go.VERSION]]
    assert changed and all(n.startswith(("dev-v1", "dev-v2")) for n in changed)
    for e in dev["runs"].values():
        assert e[go.VERSION]["RA-026.d1"], "every dev run answered RA-026.d1"


# ------------------------------------------------------------------------------------------------- the annotation


def test_agreement_of_the_stored_annotation_under_both_golds(tmp_path):
    import make_pack

    annotations = BENCH / "annotation" / "annotations-2cb1a5b5.json"
    _, private = make_pack.build_pack()
    stored = json.loads(annotations.read_text())
    assert stored["pack_hash"] == private["pack_hash"], "the stored annotation is for exactly this pack"
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps(private))
    k = go.kappa_both(annotations, mapping, write=False)["profiles"]["default"]
    a, b = k["registered"]["summaries"], k[go.VERSION]["summaries"]
    assert (a["all"]["agreement_k"], a["all"]["n"]) == (27, 29) and a["all"]["kappa"] == pytest.approx(0.866, abs=5e-4)
    assert (b["all"]["agreement_k"], b["all"]["n"]) == (26, 29) and b["all"]["kappa"] == pytest.approx(0.803, abs=5e-4)
    assert a["test_25"]["agreement"] == b["test_25"]["agreement"] == pytest.approx(0.92)
    assert a["test_25"]["kappa"] == b["test_25"]["kappa"]
    assert [d["decision_point"] for d in k["registered"]["disagreements"]] == ["RA-006.d1", "RA-023.d1"]
    assert sorted(d["decision_point"] for d in k[go.VERSION]["disagreements"]) == ["RA-006.d1", "RA-023.d1", ITEM]
    new = next(d for d in k[go.VERSION]["disagreements"] if d["decision_point"] == ITEM)
    assert new["gold"] == "ask" and new["annotator"] == "act london"
