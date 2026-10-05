# Entity resolution: canonicalisation, `find` and reversible merges (T-G3, T-C7)

Code: `src/palimem/entities/` (`normalize`, `resolver`, `registry`, `layer`, `api`). Tests: `tests/entities/`.
Evaluation: `bench/entities/` (labelled pairs, frozen test split). Conformance fixture: `ind-09`.

## 1. What this fixes, and what it does not

The extractor splits one real-world entity across several names (`Veltran`, `Veltran Inc.`, `veltran`) and so across
several keys. Every belief about it is then split too: the registry's hq city sits on one key, the wire's on another, and
neither the conflict between them nor the rule that needs both is ever seen. That is *key fragmentation*, seen on
LongMemEval. Two things fix it, and they are kept apart on purpose:

* a **resolver** that *proposes* "these two names are one entity" (`palimem.entities.resolver`). It never applies anything;
* a **merge**, a recorded, reversible host decision that makes the pipeline justify the merged names as one entity
  (`Entities.merge` / `unmerge`).

A merge never deletes or edits evidence and never resolves a conflict. Merging two names whose reports disagree gives an
`unresolved` key with both candidates: a false merge is *visible* (and reversible), not a silent corruption.

## 2. A merge is a decision in the log

A merge is an admission-stage decision with its own id. It is recorded as a **marker report** on the reserved attribute
`__entity_merge__` (the same representation the compat profile uses for source-level retraction, `docs/PIPELINE.md`
section 3), so it gets everything the evidence log gives: an LSN, the salted hash chain, an idempotency key, crash safety,
export/import and erasure. **The marker's report id is the merge id.** Reversal is another marker (`unmerge`, naming the
merge id); nothing is edited.

```
Report(key=(<alias>, "__entity_merge__"), cue=assert, proposition=member(<canonical JSON>),
       actor="system:..."|"user:...", source.id="system:..."|"user:...", origin=external_observation)
  {"v":2,"op":"merge","into":"<entity>","reason":"...","resolver":{"method":"lexical|manual|...","score":0.91,"version":"1"}}
  {"v":2,"op":"unmerge","target":"<merge id>","reason":"...","resolver":{"method":"manual"}}
```

The payload is the typed `palimem.types.MergeMarker` (author ruling 2026-10-05). **Payload version 1**, the earlier ad-hoc form
with flat `method` and `score` members, is still **read** and mapped onto the same type, so logs written before this change keep
loading unchanged (nothing about their stored bytes moves); writers emit version 2. An unknown version, a missing member or an
unknown member makes the marker malformed, which the registry ignores.

A marker is **honoured** only when all of these hold (`registry.from_host`, `MergeRegistry.decision_of`): the actor holds the
**`merge` power** (`palimem.types.may_merge`): `system:` and `user:` principals by default (the source id must be of the same
kinds), any other non-agent principal (for example `connector:ops`) only with an explicit `merge` grant declared on the reserved
attribute's `authority`, and **never an `agent:` principal**; the origin is `external_observation`; admission admitted it (a
quarantined or blocked source is not honoured); and it decodes strictly. Anything else on the reserved attribute is ignored,
never raised. The agent tool API additionally refuses every attribute starting with `__`
(`reserved_attr`) and has no merge tool, so an agent cannot merge even by writing the marker.

## 3. Classes, representatives, and what is stored

The honoured decisions up to a log position define the **entity classes** at that position: a forest of active merge
edges (`registry.ClassState`), each class with one *representative* (the node no edge leaves). A merge between two names
already in one class, an unmerge of an unknown or already-undone merge, and a malformed marker are recorded no-ops.

Every key `(entity, attr)` still has its own belief from its own evidence, as before. On top of that, for a class of more
than one member the **representative's** key `(R, attr)` is justified from the evidence of *every member* (their reports
re-keyed to `(R, attr)` for the kernel; report ids, supports and pins keep the original reports). The members' own keys keep
their own beliefs untouched, which is why reversing a merge only recomputes the representative's keys.

* **Reads.** A read of any member key at a snapshot is answered by the representative's belief at that snapshot
  (`Memory._view_for` -> `EntityLayer.canon_key`). `belief_as_of` therefore uses the classes in force *then*: before the
  merge the alias answers from its own history, after it from the class. The answer's `justified.key` names the
  representative.
* **Derived keys.** The rules of derived keys read the representative too (`Resolver` canonicalisation), so a rule that
  names `Veltran Inc` sees what was reported about `veltran`. The dependents of a changed representative key are found
  under every name of the class, so a derived key that names an alias value is revised when the representative changes
  (`test_a_derived_key_that_names_an_alias_value_...`).
* **Verification.** `verify_beliefs` recomputes the same aggregated belief (`EntityLayer.recompute_base`), so stored and
  recomputed beliefs agree; completion jobs and erasure repair use the same path.

## 4. Pinning and exact reversal

A belief pins the merges its evidence **crossed** (`ClassState.merge_pins`): an edge is pinned by the representative's
key when some contributor (a member with evidence for the attribute) lies on the alias side of it. A class whose evidence
all sits on the representative pins nothing. Derived beliefs inherit the pins of the base beliefs they read.

Consequences, each a test:

* a merge writes exactly the beliefs pinned to its own id (plus the marker's own key, which pins its own report): the
  representative keys that consumed evidence across it and the derived keys built on them. A person who happens to share
  nothing with the merged names is not recomputed (`ind-09`: Bob's belief version is unchanged across the merge and the
  reversal);
* reversing the merge writes exactly the same set again and restores segments, pins and dependency keys **byte for byte**
  (dependency *versions* legitimately advance);
* a representative that is itself merged into another class (a chain `A -> B -> C`) goes back to its own evidence, so
  `verify_beliefs` stays clean through any sequence of merges and reversals, including reversing the middle one;
* the hash chain, `recover()` and `verify_beliefs` pass after every decision, on both backends, and a crash at each step
  of a merge's transaction leaves no trace and converges on retry with the same idempotency key.

Erasing the marker report (a tombstone) removes the merge: the registry resets and the pinned beliefs are repaired by the
store's ordinary erasure repair.

## 5. The host API (`Entities`)

`Entities(memory)` is **privileged host code**; the schema must declare the reserved attribute
(`enable_entity_merges(schema)`; `merge_attr_spec()` for a hand-built kernel schema). A `Memory` whose schema declares it
attaches the entity layer automatically, so a reopened store keeps resolving merged entities.

| call | what it does |
|---|---|
| `merge(alias, into, reason=, method=, score=)` | records a merge decision; returns a `MergeOutcome` (id, representative, members, `rewritten` keys); refuses unknown entities and an already-merged pair |
| `unmerge(merge_id, reason=)` | records the reversal of an active merge |
| `propose()` / `apply(proposal)` | resolver proposals over the entities in the log (nothing applied); `apply` records one with its score, method and resolver version |
| `canonical(e)`, `members(e)`, `merges()`, `history()` | the classes and decisions at any snapshot (`as_of`) |
| `records()` | the typed `palimem.types.MergeRecord` of every honoured merge at a snapshot: id, members, representative, reason, resolver (method, score, version), `admission_version`, and `reversed_by` (the unmerge decision that undid it, or `None`) |
| `find(entity_text, attr_text)` | see below; works on any store, merges enabled or not |

Contract (author ruling 2026-10-05, additive): `Power.MERGE` exists in `AuthorityRule` and `MergeRecord` is a first-class type.
A `merge` rule is granted on its own, by identity (`principal` or `any`, never over target reports), and **can never be granted
to an `agent` principal** (the rule refuses to build). Unchanged: merges stay a privileged host operation and the agent tool API
has no merge tool.

## 6. `find`

`find(entity_text, attr_text)` resolves names the caller does not know exactly **through the same canonicalisation the
resolver uses** and returns candidate keys with their current kernel status, so ambiguity is visible before a read:

* names of one merged class collapse to its representative (`matched` lists the member names that scored);
* `ambiguous` is set on every candidate when more than one class scores within 0.1 of the best;
* a name blocked by a conflicting number, generational suffix or qualifier (`Acme 2` vs `Acme 1`) is not a candidate;
* attribute text matches declared attribute names after canonicalisation, plus aliases the host declares
  (`declare_attr_alias("works at", "employer")`); `find(None, "hq city")` lists the entities that hold the attribute;
* reserved attributes (`__...`) never appear.

## 7. The lexical resolver

`similarity(a, b, kind=)` is deterministic and symmetric. Normalisation removes what carries no identity (case, accents,
punctuation, honorifics, articles, corporate suffixes, `saint`/`st`-style abbreviations, `Smith, John` order) and keeps
what does (generational suffixes, numbers). The score blends soft token alignment (equal, nickname table, one plausible
typo, prefix, initial) with character-trigram overlap, and is **capped by blockers** no similarity can override: conflicting
numbers, generational suffixes, a given-name conflict next to a shared family name, and a qualifier on each side
(`Veltran Systems` / `Veltran Capital`) or an extra qualifier on one (`Paris` / `Paris Hilton`). A single-letter
*substitution* is not accepted as a typo (`stark`/`spark`). A declared `AliasTable` says what no rule can (`Bombay` /
`Mumbai`); `distinct` wins over everything.

Proposals are blocked by shared tokens, token prefixes and initials, so the cost is not quadratic in the number of
entities. The default `ResolverPolicy` has `auto_at = None`: **no proposal is ever applied automatically**.

### Evaluation (`bench/entities`, `python bench/entities/eval_resolver.py --split dev|test`)

136 labelled pairs written by one author (with LLM assistance), 68 same and 68 different, in 23 categories (typos, nicknames,
initials, accents, word order, corporate suffixes, acronyms, homonyms, same-surname people, generational suffixes, numbers,
shared tokens, places, near names, unrelated, alias-only). Stratified dev/test split, frozen by checksum; the rules were
tuned on dev; **the test split was scored once, after the last rule change**. `alias_only` positives (5) are excluded from
lexical recall: no rule can resolve them (all 5 resolve with a declared alias). Wilson 95% intervals.

| threshold | split | precision | recall | false-merge rate | recall 95% | false-merge 95% |
|---|---|---|---|---|---|---|
| 0.6 (propose) | dev (32 / 35) | 0.970 | 1.000 | 0.029 (1 of 35) | [0.893, 1.000] | [0.005, 0.145] |
| 0.6 (propose) | **test** (31 / 33) | **0.938** | **0.968** | **0.061 (2 of 33)** | [0.838, 0.994] | [0.017, 0.196] |
| 0.8 | dev | 0.960 | 0.750 | 0.029 | [0.579, 0.867] | [0.005, 0.145] |
| 0.8 | **test** | **1.000** | **0.742** | **0.000 (0 of 33)** | [0.568, 0.863] | [0.000, 0.104] |

Test errors at 0.6: `Amazon` ~ `Amazonas` (the prefix rule), `J. Smith` ~ `Jane Smith` (the initial is compatible with
both `John` and `Jane`: the *label* is arguable, since `J. Smith` is a valid reading of either), and one miss, `Priya Nairr`
~ `Priya Nair` (the typo rule needs tokens of at least five letters). On dev the one false merge is `Alison Reed` ~
`Allison Reed` (a doubled letter is an accepted typo; these are different given names). None of these were tuned away.

Limits that matter more than the intervals: the set is small (66 test pairs, 33 positives), Western and English, written by
one author, and **a name is not an identity**. Two different people called `John Smith` look identical to every resolver in
this repository, and `canonical_equal` is *not* safe to auto-apply for that reason. Nothing here measures merges that need
context (a person who moved, a company that rebranded).

### False-merge policy (recommendation)

1. **Never auto-apply a fuzzy match.** A false merge at the proposal threshold is a few percent of different pairs on
   this set (upper bound about 20%), and each one rewrites every attribute of two entities until reversed. The default
   (`auto_at = None`) stands.
2. **Review above 0.8.** At 0.8 no false merge occurred on test (0 of 33, upper bound 10%) at about 74% recall of the
   lexically resolvable pairs. A review queue for 0.6 to 0.8 and a faster path above 0.8 is the practical split.
3. **Auto-apply only on an external identity.** If both names carry the same stable identifier from the source (a
   registry id), declare them in an `AliasTable` or merge them from that connector's host code with `method="external-id"`:
   that is evidence, not similarity.
4. **Preview before applying.** `MergeOutcome.rewritten` shows which beliefs a decision wrote; a merge exposes conflicts as
   `unresolved` instead of hiding them, so a wrong merge announces itself, and `unmerge` restores everything.

## 8. An embedding or LLM resolver (design only; nothing here calls a model)

`ResolverBackend` is a one-method protocol (`method`, `score(a, b) -> Similarity`); `propose_merges(backend=...)` and
`Entities.find` take any implementation, and its `method` and score travel in the recorded decision. Rules for a
model-backed one, written down before anyone builds it:

* every call goes through `palimem.costs` (pre-run estimate, ledger, hard cap) and the responses are cached by pair, so a
  re-run is free and reproducible offline;
* names and any context snippets are **untrusted text** (the extractor's injection findings apply): the model's reply is
  parsed into a score in `[0, 1]` and a short reason, nothing else; it can never name an action;
* the **blockers apply after the model**: a numeric, generational or declared-distinct block vetoes a high model score,
  so the model can raise a score only where the lexical rules did not block;
* it is calibrated and reported on the same labelled set (dev tuned, test once) with the same Wilson intervals, and the
  default policy still never auto-applies it;
* the recorded `method` carries the model id and prompt hash, so a changed model is a visible change, like an extractor's.

## 9. Known limits and contract gaps

* **Admission is per raw key.** Confirmation (an equivalent report from another origin group), the A-SELF/A-SU self-update
  and quarantine do not cross a merge: a quarantined report on one name is not confirmed by a report on its alias
  (strict xfail `test_known_gap_confirmation_does_not_cross_aliases`).
* **Audit paths ignore merges.** `Memory.justification`, `explain(depth=...)` and the yes/no slots replay a *raw* key.
* **Object canonicalisation is read-time only.** A rule that binds an entity-valued object canonicalises it when it reads;
  the stored values stay as reported.
* **`Belief.invalidated_by`** (`kind=merge`) is not populated: `verify_beliefs` compares it and a recomputation cannot know
  which decision caused a given version. The pinned merge id carries that information.
* ~~No `merge` `Power` / `MergeRecord` type in the contract~~ **resolved 2026-10-05** (section 5): `Power.MERGE`, `MergeMarker`, `MergeRecord`.
* **Attribute names** are canonicalised for `find` (declared aliases) but reports on `works_at` and `employer` are still
  different keys: schema-layer attribute aliasing at ingestion is not implemented.
* **Concurrency:** the registry is rebuilt from the committed log prefix, so a rolled-back append never entered it; a
  merge racing with appends is serialised by the store's single writer.
