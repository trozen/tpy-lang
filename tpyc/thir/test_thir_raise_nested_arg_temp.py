"""A temp under a raise's ctor argument keeps the flush right.

`raise X(args)` flushes its argument temps before the throw line, so the
validator must grant the right both to a temp that IS the ctor arg and to one
nested inside a call in that arg list; the latter used to escape as a
validation failure on valid code.

The right is granted, not universal: a temp that would DEFER (a ternary arm)
has no conditional-region render on this path and still rejects at lowering,
and a temp reaching a position with no flush point at all is a lowering bug the
validator must still fail on.
"""
from __future__ import annotations

import pytest

from ..compilation_context import activate_compiler
from .lower import lower_module
from .nodes import (
    Form, THIRArgTemp, THIRAssign, THIRCall, THIRFunction, THIRFunctionLayout,
    THIRName, THIRRaise,
)
from .testutil import (
    _assert_rejects_at, _assert_routes_byte_identical, _compile, _entry, _fn,
    _thir_ctx,
)
from .validate import THIRValidationError, validate_function

NESTED = '''
class Tag:
    def __init__(self, n: int) -> None:
        self.n = n

def describe(t: Tag) -> str:
    return "tag" + str(t.n)

def f() -> None:
    raise ValueError(describe(Tag(7)))
'''

# The ctor's OWN arg is the temp. A record rvalue binds a reference-typed
# ctor slot directly, so the one argument kind that materializes a temp at
# the arg position itself is an OWNED string slot fed a `String` local:
# `std::string __tmp_1{a};` right before the throw.
DIRECT = '''
from tpy import Own

class MyErr(Exception):
    msg: str
    def __init__(self, msg: Own[str]) -> None:
        self.msg = msg

def f() -> None:
    a = "x" + "y"
    raise MyErr(a)
'''

DEFERRING = '''
class Tag:
    def __init__(self, n: int) -> None:
        self.n = n

def describe(t: Tag) -> str:
    return "tag" + str(t.n)

def f(c: bool) -> None:
    raise ValueError(describe(Tag(7)) if c else "z")
'''


def test_temp_under_a_call_in_the_raise_arg_routes():
    _assert_routes_byte_identical(NESTED)


def test_temp_as_the_direct_raise_arg_routes():
    hpp, cpp = _assert_routes_byte_identical(DIRECT)
    both = hpp + cpp
    assert "std::string __tmp_1{a};" in both
    assert "throw MyErr(std::move(__tmp_1));" in both


def test_direct_raise_arg_is_lowered_as_a_temp():
    # The routing pin above cannot tell a temp from a plain coerce; assert the
    # node so the fixture cannot quietly stop covering the arg position.
    compiler, modules = _compile(DIRECT)
    entry = _entry(modules)
    with activate_compiler(compiler):
        thir = lower_module(entry.ast, entry.analyzer)
    raised = [s for s in _fn(thir, "f").body if isinstance(s, THIRRaise)]
    assert len(raised) == 1
    assert isinstance(raised[0].args[0], THIRArgTemp)


def test_deferring_temp_under_the_raise_arg_still_rejects():
    # BOUNDARY: a ternary arm evaluates lazily, so the temp would have to
    # defer into a conditional region this path does not render.
    _, fell = _thir_ctx(DEFERRING)
    _assert_rejects_at(fell, "body:stmt.raise", "argtemp.cond_defer")


def test_argtemp_under_a_non_flushable_position_still_fails():
    """A planted node -- lowering has no arm that builds this, which is what
    the rule guards against. The raise relaxation grants the flush right at
    ONE position; a call carrying the same temp under an assign's receiver
    eval (a documented temp-free seam) must still fail."""
    compiler, modules = _compile(NESTED)
    entry = _entry(modules)
    with activate_compiler(compiler):
        thir = lower_module(entry.ast, entry.analyzer)
        fn = _fn(thir, "f")
        raised = next(s for s in fn.body if isinstance(s, THIRRaise))
        call = raised.args[0]
        assert isinstance(call, THIRCall)
        assert isinstance(call.args[0], THIRArgTemp)
        name = THIRName(result_type=call.result_type, name="a",
                        form=Form.STORAGE)
        planted = THIRAssign(target=name, value=name, recv_eval=call)
        with pytest.raises(THIRValidationError, match="non-flushable"):
            validate_function(THIRFunction(
                name="w", params=(), return_type=None, body=(planted,),
                layout=THIRFunctionLayout()))
