import importlib
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

oracle_bridge = importlib.import_module("research.r41.oracle_bridge")

needs_study = pytest.mark.skipif(
    not oracle_bridge.available(),
    reason="study repo (revise_stream) not found; set PALIMPSEST_STUDY_DIR",
)
