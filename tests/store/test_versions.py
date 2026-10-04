"""Versioned inputs (T-C6): schema, rule, semantic, admission and policy versions are stored, and a historical
``belief_as_of`` query is evaluated under the versions current at that log position."""

from __future__ import annotations

from dataclasses import replace

import pytest
from chain_fakes import ChainReviser, chain_schema
from fakes import FakeAdmitter, make_report

from palimem.store import InputKind, RevisionContext, StoreError
from palimem.types import Key, Versions

E = "alice"
A = Key(entity=E, attr="employer")


class VersionedReviser(ChainReviser):
    """Stamps each belief with the input versions the store says are in force (``RevisionContext.inputs``)."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[dict[str, int]] = []

    def revise(self, ctx: RevisionContext):  # type: ignore[no-untyped-def]
        self.seen.append(dict(ctx.inputs))
        v = Versions(schema=ctx.inputs.get("schema", 1), semantic=ctx.inputs.get("semantic", 1), admission=ctx.inputs.get("admission", 1))
        return [replace(b, versions=v) for b in super().revise(ctx)]


def add(b, report, idem, reviser):  # type: ignore[no-untyped-def]
    return b.append(report, idempotency_key=idem, admitter=FakeAdmitter(), reviser=reviser)


def test_a_historical_query_is_evaluated_under_the_versions_current_at_that_log_position(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    r = VersionedReviser()
    b.put_schema(chain_schema(1))
    b.put_input(InputKind.SEMANTIC, 1, {"semantics": "v0.3", "self_update": False})
    b.put_input(InputKind.ADMISSION, 1, {"grants": []})
    b.put_input(InputKind.POLICY, 1, {"abstain_threshold": 0.5})
    first = add(b, make_report(E, "employer", "acme"), "k1", r)
    h.clock.tick(60)
    b.put_schema(chain_schema(2, band=True))  # a new rule appears: tax_band reads tax_city
    b.put_input(InputKind.SEMANTIC, 2, {"semantics": "v0.3", "self_update": True})
    b.put_input(InputKind.ADMISSION, 2, {"grants": ["connector:registry may withdraw its own"]})  # a grant change is an admission version
    second = add(b, make_report(E, "employer", "globex", source="s2"), "k2", r)

    # what the store passed to the reviser at each append
    assert r.seen == [
        {"schema": 1, "semantic": 1, "admission": 1, "policy": 1},
        {"schema": 2, "semantic": 2, "admission": 2, "policy": 1},
    ]
    # the beliefs record the versions they were computed under
    assert b.belief_at(A, 1).versions == Versions(schema=1, semantic=1, admission=1)  # type: ignore[union-attr]
    assert b.belief_at(A, 2).versions == Versions(schema=2, semantic=2, admission=2)  # type: ignore[union-attr]

    h1, h2, now = b.historical_inputs(1), b.historical_inputs(2), b.historical_inputs()
    assert h1.versions() == {"schema": 1, "semantic": 1, "admission": 1, "policy": 1} and h1.lsn == 1
    assert h2.versions() == {"schema": 2, "semantic": 2, "admission": 2, "policy": 1} and h2.lsn == 2
    assert now.versions() == h2.versions() and now.lsn == 3  # "now" = the version the next append would see
    assert h1.schema is not None and [a.name for a in h1.schema.attrs].count("tax_band") == 0
    assert h2.schema is not None and "tax_band" in [a.name for a in h2.schema.attrs]
    work_city = next(a for a in h1.schema.attrs if a.name == "work_city")  # rule versions travel with the schema
    assert work_city.rule is not None and work_city.rule.fn == "f_work_city"
    assert h1.semantic is not None and h1.semantic[1]["self_update"] is False and h2.semantic is not None and h2.semantic[1]["self_update"] is True
    assert h2.admission is not None and "grants" in h2.admission[1]

    # a timestamp resolves through the log: the last LSN recorded at or before it
    t1, t2 = first.entry.recorded_at, second.entry.recorded_at
    assert b.historical_inputs(t1).versions()["schema"] == 1
    assert b.historical_inputs(t2).versions()["schema"] == 2
    assert b.attr_dependents("tax_city", as_of=1) == () and b.attr_dependents("tax_city", as_of=2) == ("tax_band",)
    assert b.schema(as_of=1) == h1.schema


def test_versions_only_increase_and_schema_goes_through_put_schema(h) -> None:  # type: ignore[no-untyped-def]
    b = h.backend
    b.put_schema(chain_schema(2))
    with pytest.raises(StoreError):
        b.put_schema(chain_schema(2))
    with pytest.raises(StoreError):
        b.put_schema(chain_schema(1))
    b.put_input(InputKind.POLICY, 3, {"v": 3})
    with pytest.raises(StoreError):
        b.put_input(InputKind.POLICY, 3, {"v": 3})
    with pytest.raises(ValueError):
        b.put_input(InputKind.SCHEMA, 5, {})


def test_no_inputs_means_an_empty_snapshot(h) -> None:  # type: ignore[no-untyped-def]
    snap = h.backend.historical_inputs()
    assert snap.schema is None and snap.semantic is None and snap.admission is None and snap.policy is None
    assert snap.versions() == {}
