"""Authority evaluation for ``correct`` / ``withdraw`` / ``dispute`` (S-02, S-07, decided).

The grant table is a flat, ordered list of :class:`~palimem.types.AuthorityRule`; per-attribute rules
(``Attr.authority``) come first, then the global table of the admission config; the first rule
that matches decides (and its ``targets`` decides the *extent* of a withdrawal: one report, or every
report of the target's source). Product default: **own source only**. Compat profile
``revise-stream-v1``: the paper's rules (anyone withdraws, same origin group corrects).

Invariants enforced here, because the types cannot check wildcard ``Who`` kinds against principals
at load time (docs/TYPES.md):

* **Agent invariant.** A principal of kind ``agent`` never withdraws or corrects a report whose origin
  is outside the agent-class origins, whatever the table says. Wildcard kinds (``any``,
  ``origin_group``, ``target_*``) therefore can never hand an agent authority over external
  evidence, and an agent never gets a source- or table-wide extent.
* **Agent dispute needs an explicit grant.** For an agent, only a rule naming the principal
  (``Who(kind=principal)``) can grant ``dispute``; wildcards do not count. Without one the write is
  an ``allege``.
* ``origin_group`` is never authority outside the compat profile (rejected at config load).
"""

from __future__ import annotations

from dataclasses import dataclass

from palimem.types import (
    AuthorityRule,
    Power,
    PrincipalKind,
    Report,
    Schema,
    Targets,
    Who,
    WhoKind,
    principal_kind,
)
from palimem.types.authority import AGENT_CLASS_ORIGINS

from .config import AdmissionConfig


@dataclass(frozen=True)
class AuthDecision:
    allowed: bool
    reason: str  # "rule" | "agent_invariant" | "no_matching_rule"
    rule: AuthorityRule | None = None
    rule_index: int | None = None

    @property
    def extent(self) -> Targets:
        """How far an allowed withdrawal reaches (``report`` unless the matching rule says otherwise)."""
        return self.rule.targets if self.rule is not None else Targets.REPORT


def _deny(reason: str) -> AuthDecision:
    return AuthDecision(allowed=False, reason=reason)


class Authorizer:
    def __init__(self, config: AdmissionConfig, schema: Schema | None = None) -> None:
        self.config = config
        self.schema = schema
        self._global = config.effective_rules()

    def rules_for(self, attr: str) -> tuple[AuthorityRule, ...]:
        local: tuple[AuthorityRule, ...] = ()
        if self.schema is not None:
            try:
                local = self.schema.attr(attr).authority
            except KeyError:
                local = ()
        return local + self._global

    @staticmethod
    def _who_matches(who: Who, report: Report, target: Report, kind: PrincipalKind, power: Power) -> bool:
        if who.kind is WhoKind.PRINCIPAL:
            return report.actor == who.value
        if kind is PrincipalKind.AGENT and power is Power.DISPUTE:
            return False  # an agent disputes only by an explicit, named grant
        if who.kind is WhoKind.ANY:
            return kind is not PrincipalKind.AGENT
        if who.kind is WhoKind.ORIGIN_GROUP:
            return report.origin_group == who.value
        if who.kind is WhoKind.TARGET_SOURCE:
            return report.source.id == target.source.id
        if who.kind is WhoKind.TARGET_ACTOR:
            return report.actor == target.actor
        if who.kind is WhoKind.TARGET_ORIGIN_GROUP:  # compat profile only (config rejects it elsewhere)
            return report.origin_group == target.origin_group
        return False  # pragma: no cover - exhaustive over WhoKind

    def check(self, report: Report, power: Power, target: Report) -> AuthDecision:
        """May ``report.actor`` exercise ``power`` on ``target``?"""
        kind = principal_kind(report.actor)
        agent = kind is PrincipalKind.AGENT
        if agent and power in (Power.WITHDRAW, Power.CORRECT) and target.origin not in AGENT_CLASS_ORIGINS:
            return _deny("agent_invariant")
        for i, rule in enumerate(self.rules_for(target.key.attr)):
            if power not in rule.may:
                continue
            if not rule.on.matches(target.key):
                continue
            if rule.over_origins is not None and target.origin not in rule.over_origins:
                continue
            if not self._who_matches(rule.who, report, target, kind, power):
                continue
            if agent and rule.targets is not Targets.REPORT:
                continue  # an agent never gets a source- or table-wide extent
            return AuthDecision(allowed=True, reason="rule", rule=rule, rule_index=i)
        return _deny("no_matching_rule")
