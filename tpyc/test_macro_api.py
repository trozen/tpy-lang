"""Unit tests for the @function_macro context API surface that needs no
running compiler -- chiefly FunctionMacroContext.resolve_type, which maps a
builtin type name to a TypeInfo without touching the SemanticContext.
"""
import pytest

from tpyc.macro_api import FunctionMacroContext
from tpyc.parse.nodes import TpyFunction


def _ctx() -> FunctionMacroContext:
    # resolve_type reads neither ctx nor func, so a skeleton func and a None
    # SemanticContext are sufficient to exercise it.
    fn = TpyFunction(name="f", params=[], return_type=None, body=[])
    return FunctionMacroContext(None, fn, "testmod")


@pytest.mark.parametrize("name", [
    "bool", "int", "str", "String", "StrView", "Char",
    "Int32", "UInt64", "float", "Float32", "Float64",
    "bytes", "bytearray", "BytesView", "basic_slice", "slice"])
def test_resolve_type_known_names(name):
    ti = _ctx().resolve_type(name)
    assert ti is not None


def test_resolve_type_none_is_not_mintable():
    # `None` resolves to VOID in the shared resolver, but void is not a
    # valid local type, so resolve_type rejects it.
    assert _ctx().resolve_type("None") is None


def test_resolve_type_int32_roundtrips_name():
    assert _ctx().resolve_type("Int32").name == "Int32"


def test_resolve_type_bool_is_bool():
    assert _ctx().resolve_type("bool").name == "bool"


def test_resolve_type_float64_aliases_float():
    # Float64 is TPy `float`; it resolves to a real type but round-trips its
    # name as "float" since there is no distinct FLOAT64 singleton.
    ti = _ctx().resolve_type("Float64")
    assert ti is not None and ti.is_float


@pytest.mark.parametrize("name", ["UnknownThing", "", "list", "MyRecord"])
def test_resolve_type_unknown_returns_none(name):
    assert _ctx().resolve_type(name) is None
