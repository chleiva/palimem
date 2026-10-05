"""``inertia=False`` is specified but refused until implemented (author ruling 6 of 2026-10-05, S-08).

Specified meaning: the value holds only within its stated valid interval, with no extension. The kernel refuses it with that
explanation instead of silently treating it as inertia-on, and the compat profile (inertia on every attribute, parity with the
oracle is the arbiter) is unaffected.
"""

from __future__ import annotations

import pytest

from palimem.compat import schema_from_kernel
from palimem.kernel import AttrSpec, KernelSchema, KernelUnsupported
from palimem.types import Attr, AttrClass, Schema, ValueType


def schema(inertia: bool, cls: AttrClass = AttrClass.SINGLE_CHANGEABLE) -> Schema:
    return Schema(version=1, attrs=(Attr(name="employer", attr_class=cls, value_type=ValueType.ENTITY, inertia=inertia),))


@pytest.mark.parametrize("cls", [AttrClass.SINGLE_CHANGEABLE, AttrClass.SINGLE_STABLE, AttrClass.MULTI_SET])
def test_inertia_false_is_refused_with_the_specified_meaning(cls: AttrClass) -> None:
    with pytest.raises(KernelUnsupported) as e:
        KernelSchema.from_schema(schema(False, cls))
    msg = str(e.value)
    assert "inertia=False" in msg and "valid interval" in msg and "no extension" in msg and "not implemented" in msg


def test_inertia_true_is_accepted() -> None:
    ks = KernelSchema.from_schema(schema(True))
    assert ks.spec("employer").cardinality == "single" and ks.spec("employer").changeable


def test_the_compat_profile_applies_inertia_to_every_attribute() -> None:
    # the compat schema builder maps every attribute with inertia on, whatever its class (parity with the deposited code)
    ks = KernelSchema(attrs={
        "employer": AttrSpec("employer", "single", True),
        "birth_date": AttrSpec("birth_date", "single", False),
        "nickname": AttrSpec("nickname", "multi", False, competing_values=False),
    }, entities=("alice",))
    s = schema_from_kernel(ks)
    assert all(a.inertia for a in s.attrs)
