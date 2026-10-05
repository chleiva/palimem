"""The harness checks must report "cannot run" (not raise) when no frozen data exists on the machine.

CI's plain ``test`` job has no study checkout and no deposit zip, so ``frozen.locate_frozen`` raises
``FrozenError`` there; a check that lets it escape fails the job instead of being skipped (the failure of
``test (3.11)`` on the push of 2026-10-05).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness import frozen
from tests.conformance import checks


def test_compat_authority_check_reports_unavailable_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def no_data(*_a: object, **_k: object) -> Path:
        raise frozen.FrozenError("frozen Setting 1 data not found.")

    monkeypatch.setattr(checks, "REPO", tmp_path)  # no .cache/frozen under this root
    monkeypatch.setattr(frozen, "locate_frozen", no_data)
    ok, why = checks.compat_authority_coincide()
    assert ok is None
    assert "not available" in why
