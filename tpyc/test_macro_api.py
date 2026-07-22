"""Unit tests for the @function_macro context API surface that needs no
running compiler -- chiefly FunctionMacroContext.resolve_type, which maps a
builtin type name to a TypeInfo without touching the SemanticContext, and
the method-context properties (is_method / self_type).
"""
import pytest

from tpyc.diagnostics import SemanticError
from tpyc.macro_api import FunctionMacroContext, PostSemaFunctionMacroContext
from tpyc.parse.nodes import TpyFunction, TpyRecord


def _ctx() -> FunctionMacroContext:
    # resolve_type reads neither ctx nor func, so a skeleton func and a None
    # SemanticContext are sufficient to exercise it.
    fn = TpyFunction(name="f", params=[], return_type=None, body=[])
    return FunctionMacroContext(None, fn, "testmod")


def _method_ctx(is_staticmethod: bool = False,
                record: bool = True) -> FunctionMacroContext:
    fn = TpyFunction(name="m", params=[], return_type=None, body=[],
                     is_method=record, is_staticmethod=is_staticmethod)
    rec = TpyRecord(name="R", fields=[]) if record else None
    return FunctionMacroContext(None, fn, "testmod", record=rec)


# --- method-context properties (is_method / self_type) --------------------

def test_free_function_has_no_method_context():
    ctx = _ctx()
    assert ctx.is_method is False
    assert ctx.self_type is None


def test_method_context_exposes_self_type():
    # Without a SemanticContext the property takes the bare-NominalType
    # fallback; the name is what matters for the API contract.
    ctx = _method_ctx()
    assert ctx.is_method is True
    assert ctx.self_type is not None
    assert ctx.self_type.name == "R"


def test_staticmethod_has_no_self_type():
    ctx = _method_ctx(is_staticmethod=True)
    assert ctx.is_method is True
    assert ctx.self_type is None


def test_note_param_mutated_rejects_method_host():
    # note_param_mutated resolves the host via get_function (module-level
    # functions only), so it must reject a method host rather than mis-target
    # a same-named free function; method-aware support is a parked follow-up.
    fn = TpyFunction(name="m", params=[("c", None)], return_type=None, body=[],
                     is_method=True, is_staticmethod=False)
    rec = TpyRecord(name="R", fields=[])
    ctx = PostSemaFunctionMacroContext(None, fn, "testmod", record=rec)
    with pytest.raises(SemanticError, match="not supported on method hosts"):
        ctx.note_param_mutated(0)


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


def test_from_tpy_type_int_type_arg_passthrough():
    # An `N: int` generic binding is a plain int in NominalType.type_args (the
    # compiler-wide `TpyType | int` convention); TypeInfo.from_tpy_type must
    # pass it through as-is instead of recursing into it (it used to crash
    # "'int' object has no attribute 'is_value_type'"). Positions must stay
    # aligned with the declared params.
    from tpyc.macro_api import TypeInfo
    from tpyc.typesys import INT32, NominalType

    box = NominalType("Box", (INT32, 8), _module_qname="testmod.Box")
    ti = TypeInfo.from_tpy_type(box)
    assert len(ti.type_args) == 2
    assert ti.type_args[0].name == "Int32"
    assert ti.type_args[1] == 8


def test_from_tpy_type_nested_int_type_arg():
    # The recursion re-enters the fixed branch: an int arg INSIDE a nested
    # generic type arg (Box[Inner[Int32, 4], 8]) converts the same way.
    from tpyc.macro_api import TypeInfo
    from tpyc.typesys import INT32, NominalType

    inner = NominalType("Inner", (INT32, 4), _module_qname="testmod.Inner")
    box = NominalType("Box", (inner, 8), _module_qname="testmod.Box")
    ti = TypeInfo.from_tpy_type(box)
    assert ti.type_args[1] == 8
    nested = ti.type_args[0]
    assert nested.name.startswith("Inner")
    assert nested.type_args[0].name == "Int32"
    assert nested.type_args[1] == 4
