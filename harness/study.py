"""Locate and import the PALIMPSEST study repo (read-only dependency of the harness)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

PIN_PATH = Path(__file__).with_name("study_pin.json")


def pin() -> dict:
    return json.loads(PIN_PATH.read_text())


def study_dir(explicit: str | os.PathLike[str] | None = None) -> Path:
    d = Path(explicit or os.environ.get("PALIMPSEST_STUDY_DIR") or Path.home() / "palimpsest").expanduser()
    if not (d / "palimpsest" / "core.py").is_file() or not (d / "revise_stream" / "oracle_v1.py").is_file():
        raise FileNotFoundError(
            f"{d} is not a PALIMPSEST study checkout (set PALIMPSEST_STUDY_DIR). "
            f"Expected {pin()['repo']} at {pin()['commit'][:7]}."
        )
    return d


def commit_of(d: Path) -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=d, capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def load(explicit: str | os.PathLike[str] | None = None) -> SimpleNamespace:
    """Import the study modules. Returns a namespace with the classes the harness uses."""
    d = study_dir(explicit)
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))
    from baselines.structured import LogQueryTime
    from eval.scorer import norm
    from palimpsest.core import BeliefStore
    from revise_stream.model import load_stream

    return SimpleNamespace(
        dir=d, commit=commit_of(d), BeliefStore=BeliefStore, LogQueryTime=LogQueryTime,
        load_stream=load_stream, norm=norm,
    )
