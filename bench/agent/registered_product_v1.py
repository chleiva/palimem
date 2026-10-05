"""Pin the REGISTERED product behaviour (commit 034d520) for the RETRACT-ACT benchmark.

The registered runs (symbolic: docs/eval/AGENT_BENCHMARK_RESULTS.md; LLM-in-the-loop: AGENT_BENCHMARK_LLM_RESULTS.md)
were produced before Lane Q's intentional product changes. Five of them reach the benchmark's adapters:

1. the ``recall`` rendering of attribution-only evidence (``palimem.agent.render``): frozen in
   ``registered_render_v1.py``;
2. the policy's attribution safety rule (``decide`` never commits to a ``belief_of`` candidate): disabled by making
   its ``BeliefOfForm`` test never match;
3. the product-profile authority default (``AdmissionConfig.failed_correction_is_allege``: a failed cross-source
   ``correct`` now becomes ``allege``): the adapters build ``AdmissionConfig(profile=OPEN_WORLD)``, which is wrapped to
   pass ``failed_correction_is_allege=False`` (the registered behaviour: a competing assertion with a correction cue).

4. negative evidence (ruling 4 of 2026-10-05): the registered kernel refused ``not_value`` / ``not_member`` reports
   (``KernelUnsupported``; RA-012 was answered by no palimem system): ``palimem.kernel.polarity.justify_polarity`` is
   replaced by a function that refuses them again;
5. an authorised dispute's kernel effect (ruling 3): the adapters' ``AdmissionConfig`` is wrapped to also pass
   ``dispute_is_denial=False`` (the registered behaviour: a dispute had no kernel effect).

6. the inquiry (ruling 16 of 2026-10-05): an ``ask`` carried only ``competing`` (and the attribution key) and an
   ``abstain`` carried none; ``palimem.policy.policy.ENRICH_INQUIRY`` is switched off to reproduce that;
7. the default agent-session policy label (ruling 16): the registered default was ``p-default`` (it asked); the default
   is now ``p-ask`` (the host default ``p-default`` abstains), so the frozen ``recall`` renderer is given the registered
   label.

``registered_product_v1()`` applies all seven inside a ``with`` block and restores the originals on exit, so nothing
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
    polarity = importlib.import_module("palimem.kernel.polarity")
    from palimem.kernel.spec import KernelUnsupported

    def _refuse_negative_evidence(*_a: object, **_k: object) -> object:
        raise KernelUnsupported("negative evidence is unsupported (the registered product, before the 2026-10-05 ruling)")

    saved = {
        (ps, "AdmissionConfig"): ps.AdmissionConfig,
        (tools, "answer_json"): tools.answer_json,
        (tools, "answer_text"): tools.answer_text,
        (policy, "BeliefOfForm"): policy.BeliefOfForm,
        (policy, "ENRICH_INQUIRY"): policy.ENRICH_INQUIRY,
        (polarity, "justify_polarity"): polarity.justify_polarity,
    }
    ps.AdmissionConfig = functools.partial(AdmissionConfig, failed_correction_is_allege=False, dispute_is_denial=False)
    polarity.justify_polarity = _refuse_negative_evidence
    def _registered_label(fn: object) -> object:
        def wrapped(*a: object, **k: object) -> object:
            if k.get("policy_label") == "p-ask":
                k = {**k, "policy_label": "p-default"}
            return fn(*a, **k)  # type: ignore[operator]

        return wrapped

    tools.answer_json = _registered_label(frozen.answer_json)
    tools.answer_text = frozen.answer_text
    policy.BeliefOfForm = _NeverMatches
    policy.ENRICH_INQUIRY = False
    try:
        yield
    finally:
        for (mod, name), value in saved.items():
            setattr(mod, name, value)


__all__ = ["REGISTERED_COMMIT", "registered_product_v1"]
