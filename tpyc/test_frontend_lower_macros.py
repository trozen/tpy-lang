"""Lowering routes frontend-IR `Decorator` nodes via the compiler-owned
decorator registry: `BUILTIN_LOWERING` (e.g. `tpy.native`) becomes
linkage / native fields on the lowered `TpyFunction` / `TpyRecord`, and
`MACRO` becomes `pending_macros` for the sema macro phase. Unknown names
are rejected -- no silent passthrough. Also covers the module-scoped
`macro_data` channel a macro-hosting plugin uses for non-`Expr` payloads.
"""
import pytest

from tpyc.frontend_ir.decorators import DecoratorEntry, DecoratorRoute
from tpyc.frontend_ir.lower import lower_module
from tpyc.frontend_ir.nodes import (
    BoolLit, Decorator, FrontendModule, Function, IntLit, Name, NamedType,
    NoneLit, Param, Record, StrLit)
from tpyc.parse.nodes import FunctionLinkage, RecordLinkage

# A MACRO decorator a plugin contributes via its decorator_manifest.
DEDUCE = DecoratorEntry("mymacros.deduce", DecoratorRoute.MACRO, ("function",))


def _lower(fn=None, records=(), manifest=(), macro_data=None):
    fm = FrontendModule(
        qname="testmod",
        functions=(fn,) if fn is not None else (),
        records=tuple(records),
        macro_data=macro_data)
    return lower_module(fm, "testplugin", decorator_manifest=manifest)


def _native(symbol=None, **kw):
    args = (StrLit(value=symbol),) if symbol is not None else ()
    kwargs = tuple((k, v) for k, v in kw.items())
    return Decorator(name="tpy.native", args=args, kwargs=kwargs)


# --- MACRO route ---------------------------------------------------------

def test_macro_decorator_lowers_to_pending_macro():
    res = _lower(Function(name="f", decorators=(
        Decorator(name="mymacros.deduce"),)), manifest=(DEDUCE,))
    assert res.module is not None, res.diagnostics
    assert res.module.functions[0].pending_macros == [("mymacros.deduce", {})]


def test_macro_literal_kwargs_preserved():
    dec = Decorator(name="mymacros.deduce",
                    kwargs=(("debug", BoolLit(value=True)),))
    res = _lower(Function(name="f", decorators=(dec,)), manifest=(DEDUCE,))
    assert res.module is not None, res.diagnostics
    assert res.module.functions[0].pending_macros == [
        ("mymacros.deduce", {"debug": True})]


def test_no_decorators_means_no_pending_macros():
    res = _lower(Function(name="f"))
    assert res.module is not None
    assert res.module.functions[0].pending_macros == []


def test_macro_nonliteral_kwarg_is_rejected():
    # Arbitrary (non-Expr) data must ride macro_data, not decorator kwargs.
    dec = Decorator(name="mymacros.deduce",
                    kwargs=(("table", Name(ident="x")),))
    res = _lower(Function(name="f", decorators=(dec,)), manifest=(DEDUCE,))
    assert res.module is None
    assert any("must be a literal" in d.diagnostic.message
               for d in res.diagnostics)


def test_macro_decorator_on_method_is_rejected():
    method = Function(name="m", params=(Param(name="self", type=None),),
                      decorators=(Decorator(name="mymacros.deduce"),))
    res = _lower(records=(Record(name="R", methods=(method,)),),
                 manifest=(DEDUCE,))
    assert res.module is None
    assert any("not supported on methods" in d.diagnostic.message
               for d in res.diagnostics)


# --- registry errors -----------------------------------------------------

def test_unknown_decorator_is_rejected():
    res = _lower(Function(name="f", decorators=(Decorator(name="nope.thing"),)))
    assert res.module is None
    assert any("unknown decorator" in d.diagnostic.message
               for d in res.diagnostics)


def test_non_decorator_entry_is_rejected():
    res = _lower(Function(name="f", decorators=("not_a_decorator",)))
    assert res.module is None
    assert any("must be a frontend_ir Decorator" in d.diagnostic.message
               for d in res.diagnostics)


def test_manifest_route_conflict_is_rejected():
    # Re-registering a core name with a different route is a load error.
    bad = DecoratorEntry("tpy.native", DecoratorRoute.MACRO, ("function",))
    res = _lower(Function(name="f"), manifest=(bad,))
    assert res.module is None
    assert any("conflicting" in d.diagnostic.message for d in res.diagnostics)


def test_decorator_on_wrong_target_kind_is_rejected():
    # A function-only macro placed on a record.
    res = _lower(records=(Record(name="R", decorators=(
        Decorator(name="mymacros.deduce"),)),), manifest=(DEDUCE,))
    assert res.module is None
    assert any("not valid on a record" in d.diagnostic.message
               for d in res.diagnostics)


# --- BUILTIN_LOWERING: tpy.native ----------------------------------------

def test_native_on_free_function():
    res = _lower(Function(name="sqrt_of", decorators=(_native("std::sqrt"),)))
    assert res.module is not None, res.diagnostics
    fn = res.module.functions[0]
    assert fn.linkage == FunctionLinkage.NATIVE
    assert fn.native_name == "std::sqrt"
    assert fn.is_stub


def test_bare_native_leaves_symbol_for_sema():
    res = _lower(Function(name="f", decorators=(_native(),)))
    assert res.module is not None, res.diagnostics
    fn = res.module.functions[0]
    assert fn.linkage == FunctionLinkage.NATIVE
    assert fn.native_name is None
    assert fn.is_stub


def test_native_on_method_is_allowed():
    # Unlike a macro decorator, @native IS valid on a method (it becomes
    # method linkage, not a function macro).
    method = Function(
        name="begin_object", params=(Param(name="self", type=None),),
        decorators=(_native("beginObject"),))
    res = _lower(records=(Record(name="Writer", methods=(method,)),))
    assert res.module is not None, res.diagnostics
    m = res.module.records[0].methods[0]
    assert m.linkage == FunctionLinkage.NATIVE
    assert m.native_name == "beginObject"


def test_native_function_kwarg_on_method():
    method = Function(
        name="m", params=(Param(name="self", type=None),),
        decorators=(_native("cpp_m", function=BoolLit(value=True)),))
    res = _lower(records=(Record(name="R", methods=(method,)),))
    assert res.module is not None, res.diagnostics
    assert res.module.records[0].methods[0].native_function is True


def test_native_on_record():
    res = _lower(records=(Record(name="Logger", decorators=(
        _native("log::Logger"),)),))
    assert res.module is not None, res.diagnostics
    rec = res.module.records[0]
    assert rec.linkage == RecordLinkage.NATIVE
    assert rec.native_name == "log::Logger"


def test_native_symbol_must_be_string():
    res = _lower(Function(name="f", decorators=(
        Decorator(name="tpy.native", args=(IntLit(value=5),)),)))
    assert res.module is None
    assert any("symbol must be a string" in d.diagnostic.message
               for d in res.diagnostics)


def test_native_unsupported_kwarg_is_rejected():
    res = _lower(Function(name="f", decorators=(
        _native("f", binding=StrLit(value="C")),)))
    assert res.module is None
    assert any("unsupported keyword" in d.diagnostic.message
               for d in res.diagnostics)


def test_native_function_kwarg_on_record_is_rejected():
    res = _lower(records=(Record(name="R", decorators=(
        _native("R", function=BoolLit(value=True)),)),))
    assert res.module is None
    assert any("not valid on a record" in d.diagnostic.message
               for d in res.diagnostics)


def test_duplicate_native_is_rejected():
    res = _lower(Function(name="f", decorators=(_native("a"), _native("b"))))
    assert res.module is None
    assert any("duplicate @native" in d.diagnostic.message
               for d in res.diagnostics)


# --- macro_data channel --------------------------------------------------

def test_macro_data_threads_onto_module():
    payload = {"queries": {"is$%": []}, "module": "Algo"}
    res = _lower(Function(name="f"), macro_data=payload)
    assert res.module is not None
    assert res.module.macro_data is payload


def test_macro_data_defaults_to_none():
    res = _lower(Function(name="f"))
    assert res.module is not None
    assert res.module.macro_data is None
