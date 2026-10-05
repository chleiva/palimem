"""The lexical resolver: normalisation, scoring rules, blockers, proposals, and the labelled evaluation set."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from palimem.entities import (
    AliasTable,
    LexicalResolver,
    ResolverPolicy,
    canonical_form,
    propose_merges,
    similarity,
)

ROOT = Path(__file__).resolve().parents[2]
ITEMS = ROOT / "bench" / "entities" / "items"


# --------------------------------------------------------------------------- normalisation


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Veltran Inc.", "veltran inc"), ("José García", "jose garcia"), ("Łukasz Nowak", "lukasz nowak"),
        ("Dr. Hannah Weiss", "hannah weiss"), ("Smith, John", "John Smith"), ("The Hooli Group", "Hooli Group"),
        ("St. Mary's Hospital", "St Marys Hospital"), ("Mount Everest", "Mt. Everest"), ("O'Brien", "OBrien"),
    ],
)
def test_canonical_forms_collapse_what_carries_no_identity(a: str, b: str) -> None:
    assert canonical_form(a) == canonical_form(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [("John Smith Jr", "John Smith Sr"), ("Acme 1", "Acme 2"), ("Veltran Systems", "Veltran Capital"), ("Paris", "Paris Hilton")],
)
def test_canonical_forms_keep_what_does(a: str, b: str) -> None:
    assert canonical_form(a) != canonical_form(b)


def test_normalisation_never_empties_a_name() -> None:
    assert canonical_form("Inc") == "inc"  # a name made only of suffix words is kept whole
    assert canonical_form("The Company") != ""


# --------------------------------------------------------------------------- scoring rules


def s(a: str, b: str, kind: str = "auto") -> float:
    return similarity(a, b, kind=kind).score


def test_exact_and_alias_decisions() -> None:
    assert similarity("Veltran Inc", "VELTRAN").reason == "canonical_equal"
    al = AliasTable.of(same=[("Bombay", "Mumbai")], distinct=[("John Smith", "John Smith Jr")])
    assert similarity("Bombay", "Mumbai", aliases=al).reason == "declared_alias"
    assert similarity("Bombay", "Mumbai").score < 0.6  # no lexical rule can resolve it
    assert similarity("John Smith", "John Smith Jr", aliases=al).reason == "declared_distinct"  # distinct wins
    assert similarity("a", "b", aliases=AliasTable.of(same=[("a", "b")], distinct=[("a", "b")])).reason == "declared_distinct"


@pytest.mark.parametrize(
    ("a", "b"),
    [("Robert Brown", "Bob Brown"), ("J. Smith", "John Smith"), ("Veltrann Inc", "Veltran Inc"), ("Globxe", "Globex"),
     ("Christian Leiva", "Chris Leiva"), ("International Business Machines", "IBM")],
)
def test_fuzzy_matches_are_proposed(a: str, b: str) -> None:
    assert s(a, b, "person" if " " in a and "Inc" not in a else "org") >= 0.6


@pytest.mark.parametrize(
    ("a", "b", "why"),
    [
        ("John Smith", "Jane Smith", "blocked:given_name_conflict"), ("Acme 1", "Acme 2", "blocked:numeric_mismatch"),
        ("Henry Ford II", "Henry Ford III", "blocked:generational_suffix"),
        ("Veltran Systems", "Veltran Capital", "blocked:distinct_qualifier"), ("Paris", "Paris Hilton", "blocked:extra_qualifier"),
        ("Stark Industries", "Spark Industries", None), ("Globex", "Globe", None),
    ],
)
def test_blockers_and_near_names(a: str, b: str, why: str | None) -> None:
    sim = similarity(a, b)
    assert sim.score < 0.6
    if why is not None:
        assert sim.reason == why


def test_similarity_is_symmetric_and_deterministic() -> None:
    pairs = [("Robert Brown", "Bob Brown"), ("Veltran Inc", "Veltran Capital"), ("J. Smith", "Jane Smith"), ("Acme", "Acme Bank")]
    for a, b in pairs:
        assert s(a, b) == s(b, a) == s(a, b)


# --------------------------------------------------------------------------- proposals and policy


def test_proposals_are_never_applied_and_default_to_no_auto_apply() -> None:
    pol = ResolverPolicy()
    assert pol.auto_at is None
    props = propose_merges(["Veltran Inc", "veltran", "Veltran Systems", "Acme"], policy=pol)
    assert props and all(p.decision == "propose" for p in props)
    top = props[0]
    assert {top.alias, top.into} == {"Veltran Inc", "veltran"} and top.reason == "canonical_equal"
    assert top.id == propose_merges(["veltran", "Veltran Inc"], policy=pol)[0].id  # stable ids
    assert all({p.alias, p.into} != {"Veltran Systems", "Acme"} for p in props)


def test_auto_eligible_needs_an_explicit_threshold_and_an_exact_reason() -> None:
    pol = ResolverPolicy(auto_at=0.95)
    props = propose_merges(["Veltran Inc", "veltran", "Veltrann"], policy=pol)
    kinds = {frozenset({p.alias, p.into}): p.decision for p in props}
    assert kinds[frozenset({"Veltran Inc", "veltran"})] == "auto_eligible"  # canonical_equal
    assert all(v == "propose" for k, v in kinds.items() if k != frozenset({"Veltran Inc", "veltran"}))  # fuzzy: never auto
    with pytest.raises(ValueError):
        ResolverPolicy(propose_at=0.8, auto_at=0.5)


def test_already_merged_pairs_are_not_proposed_again() -> None:
    props = propose_merges(["Veltran Inc", "veltran"], same_class=[frozenset({"Veltran Inc", "veltran"})])
    assert props == []


def test_proposals_scale_by_blocking_not_all_pairs() -> None:
    names = [f"Company {i} Holdings" for i in range(400)] + ["Alpha Corp", "alpha"]
    props = propose_merges(names)
    assert any({p.alias, p.into} == {"Alpha Corp", "alpha"} for p in props)
    assert all("Company" not in p.alias or "Company" in p.into for p in props)


def test_a_custom_backend_plugs_in_behind_the_same_record() -> None:
    class Always:
        method = "fake-embedding"

        def score(self, a: str, b: str):  # type: ignore[no-untyped-def]
            from palimem.entities import Similarity

            return Similarity(0.99, "embedding")

    props = propose_merges(["x1", "x2"], backend=Always())
    assert props and props[0].method == "fake-embedding" and props[0].decision == "propose"
    assert LexicalResolver().method == "lexical"


# --------------------------------------------------------------------------- the labelled set


def load(split: str) -> list[dict[str, str]]:
    return [json.loads(x) for x in (ITEMS / f"{split}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]


@pytest.mark.parametrize("split", ["dev", "test"])
def test_the_labelled_set_matches_its_frozen_checksums(split: str) -> None:
    want = json.loads((ITEMS / "checksums.json").read_text())[split]
    assert hashlib.sha256((ITEMS / f"{split}.jsonl").read_bytes()).hexdigest() == want


def test_the_labelled_set_is_large_stratified_and_disjoint() -> None:
    dev, test = load("dev"), load("test")
    assert len(dev) + len(test) >= 80 and len(dev) >= 30 and len(test) >= 30
    assert not ({r["id"] for r in dev} & {r["id"] for r in test})
    assert {r["category"] for r in dev} == {r["category"] for r in test}  # every category in both halves
    assert {r["label"] for r in dev + test} == {"same", "different"}
    assert sum(r["label"] == "different" for r in dev + test) >= 30  # homonyms that must NOT merge


def test_no_rule_is_blind_to_the_dev_set_at_the_proposal_threshold() -> None:
    """A regression floor on dev, where the rules were tuned (the test split is scored by bench/entities/eval_resolver.py)."""
    rows = [r for r in load("dev") if r["category"] != "alias_only"]
    pos = [r for r in rows if r["label"] == "same"]
    neg = [r for r in rows if r["label"] == "different"]
    hit = sum(similarity(r["a"], r["b"], kind=r["kind"]).score >= 0.6 for r in pos)
    fm = sum(similarity(r["a"], r["b"], kind=r["kind"]).score >= 0.6 for r in neg)
    assert hit / len(pos) >= 0.9
    assert fm / len(neg) <= 0.1
