"""`Callable | None` param consumers: the has_value None-test and the
`.value()` invocation unwrap (`f(x)` -> `f.value()(x)`, keyed on the
DECLARED Optional[Callable] type like the AST's _gen_call check). Plain
Callable bindings keep the bare invocation."""

from __future__ import annotations

from .testutil import (
    _lower_ctx_witnessed, _fn, _assert_routes_byte_identical,
)

_HDR = (
    "from typing import Callable\n"
    "from tpy import Int32\n"
)


class TestOptionalCallableParam:
    def test_none_test_and_unwrapped_invoke_route(self):
        src = (_HDR +
               "def maybe_apply(f: Callable[[Int32], Int32] | None,"
               " x: Int32) -> Int32:\n"
               "    if f is not None:\n"
               "        return f(x)\n"
               "    return x\n"
               "def main() -> None:\n"
               "    print(maybe_apply(None, 3))\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "maybe_apply") is not None
        assert faces.get("call.opt_callable_unwrap", 0) == 1
        cpp = _assert_routes_byte_identical(src)
        assert "if ((f.has_value()))" in cpp[1]
        assert "return f.value()(x);" in cpp[1]

    def test_plain_callable_invoke_stays_bare(self):
        # The inverse guard: a plain Callable binding never unwraps.
        src = (_HDR +
               "def apply(f: Callable[[Int32], Int32], x: Int32) -> Int32:\n"
               "    return f(x)\n"
               "def main() -> None:\n"
               "    print(apply(lambda x: x * 2, 4))\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "apply") is not None
        assert faces.get("call.opt_callable_unwrap", 0) == 0
        cpp = _assert_routes_byte_identical(src)
        assert "return f(x);" in cpp[1]
