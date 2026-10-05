"""The README's gate table and docs/GATES.md must state the same status for every gate.

PyPI's 0.1.0 is allowed only after the README gate section matches docs/GATES.md (author's ruling 25, 2026-10-05), so the
two are compared mechanically: the status words are parsed out of both tables and must be equal gate by gate. The test also
pins the sentences the author's rulings require the README to keep saying.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
GATES = (ROOT / "docs" / "GATES.md").read_text(encoding="utf-8")

GATE_IDS = ["G0", "G1", "G2", "G3", "G4", "G-S", "G-X", "G-A"]
STATUS_WORDS = {"met", "partly met", "not met", "not started", "blocked"}
ROW = re.compile(r"^\|\s*\*\*(G[0-4]|G-[SXA])\*\*")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _status(cell: str) -> str:
    return cell.replace("*", "").strip().lower()


def _table(text: str, status_column: int, heading: str) -> dict[str, str]:
    """Rows of the gate table that follows `heading`, as {gate id: status word(s)}."""
    start = text.index(heading)
    nxt = text.find("\n## ", start + len(heading))
    section = text[start : nxt if nxt != -1 else len(text)]
    out: dict[str, str] = {}
    for line in section.splitlines():
        m = ROW.match(line)
        if m:
            out[m.group(1)] = _status(_cells(line)[status_column])
    return out


def test_every_gate_has_a_status_word_in_both_documents() -> None:
    gates = _table(GATES, 2, "## 1. Summary")
    readme = _table(README, 1, "## 8. Gate status")
    assert sorted(gates) == sorted(GATE_IDS)
    assert sorted(readme) == sorted(GATE_IDS)
    for table in (gates, readme):
        for gate, word in table.items():
            assert word in STATUS_WORDS, f"{gate}: {word!r} is not one of {sorted(STATUS_WORDS)}"


def test_the_readme_and_the_gate_document_state_the_same_status_for_every_gate() -> None:
    gates = _table(GATES, 2, "## 1. Summary")
    readme = _table(README, 1, "## 8. Gate status")
    drift = {g: (readme[g], gates[g]) for g in GATE_IDS if readme[g] != gates[g]}
    assert not drift, f"README versus docs/GATES.md: {drift}"


def test_the_readme_says_what_the_rulings_require_it_to_say() -> None:
    gate_section = README[README.index("## 8. Gate status") : README.index("## 9. ")]
    # the G-X failure, with its numbers, and the models that were not run
    assert "0.235" in gate_section and "0.20" in gate_section
    assert "4 of 17" in gate_section
    assert "fails the declared gate" in gate_section
    assert "Ministral" in gate_section and "not run" in gate_section
    # the missed performance targets are named
    for target in ("T2", "T6", "T7"):
        assert target in gate_section or target in README, target
    assert "T3" in gate_section
    # the second annotation is not described as human
    assert "model third opinion" in README
    # ruling 20: the agent-level evidence is only claimed for a compliant reader of the kernel's text
    assert "compliant reader of the kernel's text" in README
    assert "no human annotation" in gate_section.lower() or "no human annotation" in README.lower()


def test_the_documents_agree_on_the_g_x_numbers() -> None:
    for text in (README, GATES):
        assert "0.235" in text
        assert "4 of 17" in text or "4 dropped of 17" in text
