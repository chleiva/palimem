"""The ``palimem`` CLI: golden output over a small store, export/import round trip, verify, and error handling."""

from __future__ import annotations

import io
import json
import re
import sqlite3
from pathlib import Path

import pytest

from palimem import Memory
from palimem.cli import main
from palimem.types import Attr, AttrClass, Schema, ValueType


def schema() -> Schema:
    return Schema(version=1, attrs=(
        Attr(name="employer", attr_class=AttrClass.SINGLE_CHANGEABLE, value_type=ValueType.STRING, inertia=True),
    ))


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "agent.db"
    with Memory(path, schema=schema()) as m:
        r1 = m.observe({"entity": "alice", "attr": "employer", "value": "Acme"}, source="hr", origin_group="g_hr")
        m.observe({"entity": "bob", "attr": "employer", "value": "Globex"}, source="hr", origin_group="g_hr")
        assert r1.report_id is not None
        m.withdraw(r1.report_id, actor="connector:hr")
        m.observe({"entity": "alice", "attr": "employer", "value": "Initech"}, source="press", origin_group="g_press")
    return path


def run(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    code = main(list(argv), out=out)
    return code, out.getvalue()


def norm(text: str, db: Path) -> str:
    return re.sub(r"\b[0-9A-Z]{26}\b", "<id>", text.replace(str(db), "<db>"))


def test_inspect_golden(db: Path) -> None:
    code, out = run("inspect", str(db))
    assert code == 0
    assert norm(out, db) == (
        "database        <db>\n"
        "head lsn        4 (4 reports)\n"
        "hash chain      ok\n"
        "schema          v1: employer\n"
        "admission       v1   policy abstain\n"
        "by origin       external_observation=4\n"
        "by cue          assert=3, withdraw=1\n"
        "beliefs now:\n"
        "  alice/employer: established = 'Initech' [single-origin]\n"
        "  bob/employer: established = 'Globex' [single-origin]\n"
    )


def test_inspect_json_is_machine_readable(db: Path) -> None:
    code, out = run("inspect", str(db), "--json")
    d = json.loads(out)
    assert code == 0 and d["head_lsn"] == 4 and d["chain_ok"] is True
    assert [k["entity"] for k in d["keys"]] == ["alice", "bob"]


def test_explain_golden(db: Path) -> None:
    code, out = run("explain", str(db), "alice", "employer")
    assert code == 0
    assert norm(out, db) == (
        "alice/employer: ESTABLISHED = 'Initech'.\n"
        "  SINGLE ORIGIN: rests on one origin group (g_press); uncorroborated. "
        "Corroboration from a second origin group is what raises it.\n"
        "  decision=commit; policy=abstain.\n"
        "alice/employer: justification (depth 8):\n"
        "  'Initech':\n"
        "    - <id> from press (g_press)\n"
    )


def test_explain_as_of_an_earlier_log_position(db: Path) -> None:
    code, out = run("explain", str(db), "alice", "employer", "--as-of", "1")
    assert code == 0 and "ESTABLISHED = 'Acme'" in out and "from hr (g_hr)" in out


def test_diff_golden_and_exit_code(db: Path) -> None:
    code, out = run("diff", str(db), "alice", "employer", "--a", "1", "--b", "4")
    assert code == 0
    assert out == (
        "alice/employer\n"
        "  as of 1: established = 'Acme' (decision commit)\n"
        "  as of 4: established = 'Initech' (decision commit)\n"
        "  changed: assertion\n"
    )
    assert run("diff", str(db), "alice", "employer", "--a", "1", "--b", "4", "--exit-code")[0] == 1
    same = run("diff", str(db), "bob", "employer", "--a", "2", "--b", "4", "--exit-code")
    assert same[0] == 0 and same[1].endswith("changed: nothing\n")


def test_verify_ok_and_detects_tampering(db: Path) -> None:
    code, out = run("verify", str(db))
    assert code == 0 and out == "evidence log   ok\nstored beliefs ok\n"
    con = sqlite3.connect(db)
    try:  # an attacker with file access: drop the append-only trigger and rewrite a stored report
        con.execute("DROP TRIGGER log_erase_only")
        (content,) = con.execute("SELECT content FROM log WHERE lsn = 2").fetchone()
        assert "Globex" in content
        con.execute("UPDATE log SET content = ? WHERE lsn = 2", (content.replace("Globex", "Hooli"),))
        con.commit()
    finally:
        con.close()
    code2, out2 = run("verify", str(db))
    assert code2 == 1 and "BROKEN" in out2


def test_export_import_round_trip(db: Path, tmp_path: Path) -> None:
    dump = tmp_path / "dump.jsonl"
    assert run("export", str(db), "-o", str(dump))[0] == 0
    lines = dump.read_text().splitlines()
    assert json.loads(lines[0])["format"] == "palimem-log"
    new = tmp_path / "new.db"
    code, out = run("import", str(new), str(dump))
    assert code == 0 and out == "imported 4 log rows, 4 admissions; verified ok\n"
    before = norm(run("inspect", str(db))[1], db)
    after = norm(run("inspect", str(new))[1], new)
    assert before == after


def test_import_refuses_a_non_empty_target_and_a_missing_file(db: Path, tmp_path: Path) -> None:
    dump = tmp_path / "dump.jsonl"
    run("export", str(db), "-o", str(dump))
    assert run("import", str(db), str(dump))[0] == 2
    assert run("import", str(tmp_path / "x.db"), str(tmp_path / "nope.jsonl"))[0] == 2


def test_read_commands_never_create_a_database(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    for argv in (["inspect"], ["verify"], ["export"], ["explain", "a", "b"]):
        # the database argument comes first for every command
        cmd = [argv[0], str(missing), *argv[1:]]
        assert run(*cmd)[0] == 2
    assert not missing.exists()


def test_version_and_help_exist(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert "palimem" in capsys.readouterr().out
