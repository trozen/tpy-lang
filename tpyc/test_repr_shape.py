"""`repr_shape` and its views answer the same for every wrapper order."""

from itertools import permutations

import pytest

from .typesys import (
    INT32, NominalType, OptionalType, OwnType, ReadonlyType, RefType, SendType,
    UnionType, pointer_repr_optional, pointer_variant_union, readonly_access,
    repr_shape,
)

OPT = OptionalType(NominalType("R"))
UNI = UnionType((NominalType("A"), NominalType("B")))
_WRAPPERS = {"ref": RefType, "readonly": ReadonlyType, "own": OwnType,
             "send": SendType}


def _wrap(shape, order):
    t = shape
    for name in order:
        t = _WRAPPERS[name](t)
    return t


def _orders(names):
    return [order for n in range(len(names) + 1)
            for order in permutations(names, n)]


@pytest.mark.parametrize("shape", [OPT, UNI, INT32])
@pytest.mark.parametrize("order", _orders(["ref", "readonly", "own", "send"]))
def test_shape_under_any_wrapper_order(shape, order):
    t = _wrap(shape, order)
    assert repr_shape(t) == shape
    assert readonly_access(t) == ("readonly" in order)


@pytest.mark.parametrize("order", _orders(["ref", "readonly", "send"]))
def test_views_under_any_access_wrapper_order(order):
    assert pointer_repr_optional(_wrap(OPT, order)) == OPT
    assert pointer_variant_union(_wrap(UNI, order)) == UNI
    assert pointer_repr_optional(_wrap(UNI, order)) is None
    assert pointer_variant_union(_wrap(OPT, order)) is None


@pytest.mark.parametrize("order", [o for o in _orders(["ref", "readonly", "own"])
                                   if "own" in o])
def test_owned_is_the_storage_form_in_any_order(order):
    # `Own[T | None]` / `Own[A | B]` is `std::optional<T>` / the value
    # variant, wherever the Own sits among the wrappers.
    assert pointer_repr_optional(_wrap(OPT, order)) is None
    assert pointer_variant_union(_wrap(UNI, order)) is None


def test_value_repr_optional_is_not_pointer_repr():
    assert pointer_repr_optional(ReadonlyType(OptionalType(INT32))) is None
    assert pointer_repr_optional(None) is None
