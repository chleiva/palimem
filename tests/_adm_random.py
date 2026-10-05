"""Random admission logs that exercise every rule the incremental path updates: confirmations, quarantine, origin
groups, corrections, withdrawals (also of actors), source-level withdrawals, allege downgrades, authority grants,
attributions, agent origins and out-of-order effects (a withdrawal or confirmation arriving long after its target)."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from palimem.admission import AdmissionConfig, SourceStatus, derive_ulid
from palimem.types import (
    AuthorityRule,
    BeliefOfProp,
    Cue,
    EnumerationProp,
    Key,
    LogEntry,
    MemberProp,
    Origin,
    Power,
    Profile,
    Proposition,
    Report,
    Source,
    Targets,
    ValueProp,
    Who,
    WhoKind,
)

T0 = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)

# (source id, class): the classes drive quarantine / block through the default class table
SOURCES = (
    ("s0", "standard"),
    ("s1", "trusted"),
    ("s2", "low"),
    ("s3", "quarantined"),
    ("s4", "quarantined"),
    ("s5", "blocked"),
    ("registry", "trusted"),
)
GROUPS = ("g0", "g1", "g2", "g3")
ENTITIES = ("alice", "bob")
ATTRS = ("employer", "city")
VALUES = ("Acme", "acme", " ACME ", "Globex", "Initech")

SOURCE_LEVEL_RULES = (
    AuthorityRule(who=Who(kind=WhoKind.PRINCIPAL, value="connector:registry"), may=(Power.WITHDRAW,), targets=Targets.SOURCE),
    AuthorityRule(who=Who(kind=WhoKind.PRINCIPAL, value="user:u1"), may=(Power.WITHDRAW, Power.CORRECT, Power.DISPUTE)),
    AuthorityRule(who=Who(kind=WhoKind.TARGET_SOURCE), may=(Power.WITHDRAW, Power.CORRECT)),
    AuthorityRule(who=Who(kind=WhoKind.PRINCIPAL, value="agent:planner"), may=(Power.DISPUTE,)),
)


@dataclass(frozen=True)
class Variant:
    name: str
    config: AdmissionConfig


def variants() -> list[Variant]:
    return [
        Variant("product", AdmissionConfig()),
        Variant("product-grants", AdmissionConfig(rules=SOURCE_LEVEL_RULES)),
        Variant("product-not-live", AdmissionConfig(rules=SOURCE_LEVEL_RULES, acting_reports_must_be_live=False)),
        Variant("compat", AdmissionConfig(profile=Profile.REVISE_STREAM_V1)),
        Variant("compat-live", AdmissionConfig(profile=Profile.REVISE_STREAM_V1, acting_reports_must_be_live=True)),
        Variant(
            "override-quarantine",
            AdmissionConfig(
                rules=SOURCE_LEVEL_RULES,
                source_status={"s0": SourceStatus.QUARANTINED, "s2": SourceStatus.NORMAL},
            ),
        ),
    ]


def _proposition(rng: random.Random, origin: Origin) -> Proposition:
    v = rng.choice(VALUES)
    if origin is Origin.ATTRIBUTED:
        holder = rng.choice(("alice", "bob", "Alice "))
        return BeliefOfProp(holder=holder, proposition=ValueProp(value=v))
    r = rng.random()
    if r < 0.7:
        return ValueProp(value=v)
    if r < 0.85:
        return MemberProp(value=v)
    return EnumerationProp(values=tuple(rng.sample(VALUES, rng.randint(0, 2))))


def random_log(seed: int, n: int = 40, *, key_count: int = 3) -> list[LogEntry]:
    """``n`` log entries (LSN 1..n, ids derived from the seed) with dense interactions on few keys."""
    rng = random.Random(seed)
    keys = [Key(entity=e, attr=a) for e in ENTITIES for a in ATTRS][:key_count]
    entries: list[LogEntry] = []
    for i in range(1, n + 1):
        src, cls = rng.choice(SOURCES)
        origin = rng.choices(
            (
                Origin.EXTERNAL_OBSERVATION,
                Origin.ATTRIBUTED,
                Origin.AGENT_STATEMENT,
                Origin.AGENT_HYPOTHESIS,
                Origin.PLAN,
            ),
            weights=(70, 8, 8, 6, 8),
        )[0]
        if origin is Origin.EXTERNAL_OBSERVATION:
            actor = rng.choices((f"connector:{src}", "user:u1", "connector:registry", "system:admin"), weights=(70, 12, 12, 6))[0]
        elif origin is Origin.ATTRIBUTED:
            actor = f"connector:{src}"
        else:
            actor = "agent:planner"
        group = rng.choice(GROUPS) if rng.random() < 0.7 else f"g_{src}"
        cue = rng.choices(
            (Cue.ASSERT, Cue.CHANGE, Cue.CORRECT, Cue.WITHDRAW, Cue.DISPUTE, Cue.ALLEGE),
            weights=(46, 8, 12, 16, 8, 6),
        )[0]
        if i == 1:
            cue = Cue.ASSERT
        key = rng.choice(keys)
        target: str | None = None
        prop: Proposition | None = None
        if cue in (Cue.CORRECT, Cue.WITHDRAW, Cue.DISPUTE, Cue.ALLEGE):
            if rng.random() < 0.07 or not entries:
                target = derive_ulid("missing", str(seed), str(i))
            else:
                # reach far back as often as near, so effects arrive out of order
                tgt = rng.choice(entries[-6:]) if rng.random() < 0.5 else rng.choice(entries)
                target = tgt.report.id
                if rng.random() < 0.8:
                    key = tgt.report.key
        if cue in (Cue.ASSERT, Cue.CHANGE, Cue.CORRECT):
            prop = _proposition(rng, origin)
        elif origin is Origin.ATTRIBUTED:
            origin = Origin.EXTERNAL_OBSERVATION  # an attributed operator cue has no meaning; keep the log valid
        report = Report(
            id=derive_ulid("rnd", str(seed), str(i)),
            key=key,
            cue=cue,
            proposition=prop,
            target=target,
            source=Source(id=src, cls=cls),
            origin=origin,
            origin_group=group,
            actor=actor,
        )
        entries.append(LogEntry(lsn=i, recorded_at=T0 + timedelta(seconds=i), report=report))
    return entries
