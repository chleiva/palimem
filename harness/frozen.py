"""Checksum guard for the frozen evaluation set (T-E2).

The design speaks of a single ``frozen.json``. The study repo has no such file: the frozen
Setting 1 set is 500 generated streams under ``data/setting1/`` (``s1_NNNN.json`` stream,
``s1_NNNN.gold.json`` oracle gold, ``s1_NNNN.hidden.json`` hidden world) plus ``_stats.json``,
published in the Zenodo deposit. ``frozen_manifest.json`` in this directory is the pinned record:
the SHA-256 of every one of those files, cross-checked against the deposit's own
``MANIFEST.sha256`` when it was built. The harness refuses to run on any file that does not match.

Silent gold drift (274 of 6,737 entries differed from the frozen gold in the study) is the failure
this guards against.

CLI::

    python -m harness.frozen verify [--dir DIR]       # check every file, report extras
    python -m harness.frozen build-manifest --zip Z   # rebuild the manifest from the deposit zip
    python -m harness.frozen fetch [--dest DIR]       # download + verify the deposit, extract Setting 1
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
MANIFEST_PATH = HERE / "frozen_manifest.json"
CACHE_DIR = REPO_ROOT / ".cache" / "frozen" / "setting1"

DEPOSIT_PREFIX = "palimpsest-v1.0/"
FROZEN_PREFIX = "data/setting1/"
# Published Zenodo record (DOI 10.5281/zenodo.23127764). The same value is pinned in the study's
# scripts/fetch_release_data.py. NB: the zip in the study checkout (~/palimpsest/palimpsest-v1.0.zip)
# is a later rebuild with a different hash; only the Zenodo one is accepted here.
ZENODO_URL = "https://zenodo.org/records/23127764/files/palimpsest-v1.0.zip?download=1"
ZENODO_SHA256 = "686cf0bbf43f2cf7177aa84abaf5e27397b9543ae5363854545b26bcbf077dc6"


class FrozenError(Exception):
    pass


@dataclass(frozen=True)
class Problem:
    path: str
    kind: str  # "mismatch" | "missing" | "unexpected"
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.kind}: {self.path} {self.detail}".strip()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str | os.PathLike[str]) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(path: str | os.PathLike[str] | None = None) -> dict:
    m = json.loads(Path(path or MANIFEST_PATH).read_text())
    if m.get("schema") != 1 or "files" not in m:
        raise FrozenError("unrecognised manifest format")
    return m


def verify_files(root: str | os.PathLike[str], manifest: dict, only: list[str] | None = None) -> list[Problem]:
    """Check files under ``root`` against the manifest. With ``only`` (relative names) just those
    files are checked; otherwise every manifest file is checked and unexpected files are reported."""
    root = Path(root)
    files: dict[str, str] = manifest["files"]
    names = only if only is not None else sorted(files)
    problems: list[Problem] = []
    for name in names:
        if name not in files:
            problems.append(Problem(name, "unexpected", "not in the frozen manifest"))
            continue
        p = root / name
        if not p.is_file():
            problems.append(Problem(name, "missing"))
        elif sha256_file(p) != files[name]:
            problems.append(Problem(name, "mismatch", f"expected {files[name][:12]}…, got {sha256_file(p)[:12]}…"))
    if only is None and root.is_dir():
        for p in sorted(root.iterdir()):
            if p.is_file() and p.name not in files:
                problems.append(Problem(p.name, "unexpected", "not in the frozen manifest"))
    return problems


def require_valid(root: str | os.PathLike[str], manifest: dict | None = None, only: list[str] | None = None) -> None:
    problems = verify_files(root, manifest or load_manifest(), only)
    if problems:
        shown = "\n  ".join(str(p) for p in problems[:10])
        more = f"\n  … and {len(problems) - 10} more" if len(problems) > 10 else ""
        raise FrozenError(f"frozen set check failed ({len(problems)} problem(s)):\n  {shown}{more}")


def _deposit_manifest_lines(z: zipfile.ZipFile) -> dict[str, str]:
    """Hashes the deposit itself records for data/setting1/ (its MANIFEST.sha256)."""
    out: dict[str, str] = {}
    text = z.read(DEPOSIT_PREFIX + "MANIFEST.sha256").decode()
    for line in text.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            rel = parts[1].strip().removeprefix("./")
            if rel.startswith(FROZEN_PREFIX):
                out[rel[len(FROZEN_PREFIX):]] = parts[0]
    return out


def build_manifest(zip_path: str | os.PathLike[str]) -> dict:
    """Build the manifest from the Zenodo deposit zip, cross-checked against its MANIFEST.sha256."""
    blob = Path(zip_path).read_bytes()
    digest = sha256_bytes(blob)
    if digest != ZENODO_SHA256:
        raise FrozenError(f"zip SHA-256 {digest} is not the published Zenodo hash {ZENODO_SHA256}")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        files = {}
        for info in z.infolist():
            if info.is_dir() or not info.filename.startswith(DEPOSIT_PREFIX + FROZEN_PREFIX):
                continue
            files[info.filename[len(DEPOSIT_PREFIX + FROZEN_PREFIX):]] = sha256_bytes(z.read(info))
        recorded = _deposit_manifest_lines(z)
        bad = {k: v for k, v in recorded.items() if files.get(k) != v}
        if bad:
            raise FrozenError(f"{len(bad)} file(s) disagree with the deposit's own MANIFEST.sha256")
        cross = len(recorded)
    return {
        "schema": 1,
        "description": "SHA-256 of every file in data/setting1/ of the PALIMPSEST v1.0 Zenodo deposit",
        "source": {"doi": "10.5281/zenodo.23127764", "zenodo_url": ZENODO_URL, "zip_sha256": ZENODO_SHA256,
                   "cross_checked_against_deposit_MANIFEST_sha256": cross},
        "files": dict(sorted(files.items())),
    }


def extract_setting1(zip_path: str | os.PathLike[str], dest: str | os.PathLike[str]) -> int:
    """Extract data/setting1/ from a deposit zip whose hash is the published one."""
    blob = Path(zip_path).read_bytes()
    return _extract_blob(blob, Path(dest))


def _extract_blob(blob: bytes, dest: Path) -> int:
    digest = sha256_bytes(blob)
    if digest != ZENODO_SHA256:
        raise FrozenError(f"zip SHA-256 {digest} is not the published Zenodo hash; refusing to unpack")
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for info in z.infolist():
            if info.is_dir() or not info.filename.startswith(DEPOSIT_PREFIX + FROZEN_PREFIX):
                continue
            (dest / info.filename[len(DEPOSIT_PREFIX + FROZEN_PREFIX):]).write_bytes(z.read(info))
            n += 1
    return n


def fetch_deposit(dest: str | os.PathLike[str] = CACHE_DIR) -> int:
    with urllib.request.urlopen(ZENODO_URL, timeout=600) as r:
        blob = r.read()
    return _extract_blob(blob, Path(dest))


def locate_frozen(study_dir: str | os.PathLike[str] | None = None, explicit: str | os.PathLike[str] | None = None,
                  fetch: bool = False) -> Path:
    """Find a directory holding the frozen Setting 1 files. The returned directory is *not* yet
    verified; callers verify the files they use (``require_valid``)."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("PALIMPSEST_FROZEN_DIR"):
        candidates.append(Path(os.environ["PALIMPSEST_FROZEN_DIR"]))
    if study_dir:
        candidates.append(Path(study_dir) / "data" / "setting1")
    candidates.append(CACHE_DIR)
    for c in candidates:
        if (c / "s1_0000.json").is_file():
            return c
    zips = [Path(p) for p in (os.environ.get("PALIMPSEST_DEPOSIT_ZIP"),
                              Path.home() / "Downloads" / "palimpsest-v1.0.zip",
                              Path(study_dir) / "palimpsest-v1.0.zip" if study_dir else None) if p]
    for z in zips:
        if z.is_file() and sha256_file(z) == ZENODO_SHA256:
            extract_setting1(z, CACHE_DIR)
            return CACHE_DIR
    if fetch:
        fetch_deposit(CACHE_DIR)
        return CACHE_DIR
    raise FrozenError(
        "frozen Setting 1 data not found. Provide --frozen-dir / PALIMPSEST_FROZEN_DIR, or the Zenodo "
        "deposit zip (PALIMPSEST_DEPOSIT_ZIP; SHA-256 must be " + ZENODO_SHA256[:12] + "…), or pass --fetch "
        "to download it (~37 MB)."
    )


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness.frozen")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--dir")
    v.add_argument("--study-dir", default=os.environ.get("PALIMPSEST_STUDY_DIR", str(Path.home() / "palimpsest")))
    v.add_argument("--fetch", action="store_true")
    b = sub.add_parser("build-manifest")
    b.add_argument("--zip", required=True)
    b.add_argument("--out", default=str(MANIFEST_PATH))
    f = sub.add_parser("fetch")
    f.add_argument("--dest", default=str(CACHE_DIR))
    a = ap.parse_args(argv)
    try:
        if a.cmd == "verify":
            root = locate_frozen(a.study_dir, a.dir, a.fetch)
            problems = verify_files(root, load_manifest())
            for p in problems[:50]:
                print(p)
            n = len(load_manifest()["files"])
            print(f"{root}: {'OK' if not problems else 'FAILED'} ({n} files in manifest, {len(problems)} problem(s))")
            return 1 if problems else 0
        if a.cmd == "build-manifest":
            m = build_manifest(a.zip)
            Path(a.out).write_text(json.dumps(m, indent=1) + "\n")
            print(f"wrote {a.out}: {len(m['files'])} files, cross-checked {m['source']['cross_checked_against_deposit_MANIFEST_sha256']} against the deposit MANIFEST.sha256")
            return 0
        n = fetch_deposit(a.dest)
        print(f"extracted {n} files to {a.dest}")
        return 0
    except FrozenError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
