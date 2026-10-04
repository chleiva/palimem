"""JSONL export and import (T-C9): chain verification on import, and export -> import reproduces identical beliefs
given the same Reviser."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from chain_fakes import ChainReviser, chain_schema
from conftest import SECRET, Clock
from fakes import FakeAdmitter, make_report

from palimem.store import (
    Backend,
    ErasureReason,
    InMemoryBackend,
    InputKind,
    SQLiteBackend,
    StoreError,
)
from palimem.types import Cue, Key

E = "alice"
A = Key(entity=E, attr="employer")
KEYS = [Key(entity=E, attr=a) for a in ("employer", "work_city", "tax_city", "nickname")]


def add(b, report, idem, reviser=None):  # type: ignore[no-untyped-def]
    return b.append(report, idempotency_key=idem, admitter=FakeAdmitter(), reviser=reviser or ChainReviser())


def populate(b: Backend, clock: Clock) -> None:
    b.put_schema(chain_schema())
    b.put_input(InputKind.SEMANTIC, 1, {"semantics": "v0.3", "self_update": False})
    first = add(b, make_report(E, "employer", "acme", source="s1"), "k1")
    clock.tick(5)
    add(b, make_report(E, "employer", "globex", source="s2"), "k2")
    clock.tick(5)
    add(b, make_report(E, "nickname", "al"), "k3")
    b.put_input(InputKind.SEMANTIC, 2, {"semantics": "v0.3", "self_update": True})
    clock.tick(5)
    add(b, make_report(E, "employer", cue=Cue.WITHDRAW, target=first.entry.report.id, source="s1"), "k4")


def fresh(kind: str, tmp_path: Path, name: str = "target", *, chain: bool = True) -> Backend:
    if kind == "memory":
        return InMemoryBackend(store_secret=SECRET, clock=Clock(), chain=chain)
    return SQLiteBackend(tmp_path / f"{name}.db", store_secret=SECRET, clock=Clock(), chain=chain)


def core(b: Backend, key: Key, version: int) -> str:
    bel = b.belief_version(key, version)
    assert bel is not None
    d = bel.to_dict()
    return json.dumps({k: d[k] for k in ("segments", "pinned", "invalidated_by")}, sort_keys=True)


@pytest.mark.parametrize("target_kind", ["memory", "sqlite"])
def test_export_then_import_reproduces_identical_beliefs(h, tmp_path, target_kind: str) -> None:  # type: ignore[no-untyped-def]
    src = h.backend
    populate(src, h.clock)
    lines = list(src.export_jsonl())

    types = [json.loads(x)["type"] for x in lines]
    assert types[0] == "header" and types[-1] == "footer" and types.count("log") == 4 and types.count("input") == 3
    assert json.loads(lines[0])["version"] == 1 and json.loads(lines[0])["store_format"] == 2
    assert all(x == json.dumps(json.loads(x), sort_keys=True, separators=(",", ":"), ensure_ascii=False) for x in lines)  # canonical

    dst = fresh(target_kind, tmp_path)
    rep = dst.import_jsonl(lines, reviser=ChainReviser())
    assert (rep.log_rows, rep.admissions, rep.inputs) == (4, 4, 3) and rep.beliefs > 0

    assert dst.head() == src.head()  # same heads: same generation, same chain hashes
    assert dst.verify_log().ok and dst.recover().ok
    assert [e.entry_hash for e in dst.scan()] == [e.entry_hash for e in src.scan()]  # type: ignore[union-attr]
    assert dst.schema() == src.schema() and dst.historical_inputs() == src.historical_inputs()
    assert dst.attr_dependents("employer") == src.attr_dependents("employer")
    for k in KEYS:  # every version of every belief is identical, byte for byte
        cur = src.storage.current_version(k)  # type: ignore[attr-defined]
        assert dst.storage.current_version(k) == cur  # type: ignore[attr-defined]
        for v in range(1, (cur or 0) + 1):
            assert src.belief_version(k, v).to_json() == dst.belief_version(k, v).to_json()  # type: ignore[union-attr]
    assert dst.verify_beliefs(ChainReviser()).ok
    # and the imported store is a working store
    after = add(dst, make_report(E, "employer", "initech", source="s3"), "k5")
    assert after.entry.lsn == 5 and dst.verify_log().ok  # type: ignore[union-attr]


def test_a_chainless_export_round_trips(tmp_path) -> None:  # type: ignore[no-untyped-def]
    clock = Clock()
    src = InMemoryBackend(chain=False, clock=clock)
    populate(src, clock)
    dst = fresh("sqlite", tmp_path, chain=False)
    dst.import_jsonl(src.export_jsonl(), reviser=ChainReviser())
    assert "verify_log" not in dst.capabilities
    for k in KEYS:
        assert dst.current_belief(k) == src.current_belief(k)


def test_import_verifies_the_chain_and_rejects_every_kind_of_tampering(h, tmp_path) -> None:  # type: ignore[no-untyped-def]
    populate(h.backend, h.clock)
    lines = list(h.backend.export_jsonl())
    log_idx = [i for i, x in enumerate(lines) if json.loads(x)["type"] == "log"]
    adm_idx = [i for i, x in enumerate(lines) if json.loads(x)["type"] == "admission"]

    def bad(edit: Callable[[list[str]], list[str]], match: str) -> None:
        dst = fresh("memory", tmp_path)
        with pytest.raises(StoreError, match=match):
            dst.import_jsonl(edit(list(lines)), reviser=ChainReviser())
        assert dst.head().lsn == 0 and dst.current_belief(A) is None and dst.storage.log_head() is None  # nothing was imported

    def edit_content(ls: list[str]) -> list[str]:
        d = json.loads(ls[log_idx[1]])
        d["content"] = d["content"].replace("globex", "evil!")
        ls[log_idx[1]] = json.dumps(d)
        return ls

    def edit_meta(ls: list[str]) -> list[str]:
        d = json.loads(ls[log_idx[2]])
        d["recorded_us"] += 1
        ls[log_idx[2]] = json.dumps(d)
        return ls

    def reorder(ls: list[str]) -> list[str]:  # swap the positions the rows claim (line order itself is irrelevant)
        d1, d2 = json.loads(ls[log_idx[1]]), json.loads(ls[log_idx[2]])
        d1["lsn"], d2["lsn"] = d2["lsn"], d1["lsn"]
        ls[log_idx[1]], ls[log_idx[2]] = json.dumps(d1), json.dumps(d2)
        return ls

    def edit_adm(ls: list[str]) -> list[str]:
        d = json.loads(ls[adm_idx[1]])
        d["record"] = d["record"].replace("admissible", "excluded")
        ls[adm_idx[1]] = json.dumps(d)
        return ls

    bad(edit_content, "salted commitment")
    bad(edit_meta, "entry_hash")
    bad(reorder, "link|contiguous|entry_hash")
    bad(edit_adm, "admission")
    shuffled = [lines[0], *reversed(lines[1:-1]), lines[-1]]  # line order carries no meaning: rows are placed by their lsn
    ok = fresh("memory", tmp_path, "shuffled")
    ok.import_jsonl(shuffled, reviser=ChainReviser())
    assert ok.verify_log().ok
    bad(lambda ls: ls[:-1], "footer")  # truncated: no footer
    bad(lambda ls: ls[1:], "header")
    bad(lambda ls: [x for i, x in enumerate(ls) if i != log_idx[-1]], "footer counts")  # a row dropped
    bad(lambda ls: [*ls[:-1], json.dumps({**json.loads(ls[-1]), "log_rows": 99})], "footer counts")

    def bump_head(ls: list[str]) -> list[str]:
        f = json.loads(ls[-1])
        f["head"]["entry_hash"] = "0" * 64
        return [*ls[:-1], json.dumps(f)]

    bad(bump_head, "head")
    bad(lambda ls: ["not json", *ls[1:]], "not valid JSON")


def test_import_needs_an_empty_store_and_a_matching_chain(h, tmp_path) -> None:  # type: ignore[no-untyped-def]
    populate(h.backend, h.clock)
    lines = list(h.backend.export_jsonl())
    with pytest.raises(StoreError, match="empty"):
        h.backend.import_jsonl(lines, reviser=ChainReviser())  # importing into itself
    with pytest.raises(StoreError, match="chain"):
        fresh("memory", tmp_path, chain=False).import_jsonl(lines, reviser=ChainReviser())


@pytest.mark.parametrize("target_kind", ["memory", "sqlite"])
def test_an_export_with_an_erased_report_imports_with_a_valid_chain(h, tmp_path, target_kind: str) -> None:  # type: ignore[no-untyped-def]
    src = h.backend
    populate(src, h.clock)
    victim = next(iter(src.scan(1, 1)))
    assert victim.report.id is not None  # type: ignore[union-attr]
    src.erase(victim.report.id, ErasureReason.ERASURE_REQUEST, reviser=ChainReviser())  # type: ignore[union-attr]
    lines = list(src.export_jsonl())
    erased = [json.loads(x) for x in lines if json.loads(x)["type"] == "log" and json.loads(x)["tomb"] is not None]
    assert len(erased) == 1 and erased[0]["content"] is None and erased[0]["salt"] is None  # no content, no salt in the file
    assert "acme" not in "\n".join(lines).replace('"employer"', "")  # the erased value is not in the export

    dst = fresh(target_kind, tmp_path)
    dst.import_jsonl(lines, reviser=ChainReviser())
    assert dst.verify_log().ok and dst.recover().ok
    row = {r.lsn: r for r in dst.verify_log().rows}[1]
    assert (row.linked, row.content_verified, row.tombstoned) == (True, False, True)
    assert dst.get_entry(victim.report.id) == src.get_entry(victim.report.id)  # type: ignore[union-attr]  # the same tombstone
    for k in KEYS:  # current beliefs agree (version numbers may differ: the importer has no repair versions)
        s, d = src.current_belief(k), dst.current_belief(k)
        assert s is not None and d is not None
        assert [x.to_dict() for x in s.segments] == [x.to_dict() for x in d.segments] and s.pinned == d.pinned
    assert dst.verify_beliefs(ChainReviser()).ok
