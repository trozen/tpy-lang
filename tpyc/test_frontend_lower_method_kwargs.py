"""Lowering threads a frontend-IR `Call`'s `kwargs` into the `TpyMethodCall`
it produces for an attribute (method) callee, so a plugin-emitted
`obj.method(key=value)` keeps its keyword args -- the same as the
free-function path.
"""

from tpyc.frontend_ir.lower import lower_module
from tpyc.frontend_ir.nodes import (
    Attr, Call, ExprStmt, FrontendModule, Function, IntLit, Name)
from tpyc.parse.nodes import TpyExprStmt, TpyIntLiteral, TpyMethodCall


def _lower_call(call: Call):
    fn = Function(name="f", body=(ExprStmt(value=call),))
    res = lower_module(FrontendModule(qname="testmod", functions=(fn,)),
                       "testplugin")
    assert res.module is not None, res.diagnostics
    stmt = res.module.functions[0].body[0]
    assert isinstance(stmt, TpyExprStmt)
    return stmt.expr


def test_method_call_kwargs_thread_through():
    call = Call(
        callee=Attr(target=Name(ident="obj"), ident="Resize"),
        kwargs=(("Width", IntLit(value=5)),))
    mcall = _lower_call(call)
    assert isinstance(mcall, TpyMethodCall)
    assert mcall.method == "Resize"
    assert set(mcall.kwargs) == {"Width"}
    val = mcall.kwargs["Width"]
    assert isinstance(val, TpyIntLiteral)
    assert val.value == 5


def test_method_call_mixed_args_and_kwargs():
    call = Call(
        callee=Attr(target=Name(ident="g"), ident="Set_cell"),
        args=(IntLit(value=3),),
        kwargs=(("Step", IntLit(value=4)),))
    mcall = _lower_call(call)
    assert isinstance(mcall, TpyMethodCall)
    assert len(mcall.args) == 1
    assert set(mcall.kwargs) == {"Step"}
    val = mcall.kwargs["Step"]
    assert isinstance(val, TpyIntLiteral)
    assert val.value == 4


def test_method_call_without_kwargs_is_empty():
    call = Call(callee=Attr(target=Name(ident="obj"), ident="Bump"))
    mcall = _lower_call(call)
    assert isinstance(mcall, TpyMethodCall)
    assert mcall.kwargs == {}
