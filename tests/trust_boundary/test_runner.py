"""The trust-boundary fixtures through the host tier and the agent tool API, with a ratchet (status.json)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.trust_boundary.runner import load_fixtures, run_fixture

STATUS = json.loads((Path(__file__).parent / "status.json").read_text())
FAILING: dict[str, str] = STATUS["failing"]
FIXTURES = load_fixtures()


def test_every_fixture_is_covered() -> None:
    assert len(FIXTURES) == 24
    ids = {f["id"] for f in FIXTURES}
    assert set(FAILING) <= ids, "status.json lists a fixture that does not exist"
    assert all(FAILING.values()), "every failing fixture needs its cause"


@pytest.mark.parametrize("fx", FIXTURES, ids=[f["id"] for f in FIXTURES])
def test_fixture(fx: dict[str, Any]) -> None:
    problems = run_fixture(fx)
    if fx["id"] in FAILING:
        assert problems, f"{fx['id']} now passes: remove it from status.json 'failing' (the ratchet only moves forward)"
    else:
        assert not problems, "\n".join(problems)
