"""A deterministic lexical entity resolver: scores pairs of names and *proposes* merges; it never applies one.

A merge corrupts beliefs when it is wrong, so the resolver's output is a :class:`MergeProposal` (a recorded
decision candidate with its score, features and reasons), and the default policy never auto-applies a fuzzy one
(``ResolverPolicy.auto_at is None``). Applying a proposal is a privileged host operation
(:meth:`palimem.entities.Entities.merge`); the optional embedding or LLM resolver (:class:`ResolverBackend`) plugs in
behind the same record.

The scoring is hand-written and conservative: soft token alignment plus character-trigram overlap, capped by
*blockers* (conflicting numbers, generational suffixes, given names, distinguishing qualifiers) that no amount of
surface similarity may override. Every rule is exercised by ``bench/entities`` (labelled pairs, false-merge rate).
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations
from typing import Protocol

from palimem.entities.normalize import (
    canonical_form,
    canonical_tokens,
    char_ngrams,
    damerau_levenshtein,
    dice,
    is_generational,
)

RESOLVER_VERSION = "lexical/1"

_NICKNAMES: dict[str, frozenset[str]] = {
    "robert": frozenset({"bob", "rob", "bobby", "robbie"}),
    "william": frozenset({"bill", "will", "billy", "willy"}),
    "elizabeth": frozenset({"liz", "beth", "betty", "lizzie"}),
    "katherine": frozenset({"kate", "katie", "kathy"}),
    "catherine": frozenset({"kate", "katie", "cathy"}),
    "jonathan": frozenset({"jon", "jonny"}),
    "alexander": frozenset({"alex", "xander", "sandy"}),
    "christopher": frozenset({"chris", "kit"}),
    "christian": frozenset({"chris"}),
    "michael": frozenset({"mike", "mick"}),
    "richard": frozenset({"rick", "dick", "rich"}),
    "margaret": frozenset({"maggie", "meg"}),
    "james": frozenset({"jim", "jimmy"}),
    "thomas": frozenset({"tom", "tommy"}),
    "jennifer": frozenset({"jen", "jenny"}),
    "daniel": frozenset({"dan", "danny"}),
    "joseph": frozenset({"joe"}),
}
_NICK_PAIRS = {frozenset({a, b}) for a, bs in _NICKNAMES.items() for b in bs}


# --------------------------------------------------------------------------- aliases


@dataclass(frozen=True)
class AliasTable:
    """User-declared facts: names that are the same entity (``same``) and pairs that must never merge (``distinct``).

    Both are canonical-form pairs and are the only way to resolve what no lexical rule can (``"Peggy Hill"`` and
    ``"Margaret Hill"``, ``"Bombay"`` and ``"Mumbai"``). ``distinct`` wins over everything, including ``same``."""

    same: frozenset[frozenset[str]] = frozenset()
    distinct: frozenset[frozenset[str]] = frozenset()

    @staticmethod
    def of(*, same: Iterable[tuple[str, str]] = (), distinct: Iterable[tuple[str, str]] = ()) -> AliasTable:
        def pair(a: str, b: str) -> frozenset[str]:
            return frozenset({canonical_form(a), canonical_form(b)})

        return AliasTable(frozenset(pair(a, b) for a, b in same), frozenset(pair(a, b) for a, b in distinct))

    def same_pair(self, a: str, b: str) -> bool:
        return frozenset({canonical_form(a), canonical_form(b)}) in self.same

    def distinct_pair(self, a: str, b: str) -> bool:
        return frozenset({canonical_form(a), canonical_form(b)}) in self.distinct


# --------------------------------------------------------------------------- scoring


@dataclass(frozen=True)
class Similarity:
    score: float
    reason: str  # the dominant rule: canonical_equal | declared_alias | declared_distinct | aligned | acronym | blocked:<why>
    features: Mapping[str, float] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return self.reason.startswith("blocked:") or self.reason == "declared_distinct"


def _is_typo(a: str, b: str) -> bool:
    """One plausible slip between two tokens: an adjacent transposition, a missing or extra letter that is not a plain
    suffix (``globe``/``globex`` are different names), or a doubled letter (``veltrann``). A single-letter
    *substitution* is not accepted: ``stark``/``spark`` and ``wayne``/``payne`` are different words far more often
    than they are one typo."""
    if a == b or min(len(a), len(b)) < 5 or abs(len(a) - len(b)) > 1:
        return False
    if damerau_levenshtein(a, b) != 1:
        return False
    if len(a) == len(b):
        return sorted(a) == sorted(b)  # same letters, adjacent swap; otherwise a substitution
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    if long_.startswith(short):  # an appended letter: a different name unless it doubles the last one
        return long_[-1] == long_[-2]
    return True


def _token_score(a: str, b: str) -> tuple[float, str]:
    """How well two tokens can be the same name part, with the rule that said so ('' = incompatible)."""
    if a == b:
        return 1.0, "equal"
    if len(a) == 1 and not a.isdigit() and b.startswith(a):
        return 0.6, "initial"
    if len(b) == 1 and not b.isdigit() and a.startswith(b):
        return 0.6, "initial"
    if frozenset({a, b}) in _NICK_PAIRS:
        return 0.9, "nickname"
    if _is_typo(a, b):
        return 0.9, "typo"
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 3 and long_.startswith(short) and len(long_) - len(short) >= 2 and not short.isdigit():
        return 0.75, "prefix"
    return 0.0, ""


def _align(ta: Sequence[str], tb: Sequence[str]) -> tuple[float, list[tuple[str, str, float, str]], list[str], list[str]]:
    """Greedy best-first matching of the tokens of two names. Returns the sum of the matched scores, the matches,
    and the unmatched tokens of each side."""
    cands = []
    for i, a in enumerate(ta):
        for j, b in enumerate(tb):
            s, why = _token_score(a, b)
            if s > 0:
                cands.append((s, -abs(i - j), i, j, a, b, why))
    cands.sort(key=lambda c: (-c[0], -c[1], c[2], c[3]))
    used_a: set[int] = set()
    used_b: set[int] = set()
    matches: list[tuple[str, str, float, str]] = []
    for s, _d, i, j, a, b, why in cands:
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        matches.append((a, b, s, why))
    return (
        sum(m[2] for m in matches), matches,
        [t for i, t in enumerate(ta) if i not in used_a], [t for j, t in enumerate(tb) if j not in used_b],
    )


def _acronym(ta: Sequence[str], tb: Sequence[str]) -> bool:
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(short) != 1 or len(long_) < 2 or not (2 <= len(short[0]) <= 6) or not short[0].isalpha():
        return False
    return short[0] == "".join(t[0] for t in long_ if t not in {"of", "and", "for", "the", "de", "la"})


def similarity(a: str, b: str, *, kind: str = "auto", aliases: AliasTable | None = None) -> Similarity:
    """Score two surface names in ``[0, 1]``. See the module docstring for the rule set."""
    al = aliases or AliasTable()
    if al.distinct_pair(a, b):
        return Similarity(0.0, "declared_distinct")
    if al.same_pair(a, b):
        return Similarity(1.0, "declared_alias")
    ta, tb = canonical_tokens(a, kind=kind), canonical_tokens(b, kind=kind)
    if not ta or not tb:
        return Similarity(0.0, "blocked:empty")
    if ta == tb:
        return Similarity(1.0, "canonical_equal")

    # blockers: conflicting evidence no similarity may override
    nums_a, nums_b = {t for t in ta if any(c.isdigit() for c in t)}, {t for t in tb if any(c.isdigit() for c in t)}
    if nums_a != nums_b:
        return Similarity(0.1, "blocked:numeric_mismatch", {"numbers": 1.0})
    gen_a, gen_b = {t for t in ta if is_generational(t)}, {t for t in tb if is_generational(t)}
    if gen_a != gen_b:
        return Similarity(0.1, "blocked:generational_suffix", {"generational": 1.0})

    total, matches, left_a, left_b = _align(ta, tb)
    n = max(len(ta), len(tb))
    align = total / n
    ng = dice(char_ngrams(" ".join(ta)), char_ngrams(" ".join(tb)))
    feats = {"align": round(align, 4), "ngram": round(ng, 4), "matched": float(len(matches))}

    if _acronym(ta, tb):
        return Similarity(0.75, "acronym", feats)

    # extra tokens are only harmless when they carry no identity: single-letter initials, or (for people) a further
    # name part next to at least two shared parts ("Christian Leiva" / "Christian Leiva Beltran")
    real_a = [t for t in left_a if len(t) > 1]
    real_b = [t for t in left_b if len(t) > 1]
    if real_a and real_b:  # each side has a qualifier the other lacks: two different things
        return Similarity(min(0.4, 0.4 * align + 0.2 * ng + 0.1), "blocked:distinct_qualifier", feats)
    if real_a or real_b:
        extra = real_a or real_b
        strong = [m for m in matches if m[3] in ("equal", "nickname", "typo")]
        if not (kind == "person" and len(strong) >= 2 and len(extra) == 1):
            return Similarity(min(0.5, 0.5 * align + 0.2 * ng), "blocked:extra_qualifier", feats)
    # a given-name conflict next to a shared family name is two people
    if len(ta) >= 2 and len(tb) >= 2 and ta[-1] == tb[-1] and ta[0] != tb[0]:
        g, why = _token_score(ta[0], tb[0])
        if g == 0.0:
            return Similarity(0.3, "blocked:given_name_conflict", feats)
    score = 0.65 * align + 0.35 * ng
    if any(m[3] == "prefix" for m in matches):
        score = min(score, 0.85)
    if any(m[3] == "initial" for m in matches):
        score = min(score, 0.8)
    return Similarity(round(max(0.0, min(1.0, score)), 4), "aligned", feats)


# --------------------------------------------------------------------------- proposals and policy


@dataclass(frozen=True)
class ResolverPolicy:
    """What the resolver may do with a score. ``auto_at is None`` (the default) means *never apply automatically*:
    every proposal waits for a host decision. Recommended: leave it ``None`` for fuzzy matches (see
    ``docs/ENTITIES.md``, false-merge policy)."""

    propose_at: float = 0.6
    auto_at: float | None = None
    auto_reasons: frozenset[str] = frozenset({"canonical_equal", "declared_alias"})

    def __post_init__(self) -> None:
        if not 0.0 < self.propose_at <= 1.0:
            raise ValueError("propose_at must be in (0, 1]")
        if self.auto_at is not None and not self.propose_at <= self.auto_at <= 1.0:
            raise ValueError("auto_at must be in [propose_at, 1]")

    def decide(self, sim: Similarity) -> str:
        """``"none"`` (below ``propose_at`` or blocked), ``"propose"`` (needs a host decision) or ``"auto_eligible"``."""
        if sim.blocked or sim.score < self.propose_at:
            return "none"
        if self.auto_at is not None and sim.score >= self.auto_at and sim.reason in self.auto_reasons:
            return "auto_eligible"
        return "propose"


@dataclass(frozen=True)
class MergeProposal:
    """A candidate merge: a recorded decision that has *not* been applied. ``into`` is the suggested canonical name
    (the more frequently used one when counts are known, else the longer canonical form)."""

    alias: str
    into: str
    score: float
    reason: str
    method: str
    resolver_version: str
    decision: str  # propose | auto_eligible
    features: Mapping[str, float] = field(default_factory=dict)

    @property
    def id(self) -> str:
        """A stable digest of the pair and the resolver version: the same proposal always has the same id."""
        h = hashlib.sha256(f"{self.resolver_version}|{self.alias}|{self.into}".encode()).hexdigest()
        return h[:16]


class ResolverBackend(Protocol):
    """The optional second resolver (embeddings or an LLM) behind the same decision record. It scores a pair and
    names its method; nothing in palimem calls a model on its own (calls go through ``palimem.costs`` when a host
    supplies an implementation)."""

    method: str

    def score(self, a: str, b: str) -> Similarity: ...


class LexicalResolver:
    """The built-in :class:`ResolverBackend`."""

    method = "lexical"

    def __init__(self, *, kind: str = "auto", aliases: AliasTable | None = None) -> None:
        self.kind = kind
        self.aliases = aliases or AliasTable()

    def score(self, a: str, b: str) -> Similarity:
        return similarity(a, b, kind=self.kind, aliases=self.aliases)


def _blocking_keys(name: str, kind: str) -> set[str]:
    toks = canonical_tokens(name, kind=kind)
    keys = {f"t:{t}" for t in toks if len(t) > 1}
    keys |= {f"p:{t[:3]}" for t in toks if len(t) >= 3}
    if len(toks) == 1 and len(toks[0]) >= 2:
        keys.add(f"a:{toks[0]}")
    keys.add("i:" + "".join(t[0] for t in toks))
    return keys


def propose_merges(
    entities: Sequence[str], *, backend: ResolverBackend | None = None, policy: ResolverPolicy | None = None,
    counts: Mapping[str, int] | None = None, same_class: Iterable[frozenset[str]] = (), kind: str = "auto",
) -> list[MergeProposal]:
    """Proposals over a set of entity names (blocked by shared tokens, token prefixes and initials, so the cost is not
    quadratic in the number of entities). ``same_class`` lists entity sets that are already merged: a pair inside
    one is skipped. Proposals are returned best first and are never applied."""
    be = backend or LexicalResolver(kind=kind)
    pol = policy or ResolverPolicy()
    cnt = counts or {}
    uniq = sorted(set(entities))
    already = [frozenset(s) for s in same_class]
    buckets: dict[str, list[str]] = defaultdict(list)
    for e in uniq:
        for k in _blocking_keys(e, kind):
            buckets[k].append(e)
    seen: set[tuple[str, str]] = set()
    out: list[MergeProposal] = []
    for members in buckets.values():
        if len(members) > 200:  # a token shared by hundreds of names carries no identity
            continue
        for a, b in combinations(members, 2):
            pair = (a, b) if a < b else (b, a)
            if pair in seen:
                continue
            seen.add(pair)
            if any(a in s and b in s for s in already):
                continue
            sim = be.score(a, b)
            decision = pol.decide(sim)
            if decision == "none":
                continue
            ca, cb = cnt.get(a, 0), cnt.get(b, 0)
            into, alias = (a, b) if (ca, len(canonical_form(a)), b) >= (cb, len(canonical_form(b)), a) else (b, a)
            out.append(MergeProposal(
                alias=alias, into=into, score=sim.score, reason=sim.reason, method=be.method,
                resolver_version=RESOLVER_VERSION, decision=decision, features=dict(sim.features),
            ))
    out.sort(key=lambda p: (-p.score, p.alias, p.into))
    return out


__all__ = [
    "RESOLVER_VERSION", "AliasTable", "LexicalResolver", "MergeProposal", "ResolverBackend", "ResolverPolicy",
    "Similarity", "propose_merges", "similarity",
]
