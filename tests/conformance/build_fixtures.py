"""Generate the conformance fixtures and their schemas.

    python -m tests.conformance.build_fixtures          # write
    python -m tests.conformance.build_fixtures --check  # fail if the files on disk differ (CI)

The JSON files under ``fixtures/`` are the deliverable (language-neutral). This script exists so that
they stay consistent; a test runs ``--check`` so nobody edits a generated file by hand.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import fx_independent, fx_other
from .schema import fixture_schema, index_schema

ROOT = Path(__file__).parent
FIXTURES = ROOT / "fixtures"


def group_of(fid: str) -> str:
    if fid.startswith("ind-"):
        return "independent"
    if fid.startswith("sec-"):
        return "security"
    return "decisions"


def all_fixtures() -> list[dict[str, Any]]:
    out = [*fx_independent.build(), *fx_other.s06(), *fx_other.s10(), *fx_other.compat(), *fx_other.compat_harness(), *fx_other.security()]
    ids = [f["id"] for f in out]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise SystemExit(f"duplicate fixture ids: {sorted(dup)}")
    return out


def dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def expected_files() -> dict[Path, str]:
    files: dict[Path, str] = {
        ROOT / "fixture.schema.json": dumps(fixture_schema()),
        ROOT / "index.schema.json": dumps(index_schema()),
        FIXTURES / "security" / "index.json": dumps(fx_other.security_index()),
    }
    for f in all_fixtures():
        files[FIXTURES / group_of(f["id"]) / f"{f['id']}.json"] = dumps(f)
    return files


def main(argv: list[str]) -> int:
    files = expected_files()
    if "--check" in argv:
        bad = [str(p.relative_to(ROOT)) for p, text in files.items() if not p.exists() or p.read_text() != text]
        on_disk = {p for g in ("independent", "decisions", "security") for p in (FIXTURES / g).glob("*.json") if p.name != "index.json"}
        stale = sorted(str(p.relative_to(ROOT)) for p in on_disk - set(files))
        if bad or stale:
            print("out of date:", bad, "stale:", stale)
            return 1
        print(f"{len(files)} generated files are in sync")
        return 0
    for p, text in files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    print(f"wrote {len(files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
