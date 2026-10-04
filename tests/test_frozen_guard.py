import json
import shutil

import pytest

from harness import frozen


def make_set(tmp_path):
    root = tmp_path / "setting1"
    root.mkdir()
    (root / "s1_0000.json").write_text('{"stream": 0}')
    (root / "s1_0000.gold.json").write_text('{"q1": {"status": "established"}}')
    manifest = {"schema": 1, "files": {p.name: frozen.sha256_file(p) for p in sorted(root.iterdir())}}
    return root, manifest


def test_clean_set_verifies(tmp_path):
    root, manifest = make_set(tmp_path)
    assert frozen.verify_files(root, manifest) == []
    frozen.require_valid(root, manifest)


def test_altered_gold_fails(tmp_path):
    root, manifest = make_set(tmp_path)
    (root / "s1_0000.gold.json").write_text('{"q1": {"status": "unresolved"}}')
    problems = frozen.verify_files(root, manifest)
    assert [(p.path, p.kind) for p in problems] == [("s1_0000.gold.json", "mismatch")]
    with pytest.raises(frozen.FrozenError, match="s1_0000.gold.json"):
        frozen.require_valid(root, manifest)


def test_single_byte_flip_is_detected(tmp_path):
    root, manifest = make_set(tmp_path)
    p = root / "s1_0000.json"
    p.write_bytes(p.read_bytes()[:-1] + b"X")
    assert frozen.verify_files(root, manifest)[0].kind == "mismatch"


def test_missing_and_unexpected_files(tmp_path):
    root, manifest = make_set(tmp_path)
    (root / "s1_0000.json").unlink()
    (root / "s1_9999.json").write_text("{}")
    kinds = {(p.path, p.kind) for p in frozen.verify_files(root, manifest)}
    assert kinds == {("s1_0000.json", "missing"), ("s1_9999.json", "unexpected")}


def test_only_restricts_check_and_flags_unknown_names(tmp_path):
    root, manifest = make_set(tmp_path)
    (root / "s1_0000.gold.json").write_text("tampered")
    assert frozen.verify_files(root, manifest, only=["s1_0000.json"]) == []
    assert frozen.verify_files(root, manifest, only=["nope.json"])[0].kind == "unexpected"


def test_pinned_manifest_is_wellformed():
    m = frozen.load_manifest()
    assert m["source"]["zip_sha256"] == frozen.ZENODO_SHA256
    assert m["source"]["doi"] == "10.5281/zenodo.23127764"
    assert len(m["files"]) == 1501  # 500 x (stream, gold, hidden) + _stats.json
    assert m["source"]["cross_checked_against_deposit_MANIFEST_sha256"] == 1501
    assert all(len(h) == 64 for h in m["files"].values())
    assert "s1_0000.gold.json" in m["files"] and "_stats.json" in m["files"]


def test_build_manifest_rejects_a_zip_that_is_not_the_published_one(tmp_path):
    z = tmp_path / "fake.zip"
    z.write_bytes(b"not the deposit")
    with pytest.raises(frozen.FrozenError, match="published Zenodo hash"):
        frozen.build_manifest(z)
    with pytest.raises(frozen.FrozenError, match="refusing to unpack"):
        frozen.extract_setting1(z, tmp_path / "out")


def test_altered_copy_of_real_frozen_gold_fails(tmp_path):
    try:
        root = frozen.locate_frozen()
    except frozen.FrozenError:
        pytest.skip("frozen Setting 1 data not available locally")
    name = "s1_0000.gold.json"
    copy = tmp_path / name
    shutil.copy(root / name, copy)
    manifest = frozen.load_manifest()
    assert frozen.verify_files(tmp_path, manifest, only=[name]) == []
    gold = json.loads(copy.read_text())
    qid = next(iter(gold))
    gold[qid]["status"] = "unknown" if gold[qid]["status"] != "unknown" else "established"
    copy.write_text(json.dumps(gold))
    assert frozen.verify_files(tmp_path, manifest, only=[name])[0].kind == "mismatch"
