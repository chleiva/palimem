"""Pinned cached data of the study's Settings 2 and 3 (T-J5), with the same checksum discipline as ``harness.frozen``.

Setting 1 is replayable from the frozen streams alone. Settings 2 and 3 are different in kind: their systems did not
see the streams but **extracted claims** (an LLM read the natural-language rendering of every report once and the
study cached the result), so the replayable input is the cached ``extracted.json`` of every stream, the stream file
that carries the queries and the domain, and the study's own ``answers.json`` / ``frozen.json`` (its store's answers on
the same claims, and the frozen gold). No model is called: everything below is deposit data.

Per stream the files used are::

    data/s2/<sid>/{extracted,frozen,answers}.json        Setting 2 (100 streams, the registered evaluation set)
    data/s2_strong/<sid>/{extracted,frozen,answers}.json Setting 2, stronger backbone (15 streams)
    data/s3/<sid>/{extracted,frozen,answers}.json        Setting 3 (30 Wikidata-derived streams)
    data/setting2_100/<sid>.json                         the stream (queries, domain, sources) for s2 and s2_strong
    data/setting3/<sid>.json                             the stream for s3

``s23_manifest.json`` records the SHA-256 of every one of those files, cross-checked against the deposit's own
``MANIFEST.sha256`` when it was built; the replay refuses a file that does not match. Only the published Zenodo
deposit is accepted (the zip inside a study checkout is a later rebuild with a different hash).

CLI::

    python -m harness.studydata build-manifest --zip Z   # rebuild the manifest from the published deposit zip
    python -m harness.studydata verify [--dir DIR]       # check every file under DIR
    python -m harness.studydata fetch [--dest DIR] [--zip Z]   # extract the used files (from a local zip, else Zenodo)
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

from harness.frozen import (
    DEPOSIT_PREFIX,
    REPO_ROOT,
    ZENODO_SHA256,
    ZENODO_URL,
    FrozenError,
    Problem,
    sha256_bytes,
    sha256_file,
)

HERE = Path(__file__).resolve().parent
MANIFEST_PATH = HERE / "s23_manifest.json"
CACHE_DIR = REPO_ROOT / ".cache" / "frozen" / "s23"

CACHE_FILES = ("extracted.json", "frozen.json", "answers.json")
# dataset name -> (cache directory in the deposit, directory of the stream files, stream id prefix)
DATASETS = {
    "s2": ("data/s2/", "data/setting2_100/", "s2_"),
    "s2_strong": ("data/s2_strong/", "data/setting2_100/", "s2_"),
    "s3": ("data/s3/", "data/setting3/", "s3_"),
}


def wanted(rel: str) -> bool:
    """Is this deposit-relative path one of the files the replay uses?"""
    for cache, base, prefix in DATASETS.values():
        if rel.startswith(cache):
            parts = rel[len(cache):].split("/")
            return len(parts) == 2 and parts[0].startswith(prefix) and parts[1] in CACHE_FILES
        if rel.startswith(base):
            name = rel[len(base):]
            return "/" not in name and name.startswith(prefix) and name.endswith(".json") and name.count(".") == 1
    return False


def load_manifest(path: str | os.PathLike[str] | None = None) -> dict:
    return json.loads(Path(path or MANIFEST_PATH).read_text())


def _deposit_manifest(z: zipfile.ZipFile) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in z.read(DEPOSIT_PREFIX + "MANIFEST.sha256").decode().splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            out[parts[1].strip().removeprefix("./")] = parts[0]
    return out


def build_manifest(zip_path: str | os.PathLike[str]) -> dict:
    blob = Path(zip_path).read_bytes()
    digest = sha256_bytes(blob)
    if digest != ZENODO_SHA256:
        raise FrozenError(f"zip SHA-256 {digest} is not the published Zenodo hash {ZENODO_SHA256}")
    files: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        recorded = _deposit_manifest(z)
        for info in z.infolist():
            if info.is_dir() or not info.filename.startswith(DEPOSIT_PREFIX):
                continue
            rel = info.filename[len(DEPOSIT_PREFIX):]
            if wanted(rel):
                files[rel] = sha256_bytes(z.read(info))
        bad = {k: v for k, v in files.items() if recorded.get(k) != v}
        if bad:
            raise FrozenError(f"{len(bad)} file(s) disagree with, or are missing from, the deposit's MANIFEST.sha256")
    return {
        "schema": 1,
        "description": "SHA-256 of the cached Setting 2 / Setting 3 files the replay uses, from the PALIMPSEST v1.0 Zenodo deposit",
        "source": {"doi": "10.5281/zenodo.23127764", "zenodo_url": ZENODO_URL, "zip_sha256": ZENODO_SHA256,
                   "cross_checked_against_deposit_MANIFEST_sha256": len(files)},
        "files": dict(sorted(files.items())),
    }


def verify_files(root: str | os.PathLike[str], manifest: dict, only: list[str] | None = None) -> list[Problem]:
    root = Path(root)
    problems: list[Problem] = []
    for rel, want in manifest["files"].items():
        if only is not None and rel not in only:
            continue
        p = root / rel
        if not p.is_file():
            problems.append(Problem(rel, "missing"))
        elif sha256_file(p) != want:
            problems.append(Problem(rel, "mismatch", "SHA-256 differs from the pinned deposit hash"))
    return problems


def require_valid(root: str | os.PathLike[str], manifest: dict | None = None, only: list[str] | None = None) -> None:
    problems = verify_files(root, manifest or load_manifest(), only)
    if problems:
        shown = "\n  ".join(str(p) for p in problems[:10])
        more = f"\n  … and {len(problems) - 10} more" if len(problems) > 10 else ""
        raise FrozenError(f"Setting 2/3 data check failed ({len(problems)} problem(s)):\n  {shown}{more}")


def _extract_blob(blob: bytes, dest: Path) -> int:
    digest = sha256_bytes(blob)
    if digest != ZENODO_SHA256:
        raise FrozenError(f"zip SHA-256 {digest} is not the published Zenodo hash; refusing to unpack")
    n = 0
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for info in z.infolist():
            if info.is_dir() or not info.filename.startswith(DEPOSIT_PREFIX):
                continue
            rel = info.filename[len(DEPOSIT_PREFIX):]
            if wanted(rel):
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(info))
                n += 1
    return n


def extract_from_zip(zip_path: str | os.PathLike[str], dest: str | os.PathLike[str] = CACHE_DIR) -> int:
    return _extract_blob(Path(zip_path).read_bytes(), Path(dest))


def fetch_deposit(dest: str | os.PathLike[str] = CACHE_DIR) -> int:
    with urllib.request.urlopen(ZENODO_URL, timeout=600) as r:
        blob = r.read()
    return _extract_blob(blob, Path(dest))


def _zip_candidates(study_dir: str | os.PathLike[str] | None) -> list[Path]:
    return [Path(p) for p in (os.environ.get("PALIMPSEST_DEPOSIT_ZIP"),
                              Path.home() / "Downloads" / "palimpsest-v1.0.zip",
                              Path(study_dir) / "palimpsest-v1.0.zip" if study_dir else None) if p]


def locate(study_dir: str | os.PathLike[str] | None = None, explicit: str | os.PathLike[str] | None = None,
           fetch: bool = False) -> Path:
    """Find a directory that holds the Setting 2/3 files (laid out as in the deposit: ``<dir>/data/s2/...``). The
    returned directory is *not* yet verified; callers verify what they use (``require_valid``)."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("PALIMPSEST_S23_DIR"):
        candidates.append(Path(os.environ["PALIMPSEST_S23_DIR"]))
    candidates.append(CACHE_DIR)
    probe = "data/s2/s2_1000/extracted.json"
    for c in candidates:
        if (c / probe).is_file():
            return c
    for z in _zip_candidates(study_dir):
        if z.is_file() and sha256_file(z) == ZENODO_SHA256:
            extract_from_zip(z, CACHE_DIR)
            return CACHE_DIR
    if fetch:
        fetch_deposit(CACHE_DIR)
        return CACHE_DIR
    raise FrozenError(
        "Setting 2/3 cached data not found. Provide --s23-dir / PALIMPSEST_S23_DIR, or the published Zenodo deposit "
        "zip (PALIMPSEST_DEPOSIT_ZIP; SHA-256 must be " + ZENODO_SHA256[:12] + "…), or pass --fetch to download it "
        "(~37 MB)."
    )


def stream_ids(root: str | os.PathLike[str], dataset: str) -> list[str]:
    cache, _base, prefix = DATASETS[dataset]
    d = Path(root) / cache
    return sorted(p.name for p in d.iterdir() if p.is_dir() and p.name.startswith(prefix) and (p / "extracted.json").is_file())


def paths_of(root: str | os.PathLike[str], dataset: str, sid: str) -> dict[str, Path]:
    cache, base, _prefix = DATASETS[dataset]
    root = Path(root)
    return {
        "stream": root / base / f"{sid}.json",
        "extracted": root / cache / sid / "extracted.json",
        "frozen": root / cache / sid / "frozen.json",
        "answers": root / cache / sid / "answers.json",
    }


def needed_rel(dataset: str, sid: str) -> list[str]:
    cache, base, _prefix = DATASETS[dataset]
    return [f"{base}{sid}.json", *(f"{cache}{sid}/{f}" for f in CACHE_FILES)]


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness.studydata")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build-manifest")
    b.add_argument("--zip", required=True)
    b.add_argument("--out", default=str(MANIFEST_PATH))
    v = sub.add_parser("verify")
    v.add_argument("--dir")
    v.add_argument("--study-dir", default=os.environ.get("PALIMPSEST_STUDY_DIR", str(Path.home() / "palimpsest")))
    v.add_argument("--fetch", action="store_true")
    f = sub.add_parser("fetch")
    f.add_argument("--dest", default=str(CACHE_DIR))
    f.add_argument("--zip")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "build-manifest":
            m = build_manifest(a.zip)
            Path(a.out).write_text(json.dumps(m, indent=1) + "\n")
            print(f"wrote {a.out}: {len(m['files'])} files, each cross-checked against the deposit MANIFEST.sha256")
            return 0
        if a.cmd == "verify":
            root = locate(a.study_dir, a.dir, a.fetch)
            m = load_manifest()
            problems = verify_files(root, m)
            for p in problems[:50]:
                print(p)
            print(f"{root}: {'OK' if not problems else 'FAILED'} ({len(m['files'])} files in manifest, {len(problems)} problem(s))")
            return 1 if problems else 0
        n = extract_from_zip(a.zip, a.dest) if a.zip else fetch_deposit(a.dest)
        print(f"extracted {n} files to {a.dest}")
        return 0
    except FrozenError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
