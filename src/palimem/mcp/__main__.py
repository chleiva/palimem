"""``python -m palimem.mcp DB --principal agent:name``: the same as ``palimem mcp``."""

from __future__ import annotations

import sys

from palimem.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["mcp", *sys.argv[1:]]))
