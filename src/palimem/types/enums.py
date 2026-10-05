"""Enumerations of the palimem contract (design v0.3 §Data and API)."""

from __future__ import annotations

from enum import Enum


class Cue(str, Enum):
    """Operator cue of a report."""

    ASSERT = "assert"
    CHANGE = "change"
    CORRECT = "correct"
    WITHDRAW = "withdraw"
    DISPUTE = "dispute"
    ALLEGE = "allege"
    # There is no `confirm` cue (S-01, decided): confirmation is always *derived* from an
    # equivalent report of another origin group and recorded as an AdmissionRecord.


class Origin(str, Enum):
    EXTERNAL_OBSERVATION = "external_observation"
    ATTRIBUTED = "attributed"
    AGENT_HYPOTHESIS = "agent_hypothesis"
    AGENT_STATEMENT = "agent_statement"
    PLAN = "plan"
    SIMULATION = "simulation"
    COUNTERFACTUAL = "counterfactual"


AGENT_ORIGINS = frozenset({Origin.AGENT_HYPOTHESIS, Origin.AGENT_STATEMENT, Origin.PLAN})
"""Agent-class origins (S-02/S-07 amendment: the only reports an agent may withdraw or correct)."""


class Precision(str, Enum):
    DAY = "day"
    MONTH = "month"
    YEAR = "year"


class AttrClass(str, Enum):
    SINGLE_STABLE = "single_stable"
    SINGLE_CHANGEABLE = "single_changeable"
    MULTI_SET = "multi_set"
    DERIVED = "derived"


class ValueType(str, Enum):
    ENTITY = "entity"
    DATE = "date"
    INT = "int"
    STRING = "string"
    BOOL = "bool"


class CompletenessMode(str, Enum):
    OPEN = "open"
    DECLARED = "declared"
    BY_ENUMERATION = "by_enumeration"


class PrincipalKind(str, Enum):
    """Principal kind, carried in the id prefix (``agent:planner``) and fixed by the host (S-07)."""

    AGENT = "agent"
    USER = "user"
    CONNECTOR = "connector"
    SYSTEM = "system"


class Power(str, Enum):
    CORRECT = "correct"
    WITHDRAW = "withdraw"
    DISPUTE = "dispute"
    MERGE = "merge"  # entity merges (author ruling 2026-10-05): granted by principal id, never to an agent


class WhoKind(str, Enum):
    """Who an authority rule grants to.

    ``PRINCIPAL``: one typed principal id. ``ORIGIN_GROUP``: principals whose report origin_group
    is the named one. ``ANY``: any non-agent principal (see the S-07 invariant).
    ``TARGET_SOURCE``: the actor whose source authored the target (the product default, S-02).
    ``TARGET_ACTOR``: the actor who submitted the target (identity-based, so it works across
    sessions; used by the built-in agent rule).
    ``TARGET_ORIGIN_GROUP``: the actor sharing the target's origin_group (compat profile only;
    origin_group is otherwise for corroboration, never authority).
    """

    PRINCIPAL = "principal"
    ORIGIN_GROUP = "origin_group"
    ANY = "any"
    TARGET_SOURCE = "target_source"
    TARGET_ACTOR = "target_actor"
    TARGET_ORIGIN_GROUP = "target_origin_group"


class Targets(str, Enum):
    """What a rule lets its holder act on: one report, every report of a source, or any report."""

    REPORT = "report"
    SOURCE = "source"
    ANY = "any"


class AdmissionOutcome(str, Enum):
    ADMISSIBLE = "admissible"
    QUARANTINED = "quarantined"
    EXCLUDED = "excluded"


class AdmissionReason(str, Enum):
    """Reason code of an admission decision. The set is closed per admission version."""

    ADMITTED = "admitted"  # external observation, source not quarantined, authority passed
    CONFIRMED = "confirmed"  # a quarantined report admitted by an admissible report of another origin group
    SOURCE_QUARANTINED = "source_quarantined"
    ORIGIN_NOT_ADMISSIBLE = "origin_not_admissible"  # agent-origin reports are never admissible
    SOURCE_BLOCKED = "source_blocked"  # the paper's `blocked` class
    AUTHORITY_FAILED = "authority_failed"  # recorded as `allege`: no effect on admissibility
    TARGET_MISSING = "target_missing"


class ExplainMode(str, Enum):
    ALL = "all"
    ONE = "one"


class Profile(str, Enum):
    OPEN_WORLD = "open-world"
    REVISE_STREAM_V1 = "revise-stream-v1"


class KernelStatus(str, Enum):
    """The five kernel statuses (S-04). ``possible`` is adapter-only and not a type-level status."""

    ESTABLISHED = "established"
    UNRESOLVED = "unresolved"
    UNKNOWN = "unknown"
    ESTABLISHED_EMPTY = "established_empty"
    ESTABLISHED_FALSE = "established_false"


class Decision(str, Enum):
    COMMIT = "commit"
    ABSTAIN = "abstain"
    ASK = "ask"


class ExplanationState(str, Enum):
    COMPLETE = "complete"
    TRUNCATED = "truncated"


class RuleFired(str, Enum):
    NONE = "none"
    PRIOR = "prior"
    THRESHOLD = "threshold"
    ASK = "ask"


class ResourceLimitedReason(str, Enum):
    INFERENCE_INCOMPLETE = "inference_incomplete"
    STALE_DEPENDENCY = "stale_dependency"
    STORE_DIRTY = "store_dirty"
    ENVIRONMENT_BUDGET = "environment_budget"  # S-06, decided: a key exceeded the environment budget


class NotReconstructableReason(str, Enum):
    ERASED = "erased"  # the belief version in force at the snapshot was redacted by an erasure (S-13)


class InvalidatedKind(str, Enum):
    REPORT = "report"
    ADMISSION = "admission"
    MERGE = "merge"
