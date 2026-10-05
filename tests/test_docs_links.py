"""Documentation hygiene: every relative link in a tracked Markdown file resolves, and every document under docs/ is
listed in the docs index (docs/README.md). Standard library only; no network (http(s) links are not checked)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SKIP_DIRS = {".git", ".venv", ".cache", ".private", ".claude", ".bench", "node_modules", "build", "dist"}

FENCE = re.compile(r"^(```|~~~).*?^\1[ \t]*$", re.DOTALL | re.MULTILINE)
INLINE_CODE = re.compile(r"`[^`\n]*`")
LINK = re.compile(r"(?<!\!)\[(?:[^\]\n]|\[[^\]\n]*\])*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
IMAGE = re.compile(r"!\[[^\]\n]*\]\(([^)\s]+)")


def tracked_markdown() -> list[Path]:
    """Markdown files in the working tree that git knows (or, outside a checkout, a filesystem walk)."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "*.md"],
            cwd=ROOT, capture_output=True, check=True, text=True,
        ).stdout
        paths = [ROOT / p for p in out.split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        paths = [p for p in ROOT.rglob("*.md")]
    return sorted(p for p in paths if p.is_file() and not (set(p.relative_to(ROOT).parts) & SKIP_DIRS))


def strip_code(text: str) -> str:
    """Links in code fences and code spans are examples, not links."""
    return INLINE_CODE.sub("", FENCE.sub("", text))


def slug(heading: str) -> str:
    """GitHub-style anchor for a heading (lowercase, punctuation dropped, spaces to hyphens)."""
    s = re.sub(r"`", "", heading.strip().lower())
    s = re.sub(r"[^\w\- ]", "", s, flags=re.UNICODE)
    return s.replace(" ", "-")


def anchors_of(path: Path) -> set[str]:
    text = FENCE.sub("", path.read_text(encoding="utf-8"))
    return {slug(m.group(2)) for m in re.finditer(r"^(#{1,6})\s+(.+?)\s*#*\s*$", text, re.MULTILINE)}


def relative_links(path: Path) -> list[str]:
    text = strip_code(path.read_text(encoding="utf-8"))
    found = [m.group(1) for m in LINK.finditer(text)] + [m.group(1) for m in IMAGE.finditer(text)]
    return [t for t in found if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", t) and not t.startswith("//")]


def test_every_relative_link_resolves() -> None:
    broken: list[str] = []
    for md in tracked_markdown():
        for target in relative_links(md):
            file_part, _, fragment = target.partition("#")
            dest = md if not file_part else (md.parent / file_part).resolve()
            where = f"{md.relative_to(ROOT)} -> {target}"
            if not dest.exists():
                broken.append(f"{where} (no such file)")
            elif fragment and dest.suffix == ".md" and slug(fragment) not in anchors_of(dest):
                broken.append(f"{where} (no such heading)")
    assert not broken, "broken links:\n  " + "\n  ".join(broken)


def test_the_docs_index_lists_every_document() -> None:
    index = DOCS / "README.md"
    assert index.is_file(), "docs/README.md (the index of documents) is missing"
    linked = {
        (index.parent / t.partition("#")[0]).resolve()
        for t in relative_links(index)
        if t.partition("#")[0]
    }
    missing = sorted(
        str(p.relative_to(ROOT))
        for p in DOCS.rglob("*.md")
        if p.resolve() != index.resolve() and p.resolve() not in linked
    )
    assert not missing, "documents not listed in docs/README.md:\n  " + "\n  ".join(missing)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("see [a](docs/x.md) and [b](https://example.com/y.md)", ["docs/x.md"]),
        ("```\n[not a link](nowhere.md)\n```\n[real](real.md#top)", ["real.md#top"]),
        ("inline `[code](nowhere.md)` and [ok](./ok.md)", ["./ok.md"]),
        ("![img](pic.png) and [mail](mailto:a@b.c)", ["pic.png"]),
    ],
)
def test_link_extraction(text: str, expected: list[str], tmp_path: Path) -> None:
    f = tmp_path / "t.md"
    f.write_text(text, encoding="utf-8")
    assert relative_links(f) == expected


def test_heading_slugs_match_github() -> None:
    assert slug("7. Measured so far, and what it does not show") == "7-measured-so-far-and-what-it-does-not-show"
    assert slug("`Memory` and the `Answer`") == "memory-and-the-answer"
