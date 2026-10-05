"""Name normalisation and similarity for entity resolution (stdlib only, deterministic).

Canonicalisation is the layer admission and ``find`` share: one :func:`canonical_form` turns a surface name into the
string every comparison uses. It is deliberately conservative: it removes things that carry no identity (case,
punctuation, accents, honorifics, corporate suffixes) and nothing else. Whether two canonical forms denote one entity
is a *decision* (:mod:`palimem.entities.resolver`), never a side effect of normalisation.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from functools import lru_cache

# characters NFKD does not decompose into base + combining mark
_TRANSLIT = str.maketrans({
    "ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D", "ß": "ss", "æ": "ae", "Æ": "AE", "œ": "oe",
    "Œ": "OE", "ð": "d", "Ð": "D", "þ": "th", "Þ": "TH", "ı": "i",
})

_HONORIFICS = frozenset({"mr", "mrs", "ms", "miss", "dr", "prof", "sir", "madam", "mx"})
_CORPORATE = frozenset({
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "llc", "llp", "plc", "gmbh", "ag",
    "sa", "sarl", "bv", "nv", "oy", "ab", "srl", "spa", "pty", "pte", "kk",
})
_ARTICLES = frozenset({"the"})
_GENERATIONAL = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})
_SPELLED = {"saint": "st", "mount": "mt", "fort": "ft", "department": "dept", "international": "intl", "street": "st",
            "doctor": "dr", "professor": "prof"}


def fold(text: str) -> str:
    """Case- and accent-fold: ``"José García"`` -> ``"jose garcia"`` (no other change)."""
    t = unicodedata.normalize("NFKD", text.translate(_TRANSLIT))
    return "".join(c for c in t if not unicodedata.combining(c)).casefold()


_WORD = re.compile(r"[a-z0-9]+")


def raw_tokens(text: str) -> list[str]:
    """Alphanumeric tokens of the folded text, apostrophes dropped (``o'brien`` -> ``obrien``), other punctuation splits."""
    t = fold(text).replace("'", "").replace("’", "")
    return _WORD.findall(t)


def _spell(tok: str) -> str:
    return _SPELLED.get(tok, tok)


@lru_cache(maxsize=65536)
def canonical_tokens(text: str, *, kind: str = "auto") -> tuple[str, ...]:
    """The identity-bearing tokens of a name.

    * ``kind="person"``: honorifics are dropped; ``"Smith, John"`` is reordered to ``"john smith"``; generational
      suffixes (jr, sr, ii...) are **kept** (they distinguish people).
    * ``kind="org"``: leading articles and trailing corporate suffixes are dropped (``"The Hooli Group"``,
      ``"Veltran Inc."``); a name made only of such words is kept whole.
    * ``kind="auto"``: both kinds of dropping (honorifics, articles, corporate suffixes) with the reorder rule.
    Spelled abbreviations are unified (``saint`` -> ``st``, ``mount`` -> ``mt``, ``international`` -> ``intl``).
    """
    reorder = ("," in text) and kind in ("person", "auto") and text.count(",") == 1
    if reorder:
        last, first = text.split(",", 1)
        text = f"{first} {last}"
    toks = [_spell(t) for t in raw_tokens(text)]
    if kind in ("person", "auto"):
        toks = [t for t in toks if t not in _HONORIFICS]
    if kind in ("org", "auto"):
        kept = [t for t in toks if t not in _ARTICLES]
        while len(kept) > 1 and kept[-1] in _CORPORATE:
            kept.pop()
        toks = kept or toks
    return tuple(toks)


def canonical_form(text: str, *, kind: str = "auto") -> str:
    """The canonical string of a name (tokens joined by one space)."""
    return " ".join(canonical_tokens(text, kind=kind))


def char_ngrams(s: str, n: int = 3) -> set[str]:
    s = f" {s} "
    if len(s) < n:
        return {s}
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def dice(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return 2.0 * len(a & b) / (len(a) + len(b))


def jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def damerau_levenshtein(a: str, b: str) -> int:
    """Optimal-string-alignment distance (insert, delete, substitute, adjacent transposition)."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if not la or not lb:
        return max(la, lb)
    prev2: list[int] = []
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[lb]


def edit_similarity(a: str, b: str) -> float:
    m = max(len(a), len(b))
    return 1.0 if m == 0 else 1.0 - damerau_levenshtein(a, b) / m


def is_generational(tok: str) -> bool:
    return tok in _GENERATIONAL


__all__ = [
    "canonical_form", "canonical_tokens", "char_ngrams", "damerau_levenshtein", "dice", "edit_similarity", "fold",
    "is_generational", "jaccard", "raw_tokens",
]
