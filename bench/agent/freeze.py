"""Freeze manifest for RETRACT-ACT: SHA-256 of every file that defines the benchmark.

    python bench/agent/freeze.py            # write bench/agent/MANIFEST.sha256 (do this once, at freeze)
    python bench/agent/freeze.py --check    # exit 1 if any frozen file changed (CI and the paid runner call this)

Frozen: scenarios/RA-*.json, schema.json, score.py. Adapters, prompts and policies are versioned
separately (their hashes go in each run record). Standard library only.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "MANIFEST.sha256"


def frozen_files(root: Path = HERE) -> list[Path]:
    return sorted([*(root / "scenarios").glob("RA-*.json"), root / "schema.json", root / "score.py"])


def digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build(root: Path = HERE) -> str:
    return "".join(f"{digest(p)}  {p.relative_to(root).as_posix()}\n" for p in frozen_files(root))


def check(root: Path = HERE, manifest: Path = MANIFEST) -> list[str]:
    """Return a list of problems (empty = frozen set intact)."""
    if not manifest.exists():
        return ["no manifest: benchmark not frozen"]
    want = dict(line.split("  ", 1)[::-1] for line in manifest.read_text().splitlines() if line.strip())
    have = {p.relative_to(root).as_posix(): digest(p) for p in frozen_files(root)}
    problems = [f"changed: {k}" for k in want if k in have and have[k] != want[k]]
    problems += [f"missing: {k}" for k in want if k not in have]
    problems += [f"unfrozen file: {k}" for k in have if k not in want]
    return problems


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--check" in argv:
        problems = check()
        for p in problems:
            print(p)
        print("frozen set intact" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0
    MANIFEST.write_text(build())
    print(f"wrote {MANIFEST} ({len(frozen_files())} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
