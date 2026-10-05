"""``verify(scope=log|beliefs)`` (author ruling 2026-10-05): both scopes are kept; the beliefs scope runs offline."""

from __future__ import annotations

import io
import json
import socket
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from palimem import Memory
from palimem.cli import main
from palimem.types import Attr, AttrClass, Belief, Key, Schema, ValueType


def schema() -> Schema:
    return Schema(version=1, attrs=(
        Attr(name="employer", attr_class=AttrClass.SINGLE_CHANGEABLE, value_type=ValueType.STRING, inertia=True),
    ))


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "agent.db"
    with Memory(path, schema=schema()) as m:
        m.observe({"entity": "alice", "attr": "employer", "value": "Acme"}, source="hr", origin_group="g_hr")
        m.observe({"entity": "bob", "attr": "employer", "value": "Globex"}, source="hr", origin_group="g_hr")
    return path


def forge_belief(db: Path, entity: str) -> None:
    """Rewrite one stored belief row only (an attacker with file access): the evidence log stays intact."""
    with Memory(db) as m:
        cur = m.core.backend.current_belief(Key(entity=entity, attr="employer"))
        assert cur is not None
        d = cur.to_dict()
        d["segments"] = [{"valid_from": None, "valid_to": None, "kernel_status": "unknown", "established": None,
                          "alternatives": [], "support": {}}]
        forged = Belief.from_dict(d).to_json()
    con = sqlite3.connect(db)
    try:
        for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='beliefs'").fetchall():
            con.execute(f"DROP TRIGGER {name}")
        con.execute("UPDATE beliefs SET belief = ? WHERE entity = ? AND attr = 'employer' AND version = "
                    "(SELECT MAX(version) FROM beliefs WHERE entity = ? AND attr = 'employer')", (forged, entity, entity))
        con.commit()
    finally:
        con.close()


def run(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    return main(list(argv), out=out), out.getvalue()


def test_both_scopes_pass_on_a_clean_store(db: Path) -> None:
    with Memory(db) as m:
        log, bel, both = m.verify("log"), m.verify("beliefs"), m.verify("all")
        assert log.ok and bel.ok and both.ok
        assert bel.checked >= 2 and both.checked == log.checked + bel.checked
        assert m.verify().ok  # the default stays the cheap log check


def test_an_unknown_scope_is_refused(db: Path) -> None:
    with Memory(db) as m, pytest.raises(ValueError, match="scope"):
        m.verify("everything")  # type: ignore[arg-type]


def test_a_forged_belief_row_is_seen_by_the_beliefs_scope_only(db: Path) -> None:
    forge_belief(db, "alice")
    with Memory(db) as m:
        assert m.verify("log").ok  # the evidence log is intact
        res = m.verify("beliefs")
        assert not res.ok and [p.kind for p in res.problems] == ["belief_mismatch"]
        assert res.problems[0].key == Key(entity="alice", attr="employer")
        assert not m.verify("all").ok
        # keys= verifies on demand: the untouched key is fine, the forged one is not
        assert m.verify("beliefs", keys=[Key(entity="bob", attr="employer")]).ok
        assert not m.verify("beliefs", keys=[Key(entity="alice", attr="employer")]).ok


def test_the_beliefs_scope_is_offline(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No network connection and no model call: sockets are made unusable, and the hosted-model SDK is never imported."""
    import sys

    sys.modules.pop("boto3", None)

    def no_network(*a: Any, **k: Any) -> Any:
        raise AssertionError("the beliefs scope must not touch the network")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    with Memory(db) as m:
        assert m.verify("beliefs").ok and m.verify("all").ok
    assert "boto3" not in sys.modules and "openai" not in sys.modules


def test_incremental_checks_only_what_changed_and_excludes_keys(db: Path) -> None:
    with Memory(db) as m:
        first = m.verify("beliefs", incremental=True)
        assert first.ok and first.checked >= 2  # the first run is a full verification
        second = m.verify("beliefs", incremental=True)
        assert second.ok and second.checked == 0
        m.observe({"entity": "alice", "attr": "employer", "value": "Initech"}, source="press", origin_group="g_press")
        third = m.verify("beliefs", incremental=True)
        assert third.ok and third.checked >= 1
        with pytest.raises(ValueError, match="alternatives"):
            m.core.verify("beliefs", keys=[Key(entity="bob", attr="employer")], incremental=True)


def test_the_facade_takes_attr_entity_pairs(db: Path) -> None:
    with Memory(db) as m:
        assert m.verify("beliefs", keys=[("employer", "bob")]).ok


def test_cli_scopes(db: Path) -> None:
    assert run("verify", str(db)) == (0, "evidence log   ok\nstored beliefs ok\n")  # default: both
    assert run("verify", str(db), "--scope", "log") == (0, "evidence log   ok\n")
    assert run("verify", str(db), "--scope", "beliefs") == (0, "stored beliefs ok\n")
    code, out = run("verify", str(db), "--scope", "all", "--json")
    d = json.loads(out)
    assert code == 0 and d == {"scope": "all", "log_ok": True, "beliefs_ok": True, "problems": []}
    forge_belief(db, "bob")
    assert run("verify", str(db), "--scope", "log") == (0, "evidence log   ok\n")  # the chain does not see it
    code, out = run("verify", str(db), "--scope", "beliefs")
    assert code == 1 and "BROKEN" in out and "beliefs:" in out
    code, out = run("verify", str(db), "--json")
    d = json.loads(out)
    assert code == 1 and d["log_ok"] is True and d["beliefs_ok"] is False
    code, out = run("verify", str(db), "--scope", "beliefs", "--incremental")
    assert code == 1  # a forged row is still found on the first (full) incremental run
