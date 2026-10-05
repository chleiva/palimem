"""Pin the REGISTERED product behaviour (commit 034d520) for the RETRACT-ACT benchmark.

The registered runs (symbolic: docs/eval/AGENT_BENCHMARK_RESULTS.md; LLM-in-the-loop: AGENT_BENCHMARK_LLM_RESULTS.md)
were produced before Lane Q's intentional product changes. Three of them reach the benchmark's adapters:

1. the ``recall`` rendering of attribution-only evidence (``palimem.agent.render``): frozen in
   ``registered_render_v1.py``;
2. the policy's attribution safety rule (``decide`` never commits to a ``belief_of`` candidate): disabled by making
   its ``BeliefOfForm`` test never match;
3. the product-profile authority default (``AdmissionConfig.failed_correction_is_allege``: a failed cross-source
   ``correct`` now becomes ``allege``): the adapters build ``AdmissionConfig(profile=OPEN_WORLD)``, which is wrapped to
   pass ``failed_correction_is_allege=False`` (the registered behaviour: a competing assertion with a correction cue).

``registered_product_v1()`` applies all three inside a ``with`` block and restores the originals on exit, so nothing
outside the block (the product, the other tests) sees any change. It is meant for the offline re-score tests and for
``registered_rerun.py``; a NEW run on current main must NOT use it (it would hide the product changes it is meant to
measure). The registered adapters (``llm_agent.py``, ``llm_systems.py``, ``palimem_system.py``) are not modified:
their hashes are part of the registration.
"""
from __future__ import annotations

import contextlib
import functools
import importlib
import sys
from collections.abc import Iterator
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

REGISTERED_COMMIT = "034d520"


class _NeverMatches:
    """Stands in for ``BeliefOfForm`` inside ``palimem.policy.policy`` so ``isinstance(c.form, ...)`` is always False."""


@contextlib.contextmanager
def registered_product_v1() -> Iterator[None]:
    from palimem.admission import AdmissionConfig

    ps = importlib.import_module("palimem_system")  # the module the registered adapters use (top-level, via sys.path)
    frozen = importlib.import_module("registered_render_v1")
    tools = importlib.import_module("palimem.agent.tools")
    policy = importlib.import_module("palimem.policy.policy")

    saved = {
        (ps, "AdmissionConfig"): ps.AdmissionConfig,
        (tools, "answer_json"): tools.answer_json,
        (tools, "answer_text"): tools.answer_text,
        (policy, "BeliefOfForm"): policy.BeliefOfForm,
    }
    ps.AdmissionConfig = functools.partial(AdmissionConfig, failed_correction_is_allege=False)
    tools.answer_json = frozen.answer_json
    tools.answer_text = frozen.answer_text
    policy.BeliefOfForm = _NeverMatches
    try:
        yield
    finally:
        for (mod, name), value in saved.items():
            setattr(mod, name, value)


__all__ = ["REGISTERED_COMMIT", "registered_product_v1"]
