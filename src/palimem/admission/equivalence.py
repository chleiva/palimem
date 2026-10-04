"""Equivalent propositions (S-01, decided).

Confirmation is derived from *equivalent* reports of another origin group. Equivalence is defined
here, normatively:

* **Value normalisation.** Strings are Unicode NFKC-normalised, stripped, whitespace-collapsed and
  case-folded; an integral float equals the int (``2.0 == 2``); ``bool`` stays distinct from
  ``int`` (``true`` is not ``1``). Dates are ISO strings and normalise as strings.
* **Same form.** ``value(v)``, ``member(v)``, ``not_member(v)``, ``not_value(v)`` are equivalent to
  a proposition of the *same form* with an equal normalised value. ``enumeration`` is equivalent to
  an enumeration with the same set of normalised values.
* **``member(v)`` is NOT equivalent to ``enumeration([v])``.** An enumeration closes the world; a
  member does not, so one cannot confirm the other.
* ``belief_of(h, P)`` is equivalent to ``belief_of(h', P')`` when the holders match after string
  normalisation and the inner propositions are equivalent. An attribution is never equivalent to a
  plain proposition (an attribution does not confirm its content).
"""

from __future__ import annotations

import unicodedata
from typing import Any

from palimem.types import (
    BeliefOfProp,
    EnumerationProp,
    MemberProp,
    NotMemberProp,
    NotValueProp,
    Proposition,
    Value,
    ValueProp,
    canonical_json,
)


def normalise_string(s: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", s).split()).casefold()


def normalise_value(v: Value) -> Value:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return normalise_string(v)
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _norm(p: Proposition) -> dict[str, Any]:
    if isinstance(p, ValueProp | MemberProp | NotMemberProp | NotValueProp):
        return {"form": p.form, "v": normalise_value(p.value)}
    if isinstance(p, EnumerationProp):
        vals = {canonical_json(normalise_value(v)): normalise_value(v) for v in p.values}
        return {"form": "enumeration", "values": [vals[k] for k in sorted(vals)]}
    assert isinstance(p, BeliefOfProp)
    return {"form": "belief_of", "holder": normalise_string(p.holder), "proposition": _norm(p.proposition)}


def proposition_signature(p: Proposition) -> str:
    """Canonical signature: two propositions are equivalent iff their signatures are equal."""
    return canonical_json(_norm(p))


def equivalent(a: Proposition, b: Proposition) -> bool:
    return proposition_signature(a) == proposition_signature(b)
