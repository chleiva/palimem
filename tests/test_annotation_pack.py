"""Tests for the blind second-annotation pack and the agreement tooling (bench/agent/annotation)."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1] / "bench" / "agent"
sys.path.insert(0, str(BENCH))
sys.path.insert(0, str(BENCH / "annotation"))

import kappa
import make_pack
import narrative as nv
import score

RAW = nv.load_raw_scenarios()
RULING = ("RA-006", "RA-007", "RA-026")


def _expected_items() -> set[tuple[str, str]]:
    # derived straight from the scenario files, independently of nv.select_items
    out = set()
    for s in RAW.values():
        if s["split"] == "test" or s["id"] in RULING:
            out |= {(s["id"], d["id"]) for d in s["decision_points"]}
    return out


@pytest.fixture(scope="module")
def pack_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("pack")
    make_pack.write_pack(out=d)
    return d


# ------------------------------------------------------------------------------------------ what is in the pack


def test_pack_holds_every_test_point_plus_the_ruling_items():
    pack, private = make_pack.build_pack()
    expected = _expected_items()
    assert len(expected) == 29
    assert pack["n_items"] == 29
    assert {(m["scenario"], m["decision_point"]) for m in private["items"].values()} == expected
    test_points = {(s["id"], d["id"]) for s in RAW.values() if s["split"] == "test" for d in s["decision_points"]}
    assert len(test_points) == 25 and test_points <= expected
    assert {sid for sid, _ in expected - test_points} == {"RA-007", "RA-026"}


def test_order_is_seeded_and_ids_are_opaque_and_unique():
    a, pa = make_pack.build_pack(1)
    a2, _ = make_pack.build_pack(1)
    b, _ = make_pack.build_pack(2)
    assert a == a2 and a["pack_hash"] == a2["pack_hash"]
    assert [i["id"] for i in a["items"]] != [i["id"] for i in b["items"]] or [i["position"] for i in a["items"]] == [i["position"] for i in b["items"]]
    assert a["pack_hash"] != b["pack_hash"]
    ids = [i["id"] for i in a["items"]]
    assert len(set(ids)) == len(ids) == 29
    assert all(re.fullmatch(r"I-[0-9a-f]{6}", i) for i in ids)
    assert all("RA" not in i for i in ids)
    assert pa["pack_hash"] == a["pack_hash"]


def test_each_item_shows_only_what_was_known_at_that_moment():
    pack, private = make_pack.build_pack()
    for it in pack["items"]:
        ref = private["items"][it["id"]]
        scn = RAW[ref["scenario"]]
        order = [r["id"] for r in scn["reports"]]
        dp = next(d for d in scn["decision_points"] if d["id"] == ref["decision_point"])
        n_visible = order.index(dp["after_report"]) + 1
        shown = [e for e in it["events"] if e["label"].startswith("Report ")]
        assert [e["label"] for e in shown] == [f"Report {i}" for i in range(1, n_visible + 1)]
        # actions only appear when they had already been taken
        for e in it["events"]:
            if e["label"] == "Action taken":
                assert any(x["after_report"] in order[:n_visible] for x in scn["executed_actions"])
        assert it["actions"] == ["act", "ask", "abstain", "revalidate"]


def test_narrative_covers_every_cue_form_and_origin_without_failing():
    for sid, s in RAW.items():
        for d in s["decision_points"]:
            it = nv.build_item(s, d["id"])
            assert it["events"] and it["decision"] and it["sources"] and it["attributes"]
            assert all(isinstance(e["text"], str) and e["text"].strip() for e in it["events"]), (sid, d["id"])


# ------------------------------------------------------------------------------------------ blindness


def test_the_generated_pack_is_blind_and_offline(pack_dir):
    html = (pack_dir / make_pack.HTML_NAME).read_text(encoding="utf-8")
    assert make_pack.leak_report(html, RAW) == []
    assert not re.search(r"https?://|src=|@import|<link ", html)
    assert make_pack.check(pack_dir) == 0


def test_no_whitelist_escape_for_any_scenario_or_decision_point():
    """Every string from a field the pack must not reveal is absent from every rendered item."""
    for s in RAW.values():
        banned: list[str] = [s["title"], s["slug"], s["description"], s["id"]]
        label_words = [*s.get("tags", []), s["category"]]  # short generic words: only an exact JSON string value would be a leak
        for d in s["decision_points"]:
            banned += [d["id"]]
            banned += [g["rationale"] for g in [d["gold"], *d.get("gold_by_profile", {}).values()] if g.get("rationale")]
            banned += [json.dumps(d["resolvers"])] if d.get("resolvers") else []
        for d in s["decision_points"]:
            blob = json.dumps(nv.build_item(s, d["id"]), ensure_ascii=False)
            for b in banned:
                assert b not in blob, (s["id"], d["id"], b)
            for w in label_words:
                assert f'"{w}"' not in blob, (s["id"], d["id"], w)
            for key in ("gold", "gold_by_profile", "rationale", "resolvers", "stakes", "costs", "slug", "title", "category", "tags"):
                assert f'"{key}"' not in blob


def test_the_private_mapping_and_gold_are_not_in_the_html(pack_dir):
    html = (pack_dir / make_pack.HTML_NAME).read_text(encoding="utf-8")
    mapping = json.loads((pack_dir / "private" / "mapping.json").read_text())
    for ref in mapping["items"].values():
        assert ref["scenario"] not in html and ref["decision_point"] not in html
    assert "mapping" not in html.lower().replace("mapping file", "")


def test_the_leak_check_catches_planted_leaks(pack_dir):
    html = (pack_dir / make_pack.HTML_NAME).read_text(encoding="utf-8")
    s = RAW["RA-006"]
    rationale = s["decision_points"][0]["gold"]["rationale"]
    assert make_pack.leak_report(html + rationale, RAW)
    assert make_pack.leak_report(html + " RA-006 ", RAW)
    assert make_pack.leak_report(html + s["title"], RAW)
    planted = html.replace('"items":[', '"gold":{"action":"ask"},"items":[', 1)
    assert any("forbidden key" in p for p in make_pack.leak_report(planted, RAW))


def test_the_pack_generator_never_reads_gold(tmp_path):
    """Poisoned gold in a scenario copy must not change the pack at all (whitelist, not delete-list)."""
    scen = tmp_path / "scn"
    scen.mkdir()
    for p in (BENCH / "scenarios").glob("RA-*.json"):
        s = json.loads(p.read_text())
        for d in s["decision_points"]:
            d["gold"] = {"action": "abstain", "rationale": "POISON-GOLD-RATIONALE", "value": "poison-gold-value"}
            d["gold_by_profile"] = {"p": {"action": "ask", "rationale": "POISON-PROFILE-RATIONALE"}}
            d["resolvers"] = ["POISON-RESOLVER"]
        s["title"], s["slug"], s["description"], s["stakes"] = "POISON-TITLE", "poison-slug", "POISON-DESCRIPTION", "high"
        s["tags"], s["category"] = ["POISON-TAG"], "poison-category"
        (scen / p.name).write_text(json.dumps(s))
    poisoned = nv.load_raw_scenarios(scen)
    sample = [(sid, d["id"]) for sid, s in poisoned.items() for d in s["decision_points"]][:12]
    for sid, dp in sample:
        assert nv.build_item(poisoned[sid], dp) == nv.build_item(RAW[sid], dp)
    blob = json.dumps([nv.build_item(poisoned[sid], dp) for sid, dp in sample])
    assert "POISON" not in blob.upper()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_form_javascript_is_syntactically_valid(pack_dir, tmp_path):
    html = (pack_dir / make_pack.HTML_NAME).read_text(encoding="utf-8")
    scripts = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
    assert len(scripts) == 1
    js = tmp_path / "form.js"
    js.write_text(scripts[0])
    r = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr


# ------------------------------------------------------------------------------------------ statistics (hand-computed)


def test_kappa_selftest_passes():
    assert kappa.selftest() == 0


def test_kappa_matches_hand_computed_values():
    a = ["y"] * 25 + ["n"] * 25
    b = ["y"] * 20 + ["n"] * 5 + ["y"] * 10 + ["n"] * 15
    assert kappa.cohen_kappa(a, b, ("y", "n")) == pytest.approx(0.4)  # po .7, pe .5
    cyc = ["act", "ask", "abstain"] * 4
    assert kappa.cohen_kappa(cyc, cyc[1:] + cyc[:1], ("act", "ask", "abstain")) == pytest.approx(-0.5)
    assert kappa.cohen_kappa(["act"] * 3, ["act"] * 3) is None
    assert kappa.cohen_kappa([], []) is None
    lo, hi = kappa.wilson(7, 10)
    assert (lo, hi) == pytest.approx((0.39678, 0.89221), abs=5e-5)
    assert kappa.wilson(0, 0) is None
    with pytest.raises(ValueError):
        kappa.cohen_kappa(["act"], [])


def test_bootstrap_is_seeded_and_brackets_the_point_estimate_for_a_mixed_set():
    gold = ["act", "ask", "abstain", "act", "ask", "act", "ask", "act"] * 3
    ann = list(gold)
    ann[0], ann[5], ann[11] = "ask", "ask", "act"
    k = kappa.cohen_kappa(gold, ann)
    b1 = kappa.bootstrap_kappa(gold, ann, n_boot=500, seed=3)
    b2 = kappa.bootstrap_kappa(gold, ann, n_boot=500, seed=3)
    assert b1 == b2 and b1["lo"] <= k <= b1["hi"]


# ------------------------------------------------------------------------------------------ end to end with synthetic annotations


def _annotation(private, picker):
    items = {}
    for oid, ref in private["items"].items():
        scn = RAW[ref["scenario"]]
        dp = next(d for d in scn["decision_points"] if d["id"] == ref["decision_point"])
        action, value = picker(oid, dp)
        items[oid] = {"action": action, "value": value, "reason": None}
    return {"format": "retract-act-annotation/1", "pack_hash": private["pack_hash"], "annotator": "synthetic", "items": items}


def _run(tmp_path, annotation, private, profile="default"):
    a = tmp_path / "ann.json"
    m = tmp_path / "map.json"
    a.write_text(json.dumps(annotation))
    m.write_text(json.dumps(private))
    return kappa.run(a, m, profile, tmp_path / "rep")


def _gold_picker(profile="default"):
    def pick(_oid, dp):
        g = score.gold_for(dp, profile)
        return g["action"], g.get("value")
    return pick


def test_perfect_annotator_gives_full_agreement_and_no_disagreements(tmp_path):
    _, private = make_pack.build_pack()
    res = _run(tmp_path, _annotation(private, _gold_picker()), private)
    s = res["summaries"]["all"]
    assert s["n"] == 29 and s["agreement"] == 1.0 and s["kappa"] == pytest.approx(1.0)
    assert res["disagreements"] == [] and res["coverage"]["missing"] == []
    assert res["summaries"]["test_25"]["n"] == 25
    assert res["summaries"]["ruling_items_RA-006_007_026"]["n"] == 5
    va = s["value_agreement"]
    assert va["agree"] == va["n_both_with_value"] > 0
    assert (tmp_path / "rep.md").read_text().startswith("# Second-annotation agreement")


def test_adversarial_annotator_never_agrees_and_gets_negative_kappa(tmp_path):
    _, private = make_pack.build_pack()
    rot = {"act": "ask", "ask": "abstain", "abstain": "revalidate", "revalidate": "act"}

    def pick(_oid, dp):
        return rot[dp["gold"]["action"]], None

    res = _run(tmp_path, _annotation(private, pick), private)
    s = res["summaries"]["all"]
    assert s["agreement"] == 0.0 and s["agreement_k"] == 0
    assert s["kappa"] is not None and s["kappa"] < 0
    assert len(res["disagreements"]) == 29
    assert all(d["adjudication"] == "" and d["narrative"] for d in res["disagreements"])
    md = (tmp_path / "rep.md").read_text()
    assert "adjudication (written reason required)" in md and "Gold errata draft" in md


def test_random_annotator_is_seeded_and_not_in_agreement(tmp_path):
    import random

    _, private = make_pack.build_pack()
    rng = random.Random(11)
    picks = {oid: rng.choice(score.ACTIONS) for oid in private["items"]}
    res1 = _run(tmp_path, _annotation(private, lambda oid, dp: (picks[oid], "x" if picks[oid] == "act" else None)), private)
    res2 = _run(tmp_path, _annotation(private, lambda oid, dp: (picks[oid], "x" if picks[oid] == "act" else None)), private)
    assert res1["summaries"] == res2["summaries"]
    s = res1["summaries"]["all"]
    assert s["n"] == 29 and 0.0 < s["agreement"] < 0.7
    assert s["kappa_bootstrap95"]["n_valid"] > 0


def test_incomplete_annotation_is_reported_as_provisional(tmp_path):
    _, private = make_pack.build_pack()
    ann = _annotation(private, _gold_picker())
    for oid in list(ann["items"])[:3]:
        del ann["items"][oid]
    res = _run(tmp_path, ann, private)
    assert res["coverage"]["answered"] == 26 and len(res["coverage"]["missing"]) == 3
    assert "provisional" in (tmp_path / "rep.md").read_text()


def test_a_file_from_another_pack_is_refused(tmp_path):
    _, private = make_pack.build_pack(1)
    ann = _annotation(private, _gold_picker())
    ann["pack_hash"] = "0" * 64
    with pytest.raises(ValueError, match="different packs"):
        _run(tmp_path, ann, private)


def test_the_alternative_gold_profile_for_ra_007_is_compared_and_listed(tmp_path):
    _, private = make_pack.build_pack()
    res = _run(tmp_path, _annotation(private, _gold_picker()), private, profile="authority_source")
    assert [d["decision_point"] for d in res["disagreements"]] == ["RA-007.d1"]
    d = res["disagreements"][0]
    assert d["gold_value"] == "leeds" and d["ann_value"] == "manchester"
    res_default = _run(tmp_path, _annotation(private, _gold_picker()), private)
    assert res_default["disagreements"] == []


def test_value_disagreement_alone_is_listed_for_adjudication(tmp_path):
    _, private = make_pack.build_pack()

    def pick(oid, dp):
        g = score.gold_for(dp, "default")
        if dp["id"] == "RA-007.d1":
            return "act", "somewhere else"
        return g["action"], g.get("value")

    res = _run(tmp_path, _annotation(private, pick), private)
    assert res["summaries"]["all"]["agreement"] == 1.0
    assert [d["decision_point"] for d in res["disagreements"]] == ["RA-007.d1"]
