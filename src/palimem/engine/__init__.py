"""The revision pipeline: admission and kernel plugged into the store (Lane M).

* :class:`Pipeline` holds the configuration shared by the two stages and the caches.
* :class:`StoreAdmitter` adapts :class:`palimem.admission.Admitter` to the store's admission interface.
* :class:`KernelReviser` adapts :mod:`palimem.kernel` to the store's ``Reviser`` protocol.

See ``docs/PIPELINE.md``.
"""

from palimem.engine.logview import ViewLog
from palimem.engine.pipeline import (
    KernelReviser,
    Pipeline,
    RuleDepthError,
    StoreAdmitter,
    attribution_support,
    derivation_depths,
    direct_entries,
    family_of_segment,
)

__all__ = [
    "KernelReviser",
    "Pipeline",
    "RuleDepthError",
    "StoreAdmitter",
    "ViewLog",
    "attribution_support",
    "derivation_depths",
    "direct_entries",
    "family_of_segment",
]
