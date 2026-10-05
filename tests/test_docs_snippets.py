"""The README quickstart is real: every ```python block under '## 6. Quickstart' runs, and the ```text block that follows
it is its exact output. A doc that drifts from the code fails here."""

from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def blocks(path: Path, heading: str) -> list[tuple[str, str]]:
    """(python source, expected output) pairs from the section that starts at ``heading``."""
    text = path.read_text(encoding="utf-8")
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    section = text[start : end if end != -1 else None]
    pairs = re.findall(r"```python\n(.*?)```\n\s*```text\n(.*?)```", section, re.DOTALL)
    return [(src, out) for src, out in pairs]


def test_readme_quickstart_runs_and_prints_what_it_says(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # "agent.db" is created here
    pairs = blocks(ROOT / "README.md", "## 6. Quickstart")
    assert len(pairs) == 2, "the quickstart has two runnable snippets, each followed by its output"
    scope: dict[str, object] = {}  # one namespace: the second snippet continues the first
    for src, expected in pairs:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exec(compile(src, "<readme>", "exec"), scope)  # noqa: S102
        assert buf.getvalue() == expected
    assert (tmp_path / "agent.db").exists()


def test_agent_guide_session_snippet_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    text = (ROOT / "docs" / "AGENT_GUIDE.md").read_text(encoding="utf-8")
    src = re.search(r"```python\n(.*?)```", text, re.DOTALL)
    assert src is not None
    scope: dict[str, object] = {}
    exec(compile(src.group(1), "<agent-guide>", "exec"), scope)  # noqa: S102
    tools = scope["tools"]
    assert [s.name for s in tools.tool_specs()] == ["remember", "recall", "retract", "explain"]  # type: ignore[attr-defined]
