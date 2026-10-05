"""The registered RETRACT-ACT runs stay reproducible after the product changed (Lane Q), and the change is bounded.

``bench/agent/registered_product_v1.py`` pins the product behaviour of commit 034d520 inside a ``with`` block. These tests
check, offline and symbolically (no model, no money):

* every REGISTERED symbolic result (``bench/agent/results/{dev,test}-<system>.json``) is reproduced exactly under the pin;
* the pin is confined: after the block the product's own functions are back;
* on CURRENT main (no pin) the registered responses differ at exactly the documented decision points, nothing else, so a
  further product change that moves more of the benchmark is noticed instead of silently re-defining the baseline.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1] / "bench" / "agent"
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))

import palimem_system as ps
import score
from registered_product_v1 import registered_product_v1

RESULTS = BENCH / "results"
SYSTEMS = ("justified", "recency", "lww", "justified_su_off")
# decision points whose response differs between the registered product (034d520) and current main, per split.
# Lane Q made a failed correction a total `allege` (RA-007 dev and RA-006 test then differed). The author's ruling of
# 2026-10-05 (S-02) restored the content half: a correction without authority loses its effect on the target but its
# proposition is admitted as an assert from its own source, so for these two scenarios current main answers exactly as
# the registered product did (RA-006 `ask`, RA-007 `act manchester`). The remaining difference is in the gold, not the
# response (RA-007's `authority_source` gold; see docs/eval/RA-007_TRACE.md). Any further drift must be documented here.
# Ruling 4 (negative evidence in the product kernel) answers RA-012, which no palimem system answered when the runs were
# registered (the kernel refused the denial, a recorded gap): current main answers `ask`, which is RA-012's gold. The pin
# restores the refusal, so the registered runs still reproduce.
EXPECTED_DIFFERENCES: dict[str, set[tuple[str, str]]] = {"dev": set(), "test": {("RA-012", "RA-012.d1")}}


def _registered(split: str, system: str) -> dict:
    return json.loads((RESULTS / f"{split}-{system}.json").read_text())


def _differences(old: dict, new: dict) -> set[tuple[str, str]]:
    return ({(sc, p) for sc, pts in old["responses"].items() for p, v in pts.items() if new["responses"].get(sc, {}).get(p) != v}
            | {(sc, p) for sc, pts in new["responses"].items() for p, v in pts.items() if old["responses"].get(sc, {}).get(p) != v})


@pytest.mark.parametrize("split", ["dev", "test"])
@pytest.mark.parametrize("system", SYSTEMS)
def test_registered_symbolic_runs_reproduce_under_the_pin(split: str, system: str) -> None:
    old = _registered(split, system)
    scns = score.load_scenarios(split=split)
    assert [s["id"] for s in scns] == old["scenario_ids"]
    with registered_product_v1():
        new = ps.run_system(old["system"], scns, "memory")
    assert new["responses"] == old["responses"]
    assert sorted(new["errors"]) == sorted(old["errors"])


def test_the_pin_is_confined_to_its_block() -> None:
    from palimem.agent import tools
    from palimem.policy import policy

    before = (ps.AdmissionConfig, tools.answer_json, tools.answer_text, policy.BeliefOfForm)
    with registered_product_v1():
        assert (ps.AdmissionConfig, tools.answer_json, tools.answer_text, policy.BeliefOfForm) != before
    assert (ps.AdmissionConfig, tools.answer_json, tools.answer_text, policy.BeliefOfForm) == before


def test_the_pin_is_restored_when_the_block_raises() -> None:
    from palimem.agent import tools

    before = tools.answer_text
    with pytest.raises(RuntimeError), registered_product_v1():
        raise RuntimeError("boom")
    assert tools.answer_text is before


@pytest.mark.parametrize("split", ["dev", "test"])
def test_current_main_differs_from_the_registered_runs_exactly_where_documented(split: str) -> None:
    old = _registered(split, "justified")
    scns = score.load_scenarios(split=split)
    new = ps.run_system("justified", scns, "memory")  # NO pin: the product as it is now
    assert _differences(old, new) == EXPECTED_DIFFERENCES[split]


def test_the_committed_current_main_column_matches() -> None:
    doc = json.loads((RESULTS / "current-main-symbolic.json").read_text())
    for row in doc["rows"]:
        got = {tuple(x) for x in row["differing_points"]}
        assert got == EXPECTED_DIFFERENCES[row["split"]], (row["split"], row["system"])
