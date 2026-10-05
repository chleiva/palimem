"""``find``: names the caller does not know exactly resolve to keys, with their status, through the merge layer."""

from __future__ import annotations

import pytest

from palimem import Memory as Facade
from palimem.entities import Entities, EntitiesNotEnabled
from palimem.memory import Memory
from palimem.types import KernelStatus
from tests._pipeline_helpers import Clock, assertion, make_backend
from tests.entities._helpers import merge_setup

BACKENDS = ["memory", "sqlite"]


@pytest.fixture(params=BACKENDS)
def ms(request: pytest.FixtureRequest) -> tuple[Memory, Entities]:
    m, ent = merge_setup(make_backend(request.param, Clock()))
    m.append(assertion("alex", "employer", "veltran", source="press"))
    m.append(assertion("veltran", "hq_city", "tessaly", source="registry"))
    m.append(assertion("Veltran Inc", "hq_city", "ashford", source="wire"))
    m.append(assertion("acme", "hq_city", "brook", source="registry"))
    return m, ent


def test_find_returns_keys_with_their_current_status(ms: tuple[Memory, Entities]) -> None:
    _, ent = ms
    out = ent.find("veltran", "hq_city")
    got = {(c.entity, c.attr): c.kernel_status for c in out}
    assert got[("veltran", "hq_city")] == "established" and got[("Veltran Inc", "hq_city")] == "established"
    assert all(c.ambiguous for c in out)  # two classes score alike: the caller sees the ambiguity before reading
    assert ("acme", "hq_city") not in got


def test_after_a_merge_find_collapses_the_class_and_the_ambiguity_goes(ms: tuple[Memory, Entities]) -> None:
    _, ent = ms
    ent.merge("Veltran Inc", "veltran", reason="same company")
    out = ent.find("Veltran Inc", "hq_city")
    assert [(c.entity, c.attr) for c in out] == [("veltran", "hq_city")]
    c = out[0]
    assert c.kernel_status == "unresolved" and c.matched == ("Veltran Inc", "veltran") and not c.ambiguous


def test_find_before_the_merge_sees_the_old_classes(ms: tuple[Memory, Entities]) -> None:
    m, ent = ms
    before = m.backend.head().lsn
    ent.merge("Veltran Inc", "veltran", reason="r")
    assert {c.entity for c in ent.find("veltran", "hq_city", as_of=before)} == {"veltran", "Veltran Inc"}


def test_typos_and_blocked_neighbours(ms: tuple[Memory, Entities]) -> None:
    _, ent = ms
    assert "veltran" in {c.entity for c in ent.find("Veltrann", "hq_city")}  # a doubled letter
    assert ent.find("Acme 2", "hq_city") == []  # a different number is a different thing


def test_attribute_text_resolves_through_declared_aliases(ms: tuple[Memory, Entities]) -> None:
    _, ent = ms
    assert [c.attr for c in ent.find("alex", "employer")] == ["employer"]
    assert ent.find("alex", "works at") == []
    ent.declare_attr_alias("works at", "employer")
    assert [c.attr for c in ent.find("alex", "works at")] == ["employer"]
    with pytest.raises(Exception):
        ent.declare_attr_alias("x", "nope")


def test_find_by_attribute_alone_lists_the_entities_that_hold_it(ms: tuple[Memory, Entities]) -> None:
    _, ent = ms
    out = ent.find(None, "hq city")
    assert {(c.entity, c.attr) for c in out} == {("veltran", "hq_city"), ("Veltran Inc", "hq_city"), ("acme", "hq_city")}
    assert all(c.kernel_status == "established" for c in out)


def test_find_needs_something_to_look_for(ms: tuple[Memory, Entities]) -> None:
    _, ent = ms
    with pytest.raises(Exception):
        ent.find()


def test_reserved_attributes_are_not_findable(ms: tuple[Memory, Entities]) -> None:
    _, ent = ms
    ent.merge("Veltran Inc", "veltran", reason="r")
    assert all(not c.attr.startswith("__") for c in ent.find(None, "entity merge"))
    assert all(not c.attr.startswith("__") for c in ent.find("veltran"))


def test_the_marker_report_is_not_counted_as_evidence_about_an_entity(ms: tuple[Memory, Entities]) -> None:
    _, ent = ms
    ent.merge("Veltran Inc", "veltran", reason="r")
    assert set(ent.known_entities()) == {"Veltran Inc", "acme", "alex", "veltran"}


# --------------------------------------------------------------------------- the zero-config store


def test_find_works_over_the_zero_config_store_without_merges_enabled() -> None:
    mem = Facade()  # zero-config: no declared schema, attributes appear as they are first seen
    for entity, attr, value, source in (
        ("Alice Johnson", "city", "Paris", "hr"), ("alice  johnson", "city", "Rome", "crm"), ("bob", "city", "Oslo", "hr"),
    ):
        mem.observe({"entity": entity, "attr": attr, "value": value}, source=source)
    ent = Entities(mem.core)
    assert not ent.enabled
    out = ent.find("alice johnson", "city")
    assert {c.entity for c in out} == {"Alice Johnson", "alice  johnson"} and all(c.attr == "city" for c in out)
    assert {c.kernel_status for c in out} == {KernelStatus.ESTABLISHED.value} and all(c.ambiguous for c in out)
    assert ent.find("Alice Jonhson", "city")  # a transposition still finds a candidate
    props = ent.propose()  # proposals never need merges enabled
    assert any({p.alias, p.into} == {"Alice Johnson", "alice  johnson"} for p in props)
    with pytest.raises(EntitiesNotEnabled):
        ent.merge("Alice Johnson", "alice  johnson", reason="r")
