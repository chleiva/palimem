"""Entity resolution: canonicalisation, ``find``, and reversible entity merges (T-G3, T-C7). See docs/ENTITIES.md.

* :mod:`~palimem.entities.normalize`: names to canonical forms and similarity measures (deterministic, stdlib only);
* :mod:`~palimem.entities.resolver`: a lexical resolver that *proposes* merges and never applies one;
* :mod:`~palimem.entities.registry` / :mod:`~palimem.entities.layer`: merges as recorded, reversible decisions in the
  evidence log and the pipeline hooks that justify a merged class as one entity;
* :mod:`~palimem.entities.api`: the privileged host API (``Entities``: merge, unmerge, propose, apply, find).
"""

from palimem.entities.api import (
    Entities,
    EntitiesError,
    EntitiesNotEnabled,
    KeyCandidate,
    MergeRecord,
    MergeRejected,
    UnknownEntity,
    attach_layer,
    enable_entity_merges,
    merge_attr_spec,
)
from palimem.entities.layer import EntityLayer
from palimem.entities.normalize import canonical_form, canonical_tokens
from palimem.entities.registry import ENTITY_MERGE_ATTR, MergeDecision, MergeOp
from palimem.entities.resolver import (
    AliasTable,
    LexicalResolver,
    MergeProposal,
    ResolverBackend,
    ResolverPolicy,
    Similarity,
    propose_merges,
    similarity,
)

__all__ = [
    "ENTITY_MERGE_ATTR", "AliasTable", "Entities", "EntitiesError", "EntitiesNotEnabled", "EntityLayer", "KeyCandidate",
    "LexicalResolver", "MergeDecision", "MergeOp", "MergeProposal", "MergeRecord", "MergeRejected", "ResolverBackend",
    "ResolverPolicy", "Similarity", "UnknownEntity", "attach_layer", "canonical_form", "canonical_tokens",
    "enable_entity_merges", "merge_attr_spec", "propose_merges", "similarity",
]
