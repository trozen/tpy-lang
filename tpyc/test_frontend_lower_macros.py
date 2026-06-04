"""Lowering threads frontend-IR `Function.decorators` into
`TpyFunction.pending_macros`, so a plugin can apply a @function_macro to an
emitted (lowered) function -- the parser-side decorator path never runs for
plugin output.
"""
import pytest

from tpyc.frontend_ir.lower import lower_module
from tpyc.frontend_ir.nodes import (
    FrontendModule, Function, Param, Record)


def _lower_one(fn: Function):
    fm = FrontendModule(qname="testmod", functions=(fn,))
    return lower_module(fm, "testplugin")


def test_decorator_lowers_to_pending_macro():
    res = _lower_one(Function(
        name="f", decorators=(("mymacros", "deduce", {}),)))
    assert res.module is not None
    assert res.module.functions[0].pending_macros == [
        ("mymacros.deduce", {})]


def test_decorator_kwargs_preserved():
    res = _lower_one(Function(name="f", decorators=(("m", "fn", {"debug": True}),)))
    assert res.module is not None
    assert res.module.functions[0].pending_macros == [("m.fn", {"debug": True})]


def test_no_decorators_means_no_pending_macros():
    res = _lower_one(Function(name="f"))
    assert res.module is not None
    assert res.module.functions[0].pending_macros == []


@pytest.mark.parametrize("dec", [
    ("m", "fn"),                 # len 2
    ("m", "fn", {}, "extra"),    # len 4
    "not_a_tuple",               # non-tuple
    (1, "fn", {}),               # non-string module
    ("m", 2, {}),                # non-string name
    ("m", "fn", "kwargs"),       # non-dict kwargs
])
def test_malformed_decorator_is_rejected(dec):
    res = _lower_one(Function(name="f", decorators=(dec,)))
    assert res.module is None
    assert any("decorator must be" in d.diagnostic.message
               for d in res.diagnostics)


def test_decorator_on_method_is_rejected():
    # The function-macro phase only scans module-level functions, so a
    # decorator on a method would be silently dropped -- lowering must
    # reject it loudly instead.
    method = Function(
        name="m", params=(Param(name="self", type=None),),
        decorators=(("m", "fn", {}),))
    fm = FrontendModule(
        qname="testmod", records=(Record(name="R", methods=(method,)),))
    res = lower_module(fm, "testplugin")
    assert res.module is None
    assert any("not supported on methods" in d.diagnostic.message
               for d in res.diagnostics)
