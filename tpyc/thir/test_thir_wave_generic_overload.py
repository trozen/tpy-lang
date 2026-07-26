"""Generic free calls whose callee is an @overload GROUP.

The callee still spells the plain name plus explicit template args
(`apply<int32_t>(f1, 5)`); what changes is WHICH stub the template args and
the arg slots come from -- the sema-selected one, exactly as the AST picks
`func_info`. A group whose selected stub is NOT generic carries no inferred
type args at all and rides the plain callee kind (no `<>`).

The callable family (`reduce(add, xs, 0)`, `apply(f1, 5)`, a lambda literal)
renders itself independently of the slot, so the plain free-call rows carry
over unchanged to a substituted `Fn[...]` slot.
"""

from __future__ import annotations

import io

from .emit import emit_thir_body
from .testutil import _lower_ctx, _fn, _assert_byte_identical


def _body(thir, name: str) -> str:
    buf = io.StringIO()
    emit_thir_body(buf, _fn(thir, name))
    return buf.getvalue()


_PICK = (
    "from typing import overload\n"
    "from tpy import Fn, Int32\n"
    "@overload\n"
    "def pick[T, U](g: Fn[[U, T], U], a: list[T], init: U) -> U:\n"
    "    return init\n"
    "@overload\n"
    "def pick[T](g: Fn[[T, T], T], a: list[T]) -> T:\n"
    "    return a[0]\n"
    "def add(a: Int32, b: Int32) -> Int32:\n    return a + b\n"
)


class TestGenericOverloadCallee:
    def test_selected_stub_drives_the_template_args(self):
        # The 2-arg stub has ONE type param, the 3-arg stub two -- picking
        # `fis[0]` would spell the wrong arity.
        src = _PICK + ("def f(xs: list[Int32]) -> Int32:\n"
                       "    return pick(add, xs)\n"
                       "def g(xs: list[Int32]) -> Int32:\n"
                       "    return pick(add, xs, Int32(0))\n")
        thir = _lower_ctx(src)
        assert "pick<int32_t>(add, xs)" in _body(thir, "f")
        assert "pick<int32_t, int32_t>(add, xs, 0)" in _body(thir, "g")
        _assert_byte_identical(src)

    def test_lambda_into_a_substituted_fn_slot(self):
        src = _PICK + ("def f(xs: list[Int32]) -> Int32:\n"
                       "    return pick(lambda a, b: a + b, xs, Int32(0))\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

    def test_callable_value_local_into_a_substituted_fn_slot(self):
        src = ("from typing import overload, Callable\n"
               "from tpy import Fn, Int32\n"
               "@overload\n"
               "def apply[T](g: Fn[[T], T], v: T) -> T:\n"
               "    return g(v)\n"
               "@overload\n"
               "def apply[T](g: Fn[[T, T], T], v: T) -> T:\n"
               "    return g(v, v)\n"
               "def f() -> None:\n"
               "    f1: Callable[[Int32], Int32] = lambda x: x + Int32(1)\n"
               "    print(apply(f1, Int32(5)))\n")
        assert "apply<int32_t>(f1, 5)" in _body(_lower_ctx(src), "f")
        _assert_byte_identical(src)

    def test_group_with_a_non_generic_sibling_keeps_the_plain_name(self):
        # A generic stub next to a concrete one: the group never mangles the
        # callee, so both call sites spell `gen_ov<...>` off the plain name.
        src = ("from typing import overload\n"
               "from tpy import Int32\n"
               "@overload\n"
               "def gen_ov[T](x: T) -> str:\n    return \"generic\"\n"
               "@overload\n"
               "def gen_ov(x: bool) -> str:\n    return \"bool\"\n"
               "def f() -> str:\n"
               "    return gen_ov(True)\n"
               "def g(n: Int32) -> str:\n"
               "    return gen_ov(n)\n")
        thir = _lower_ctx(src)
        assert "gen_ov<bool>(true)" in _body(thir, "f")
        assert "gen_ov<int32_t>(n)" in _body(thir, "g")
        _assert_byte_identical(src)

    def test_container_param_lambda_still_defers(self):
        # The lambda gate admits only scalar/char/enum/str/bytes/F1-record
        # param families -- a container param is a separate rung, and the
        # generic tail must not widen past it.
        src = ("from typing import overload\n"
               "from tpy import Fn, Int32\n"
               "@overload\n"
               "def fold[T, U](g: Fn[[U, T], U], a: list[T], init: U) -> U:\n"
               "    return init\n"
               "@overload\n"
               "def fold[T](g: Fn[[T, T], T], a: list[T]) -> T:\n"
               "    return a[0]\n"
               "def f(xs: list[Int32], seed: list[Int32]) -> list[Int32]:\n"
               "    return fold(lambda acc, x: acc + [x], xs, seed)\n")
        assert _fn(_lower_ctx(src), "f") is None
