"""Re-run or re-score a REGISTERED RETRACT-ACT run under the registered product behaviour (commit 034d520).

    python bench/agent/registered_rerun.py llm rescore <run.json> --cache <cache.jsonl>
    python bench/agent/registered_rerun.py symbolic --split dev --system justified --out <file> [--backend memory]

The first word picks the registered runner (``llm`` = ``llm_agent.py``, ``symbolic`` = ``palimem_system.py``); the
rest is passed through unchanged. Everything runs inside ``registered_product_v1()``. A NEW run on current main is made
with the runners directly (without this wrapper), never with it. The registered adapters are not edited.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import llm_agent
import palimem_system
from registered_product_v1 import REGISTERED_COMMIT, registered_product_v1

RUNNERS = {"llm": llm_agent.main, "symbolic": palimem_system.main}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in RUNNERS:
        print(__doc__, file=sys.stderr)
        return 2
    runner = RUNNERS[args[0]]
    print(f"[registered product v1: behaviour of commit {REGISTERED_COMMIT}]", file=sys.stderr)
    with registered_product_v1():
        return runner(args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
