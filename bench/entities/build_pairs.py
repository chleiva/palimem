"""Builds the labelled entity-resolution pairs (`items/dev.jsonl`, `items/test.jsonl`, `items/checksums.json`).

Each pair is two surface names and a label: `same` (one real-world entity) or `different`. Pairs that are
ambiguous to a reasonable annotator were left out (for example `Sara Lee` / `Sarah Lee`); the set measures the rules,
not the annotator's taste. `alias_only` positives cannot be resolved by any lexical rule (`Bombay` / `Mumbai`); they
are reported separately and resolved only with a declared alias.

Honest limits: one author wrote the labels (with LLM assistance), names are mostly Western and English, and a name
is not an identity: two different people called `John Smith` look identical to every resolver in this repository.

The dev/test split is deterministic and stratified (within each category, pairs sorted by id alternate dev, test).
Rules were tuned on dev; the test split is frozen by checksum (`checksums.json`) and reported once.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

# (a, b, category, kind)
SAME: list[tuple[str, str, str, str]] = [
    # --- exact variants: case, punctuation, spacing, honorifics
    ("Veltran Inc.", "veltran inc", "case_punct", "org"),
    ("Alice Johnson", "ALICE  JOHNSON", "case_punct", "person"),
    ("St. Mary's Hospital", "St Marys Hospital", "case_punct", "org"),
    ("O'Brien Consulting", "OBrien Consulting", "case_punct", "org"),
    ("New-York", "New York", "case_punct", "other"),
    ("Acme, Corp.", "Acme Corp", "case_punct", "org"),
    ("Dr. Hannah Weiss", "Hannah Weiss", "honorific", "person"),
    ("Mrs. Elena Rossi", "Elena Rossi", "honorific", "person"),
    ("Prof. Idris Okafor", "Idris Okafor", "honorific", "person"),
    ("Ms. Priya Nair", "Priya Nair", "honorific", "person"),
    # --- corporate suffixes and articles
    ("Veltran Inc", "Veltran", "corporate", "org"),
    ("Globex Corporation", "Globex", "corporate", "org"),
    ("Initech LLC", "Initech", "corporate", "org"),
    ("Umbrella Ltd", "Umbrella", "corporate", "org"),
    ("Wayne Enterprises Co.", "Wayne Enterprises", "corporate", "org"),
    ("Stark Industries GmbH", "Stark Industries", "corporate", "org"),
    ("The Hooli Group", "Hooli Group", "corporate", "org"),
    ("Tyrell Corp", "Tyrell Corporation", "corporate", "org"),
    ("Soylent Company", "Soylent Co", "corporate", "org"),
    ("Cyberdyne Systems Limited", "Cyberdyne Systems", "corporate", "org"),
    # --- spelled-out abbreviations
    ("Dept. of Health", "Department of Health", "abbrev", "org"),
    ("Mt. Everest", "Mount Everest", "abbrev", "other"),
    ("St. Louis", "Saint Louis", "abbrev", "other"),
    ("Ft. Worth", "Fort Worth", "abbrev", "other"),
    ("Intl. Rescue Committee", "International Rescue Committee", "abbrev", "org"),
    ("Prof. Marta Silva", "Professor Marta Silva", "abbrev", "person"),
    # --- acronyms
    ("International Business Machines", "IBM", "acronym", "org"),
    ("Massachusetts Institute of Technology", "MIT", "acronym", "org"),
    ("World Health Organization", "WHO", "acronym", "org"),
    ("European Space Agency", "ESA", "acronym", "org"),
    # --- nicknames covered by the built-in table
    ("Robert Brown", "Bob Brown", "nickname", "person"),
    ("William Clarke", "Bill Clarke", "nickname", "person"),
    ("Elizabeth Turner", "Liz Turner", "nickname", "person"),
    ("Christian Leiva", "Chris Leiva", "nickname", "person"),
    ("Katherine Moore", "Kate Moore", "nickname", "person"),
    ("Alexander Petrov", "Alex Petrov", "nickname", "person"),
    ("Jonathan Reed", "Jon Reed", "nickname", "person"),
    ("Michael Chen", "Mike Chen", "nickname", "person"),
    # --- initials and middle names
    ("J. Smith", "John Smith", "initial", "person"),
    ("Maria G. Lopez", "Maria Lopez", "initial", "person"),
    ("A. Kowalski", "Anna Kowalski", "initial", "person"),
    ("John R. Doe", "John Doe", "initial", "person"),
    ("M. Dubois", "Marie Dubois", "initial", "person"),
    ("Christian Leiva", "Christian Leiva Beltran", "extra_name", "person"),
    ("Maria Lopez", "Maria Lopez Garcia", "extra_name", "person"),
    # --- typos
    ("Veltrann Inc", "Veltran Inc", "typo", "org"),
    ("Alice Jonhson", "Alice Johnson", "typo", "person"),
    ("Globxe", "Globex", "typo", "org"),
    ("Hannh Weiss", "Hannah Weiss", "typo", "person"),
    ("Stark Industires", "Stark Industries", "typo", "org"),
    ("Cambrdige", "Cambridge", "typo", "other"),
    ("Priya Nairr", "Priya Nair", "typo", "person"),
    ("Okafor Idirs", "Okafor Idris", "typo", "person"),
    # --- word order
    ("Leiva Beltran, Christian", "Christian Leiva Beltran", "word_order", "person"),
    ("Smith, John", "John Smith", "word_order", "person"),
    ("Weiss, Hannah", "Hannah Weiss", "word_order", "person"),
    ("Nair, Priya", "Priya Nair", "word_order", "person"),
    # --- accents and transliteration
    ("José García", "Jose Garcia", "accent", "person"),
    ("Zoë Müller", "Zoe Muller", "accent", "person"),
    ("São Paulo", "Sao Paulo", "accent", "other"),
    ("Łukasz Nowak", "Lukasz Nowak", "accent", "person"),
    ("Renée Dupont", "Renee Dupont", "accent", "person"),
    ("Björk Guðmundsdóttir", "Bjork Gudmundsdottir", "accent", "person"),
    # --- only a declared alias can resolve these
    ("Margaret Hill", "Peggy Hill", "alias_only", "person"),
    ("Bombay", "Mumbai", "alias_only", "other"),
    ("Facebook", "Meta Platforms", "alias_only", "org"),
    ("Big Apple", "New York", "alias_only", "other"),
    ("Twitter", "X Corp", "alias_only", "org"),
]

DIFFERENT: list[tuple[str, str, str, str]] = [
    # --- people sharing a surname or a given name
    ("John Smith", "Jane Smith", "homonym_person", "person"),
    ("John Smith", "James Smith", "homonym_person", "person"),
    ("Alice Johnson", "Alison Johnson", "homonym_person", "person"),
    ("Maria Lopez", "Mario Lopez", "homonym_person", "person"),
    ("Robert Brown", "Roberta Brown", "homonym_person", "person"),
    ("Michael Chen", "Michelle Chen", "homonym_person", "person"),
    ("Elena Rossi", "Marco Rossi", "same_surname", "person"),
    ("Christian Leiva", "Carolina Leiva", "same_surname", "person"),
    ("Hannah Weiss", "Daniel Weiss", "same_surname", "person"),
    ("Priya Nair", "Anil Nair", "same_surname", "person"),
    ("Idris Okafor", "Chidi Okafor", "same_surname", "person"),
    ("Marta Silva", "Pedro Silva", "same_surname", "person"),
    ("Anna Kowalski", "Jan Kowalski", "same_surname", "person"),
    ("Marie Dubois", "Pierre Dubois", "same_surname", "person"),
    ("Alice Johnson", "Alice Peterson", "same_given", "person"),
    ("Hannah Weiss", "Hannah Schmidt", "same_given", "person"),
    # --- generational suffixes
    ("John Smith Jr", "John Smith Sr", "generation", "person"),
    ("Henry Ford II", "Henry Ford III", "generation", "person"),
    ("Martin Luther King Jr", "Martin Luther King Sr", "generation", "person"),
    # --- organisations with a shared first word
    ("Veltran Systems", "Veltran Capital", "org_qualifier", "org"),
    ("Acme Bank", "Acme Bakery", "org_qualifier", "org"),
    ("Globex Energy", "Globex Health", "org_qualifier", "org"),
    ("First National Bank", "Second National Bank", "org_qualifier", "org"),
    ("Apple Inc", "Apple Records", "org_qualifier", "org"),
    ("Delta Airlines", "Delta Faucet", "org_qualifier", "org"),
    ("Mercury Insurance", "Mercury Records", "org_qualifier", "org"),
    ("United Airlines", "United Nations", "org_qualifier", "org"),
    ("Stark Industries", "Stark Logistics", "org_qualifier", "org"),
    ("Wayne Enterprises", "Wayne Foods", "org_qualifier", "org"),
    # --- numbers
    ("Acme 1", "Acme 2", "numeric", "org"),
    ("Building 7", "Building 17", "numeric", "other"),
    ("Route 66", "Route 99", "numeric", "other"),
    ("Apollo 11", "Apollo 13", "numeric", "other"),
    ("Studio 54", "Studio 45", "numeric", "other"),
    ("Sector 9", "Sector 7", "numeric", "other"),
    # --- one name inside another
    ("Paris", "Paris Hilton", "shared_token", "other"),
    ("Georgia", "Georgia Tech", "shared_token", "other"),
    ("Washington", "Washington Post", "shared_token", "other"),
    ("Jordan", "Michael Jordan", "shared_token", "other"),
    ("Amazon", "Amazonas", "shared_token", "other"),
    ("Cambridge", "Cambridge Analytica", "shared_token", "other"),
    ("Oxford", "Oxford Instruments", "shared_token", "other"),
    # --- places with the same name
    ("Springfield, Illinois", "Springfield, Missouri", "place", "other"),
    ("Cambridge, UK", "Cambridge, MA", "place", "other"),
    ("Portland, Oregon", "Portland, Maine", "place", "other"),
    ("Birmingham, Alabama", "Birmingham, England", "place", "other"),
    ("Newcastle", "New Castle", "place", "other"),
    ("Lyon, France", "Lyon, Texas", "place", "other"),
    # --- close spellings of different names
    ("Stark Industries", "Spark Industries", "near_name", "org"),
    ("Globex", "Globe", "near_name", "org"),
    ("Initech", "Innotech", "near_name", "org"),
    ("Wayne Enterprises", "Payne Enterprises", "near_name", "org"),
    ("Hooli", "Hooley", "near_name", "org"),
    ("Alison Reed", "Allison Reed", "near_name", "person"),
    ("Hanna Weiss", "Hans Weiss", "near_name", "person"),
    ("Tyrell Corp", "Terrell Corp", "near_name", "org"),
    ("Veltran", "Veltrex", "near_name", "org"),
    ("Umbrella", "Umbrello", "near_name", "org"),
    # --- unrelated names
    ("Alice Johnson", "Bob Brown", "unrelated", "person"),
    ("Globex", "Initech", "unrelated", "org"),
    ("Springfield", "Cambridge", "unrelated", "other"),
    ("Acme", "Veltran", "unrelated", "org"),
    ("Christian Leiva", "Hannah Weiss", "unrelated", "person"),
    ("Stark Industries", "Umbrella", "unrelated", "org"),
    # --- initials that conflict
    ("J. Smith", "Jane Smith", "initial_conflict", "person"),
    ("A. Kowalski", "Jan Kowalski", "initial_conflict", "person"),
    ("M. Dubois", "Pierre Dubois", "initial_conflict", "person"),
    ("R. Brown", "Alice Brown", "initial_conflict", "person"),
]


def pair_id(a: str, b: str) -> str:
    h = hashlib.sha256(f"{a}␟{b}".encode()).hexdigest()
    return "p" + h[:10]


def build() -> dict[str, list[dict[str, str]]]:
    rows = []
    for label, group in (("same", SAME), ("different", DIFFERENT)):
        for a, b, cat, kind in group:
            rows.append({"id": pair_id(a, b), "a": a, "b": b, "label": label, "category": cat, "kind": kind})
    ids = [r["id"] for r in rows]
    if len(set(ids)) != len(ids):
        raise SystemExit("duplicate pair id")
    # stratified and deterministic: within each category, pairs sorted by id alternate dev, test, dev, ...
    split: dict[str, list[dict[str, str]]] = {"dev": [], "test": []}
    by_cat: dict[str, list[dict[str, str]]] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)
    for cat in sorted(by_cat):
        for i, r in enumerate(sorted(by_cat[cat], key=lambda r: r["id"])):
            split["dev" if i % 2 == 0 else "test"].append(r)
    for name in split:
        split[name].sort(key=lambda r: r["id"])
    return split


def main() -> int:
    split = build()
    (HERE / "items").mkdir(exist_ok=True)
    sums: dict[str, str] = {}
    for name, rows in split.items():
        text = "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows)
        (HERE / "items" / f"{name}.jsonl").write_text(text, encoding="utf-8")
        sums[name] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    (HERE / "items" / "checksums.json").write_text(json.dumps(sums, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    n_same = sum(len(SAME) for _ in [0])
    print(f"{n_same} same + {len(DIFFERENT)} different = {n_same + len(DIFFERENT)} pairs; "
          f"dev {len(split['dev'])}, test {len(split['test'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
