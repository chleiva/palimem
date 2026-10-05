"""The FULL pipeline differential with the candidate fast kernel on the base-key path (Lane B10, T-B10).

    python -m harness.fast_pipeline_diff [pipeline_diff options]

``harness.pipeline_diff`` feeds the frozen Setting 1 streams through ``palimem.Memory`` (admission, kernel, store,
policy, v1 adapter) and compares every answer (and, with ``--provenance strict``, every provenance set) with the study
gold. This wrapper swaps the pipeline's single ``justify_key`` call for the fast kernel's dispatch (fast inside the
proven class, enumeration outside it) and then runs the unchanged differential: identical answers on all 30,272
queries are a promotion criterion. All of ``pipeline_diff``'s options pass through.
"""

from __future__ import annotations

import sys
from typing import Any

from harness import pipeline_diff
from harness.fast_kernel_diff import _Either
from palimem.engine import pipeline as pipeline_mod
from palimem.kernel.fast import dispatch_key


def _fast_justify_key(ks: Any, key: Any, entries: Any, semantic: Any, *, budget: int, change_from: Any = None) -> Any:
    return dispatch_key(ks, key, entries, semantic, budget=budget, change_from=change_from).result


def main(argv: list[str] | None = None) -> int:
    pipeline_mod.justify_key = _fast_justify_key  # type: ignore[assignment]
    # pipeline_diff asserts isinstance(j, Justification | DerivedJustification); the fast justification has the same
    # read interface but no common base class (a promotion gap: a shared Protocol), so accept both here
    pipeline_diff.Justification = _Either  # type: ignore[misc,assignment]
    return int(pipeline_diff.main(argv))


if __name__ == "__main__":
    sys.exit(main())
