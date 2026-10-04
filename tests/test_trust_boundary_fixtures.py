"""Structural check of the trust-boundary conformance fixtures (no runner exists yet)."""
import json
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "trust_boundary"
FILES = sorted(FIXTURE_DIR.glob("tb-*.json"))
SETUP_KEYS = {"profile", "schema", "connectors", "authority", "session", "events", "log", "now"}
EXPECT_KEYS = {"steps", "log_delta", "audit_delta", "answers", "equal_results"}


def test_at_least_eight_fixtures():
    assert len(FILES) >= 8


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_fixture_is_well_formed(path):
    d = json.loads(path.read_text())
    assert d["id"] == path.stem
    assert d["title"] and d["covers"]
    assert SETUP_KEYS <= set(d["setup"])
    assert d["steps"], "a fixture needs at least one step"
    for s in d["steps"]:
        assert s["tier"] in ("agent", "host")
        assert isinstance(s["args"], dict)
    exp = d["expect"]
    assert set(exp) <= EXPECT_KEYS
    if "steps" in exp:
        assert len(exp["steps"]) == len(d["steps"])
        for e in exp["steps"]:
            assert ("result" in e) ^ ("error" in e)
    for ref in [r["ref"] for r in d["setup"]["log"] if "ref" in r]:
        assert ref.startswith("r")
    agent_calls = {s["call"] for s in d["steps"] if s["tier"] == "agent"}
    assert agent_calls <= {"remember", "recall", "retract", "correct", "dispute", "explain"}


def test_agent_authority_scenarios_present():
    ids = {p.stem for p in FILES}
    for needed in ("tb-15", "tb-16", "tb-17", "tb-18"):
        assert any(i.startswith(needed) for i in ids)
