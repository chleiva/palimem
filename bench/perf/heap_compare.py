"""Compare two heap reports (``bench.perf heap``) retained-bytes-per-report by file and line.

``python -m bench.perf.heap_compare BASE.json OTHER.json`` prints traced current/peak bytes per report and the
largest files and lines; used for the whole-log versus incremental admission comparison (docs/PERFORMANCE.md).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    base, other = (json.loads(Path(p).read_text()) for p in argv[:2])
    n = int(other["params"]["n_reports"])
    for k in ("traced_current_bytes", "traced_peak_bytes"):
        print(f"{k}: {base[k] / n / 1024:.2f} -> {other[k] / n / 1024:.2f} KiB per report")
    bf = {r["file"]: r["size_bytes"] for r in base["by_file"]}
    of = {r["file"]: r["size_bytes"] for r in other["by_file"]}
    print("by file (bytes per report):")
    for f in sorted(set(bf) | set(of), key=lambda f: -max(bf.get(f, 0), of.get(f, 0)))[:9]:
        print(f"  {f:52s} {bf.get(f, 0) / n:8.0f} -> {of.get(f, 0) / n:8.0f}")
    bl = {r["where"]: r["size_bytes"] for r in base["by_line"]}
    ol = {r["where"]: r["size_bytes"] for r in other["by_line"]}
    print("top lines of the second report (bytes per report; first report in brackets):")
    for w, s in sorted(ol.items(), key=lambda kv: -kv[1])[:8]:
        print(f"  {w:56s} {s / n:7.0f}  [{bl.get(w, 0) / n:.0f}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
