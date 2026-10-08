"""A body's analysis facts are found on the FunctionInfo registered from it."""

import pytest

from .parse import TpyFunction
from .typesys import (
    INT32, FunctionInfo, PropertyInfo, RecordInfo, TypeRegistry, body_function_info,
    body_method_info,
)


def _func(name: str, **flags: object) -> TpyFunction:
    func = TpyFunction(name, [], INT32, [])
    for flag, value in flags.items():
        setattr(func, flag, value)
    return func


def test_each_dispatch_variant_finds_its_own() -> None:
    first, second = _func("f"), _func("f")
    infos = [FunctionInfo("f", [], INT32, body=first), FunctionInfo("f", [], INT32, body=second)]
    registry = TypeRegistry()
    registry.register_function_group("f", infos)
    assert body_function_info(registry, first) is infos[0]
    assert body_function_info(registry, second) is infos[1]


def test_a_body_the_group_does_not_hold_has_none() -> None:
    registry = TypeRegistry()
    registry.register_function(FunctionInfo("f", [], INT32, body=_func("f")))
    assert body_function_info(registry, _func("f")) is None
    # A nested def is bound in its enclosing body, never by the module name.
    assert body_function_info(registry, _func("f", is_nested_def=True)) is None


def test_overload_group_keeps_the_positional_pick() -> None:
    stubs = [FunctionInfo("f", [], INT32), FunctionInfo("f", [], INT32)]
    registry = TypeRegistry()
    registry.register_function_group("f", stubs)
    implementation = _func("f")
    assert body_function_info(registry, implementation) is stubs[-1]


def test_two_infos_claiming_one_body_is_an_internal_error() -> None:
    body = _func("f")
    registry = TypeRegistry()
    registry.register_function_group(
        "f", [FunctionInfo("f", [], INT32, body=body), FunctionInfo("f", [], INT32, body=body)])
    with pytest.raises(AssertionError):
        body_function_info(registry, body)


def test_property_accessors_find_their_own() -> None:
    getter = _func("x", is_property_getter=True)
    setter = _func("x", is_property_setter=True, property_name="x")
    get_fi = FunctionInfo("x", [], INT32, is_property_getter=True, body=getter)
    set_fi = FunctionInfo("x", [], INT32, is_property_setter=True, property_name="x", body=setter)
    record = RecordInfo("R", [], properties={
        "x": PropertyInfo("x", accessors=[get_fi, set_fi])})
    assert body_method_info(record, getter) is get_fi
    assert body_method_info(record, setter) is set_fi
