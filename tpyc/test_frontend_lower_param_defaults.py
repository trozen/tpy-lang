"""Lowering threads a frontend-IR `Param.default` into `TpyFunction.defaults`
(the same field source defaults use), so a plugin can emit parameters with
default values -- and rejects a non-defaulted param following a defaulted one,
since C++ default arguments must be trailing.
"""

from tpyc.frontend_ir.lower import lower_module
from tpyc.frontend_ir.nodes import (
    Field, FrontendModule, Function, IntLit, NamedType, Param, Record)
from tpyc.parse.nodes import TpyIntLiteral


def _lower_fn(fn: Function):
    return lower_module(
        FrontendModule(qname="testmod", functions=(fn,)), "testplugin")


def test_param_default_lowers_to_defaults_list():
    # `f(a: int32 = 123, b: int32 = 0)` -> both defaults carried, in order.
    fn = Function(name="f", params=(
        Param(name="a", type=NamedType(name="int32"), default=IntLit(value=123)),
        Param(name="b", type=NamedType(name="int32"), default=IntLit(value=0)),
    ))
    res = _lower_fn(fn)
    assert res.module is not None, res.diagnostics
    defaults = res.module.functions[0].defaults
    assert len(defaults) == 2
    assert isinstance(defaults[0], TpyIntLiteral) and defaults[0].value == 123
    assert isinstance(defaults[1], TpyIntLiteral) and defaults[1].value == 0


def test_partial_trailing_defaults():
    # A required param may precede a defaulted one; the leading slot is None.
    fn = Function(name="f", params=(
        Param(name="a", type=NamedType(name="int32")),
        Param(name="b", type=NamedType(name="int32"), default=IntLit(value=7)),
    ))
    res = _lower_fn(fn)
    assert res.module is not None, res.diagnostics
    defaults = res.module.functions[0].defaults
    assert defaults[0] is None
    assert isinstance(defaults[1], TpyIntLiteral) and defaults[1].value == 7


def test_no_defaults_leaves_list_empty():
    # No param has a default -> the fast path (empty defaults list) is kept.
    fn = Function(name="f", params=(
        Param(name="a", type=NamedType(name="int32")),))
    res = _lower_fn(fn)
    assert res.module is not None, res.diagnostics
    assert res.module.functions[0].defaults == []


def test_required_after_default_rejected():
    # `f(a=1, b)` -- non-default following a default is rejected with a clear
    # diagnostic rather than an opaque downstream C++ error.
    fn = Function(name="f", params=(
        Param(name="a", type=NamedType(name="int32"), default=IntLit(value=1)),
        Param(name="b", type=NamedType(name="int32")),
    ))
    res = _lower_fn(fn)
    assert res.module is None
    assert any("without a default follows" in d.diagnostic.message
               for d in res.diagnostics), res.diagnostics


def test_method_param_default_lowers():
    # Methods route through the same `_lower_function`, and `self` is stripped
    # from BOTH params and defaults in lockstep, so the remaining param keeps
    # its own default (a self/defaults misalignment would make `factor` read
    # as required).
    method = Function(name="scale", is_method=True, params=(
        Param(name="self"),
        Param(name="factor", type=NamedType(name="int32"), default=IntLit(value=2)),
    ))
    rec = Record(name="Box",
                 fields=(Field(name="v", type=NamedType(name="int32")),),
                 methods=(method,))
    res = lower_module(
        FrontendModule(qname="testmod", records=(rec,)), "testplugin")
    assert res.module is not None, res.diagnostics
    m = res.module.records[0].methods[0]
    assert [p[0] for p in m.params] == ["factor"]  # self stripped
    assert len(m.defaults) == 1
    assert isinstance(m.defaults[0], TpyIntLiteral) and m.defaults[0].value == 2
