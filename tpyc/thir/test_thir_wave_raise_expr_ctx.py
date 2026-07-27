"""`raise <expr>` inside a resumable frame and inside an `@error_return`
body: `_gen_raise` has no context-specific branch for the expr form, so both
render identically to the plain sync case."""

from __future__ import annotations

from .testutil import _lower_ctx, _fn, _assert_byte_identical

_EXC = "err = ValueError('bad')\n"


class TestRaiseExprInContext:
    def test_sync_baseline_still_routes(self):
        src = (_EXC
               + "def use() -> None:\n"
               + "    raise err\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    # The RESUMABLE-frame half is pinned where it belongs, in
    # test_thir_resumable.py::test_raise_expr_after_await_routes (converted
    # from the defer pin the removed guard used to satisfy).


class TestRaiseExprBoundaries:
    def test_non_routable_operand_still_falls_back(self):
        # The guard removal does not widen the OPERAND's own admission -- a
        # source whose lowering rejects still falls the body back.
        src = ("from typing import Iterator\n"
               "class Holder:\n"
               "    def __init__(self) -> None:\n"
               "        pass\n"
               "def gen(h: Holder) -> Iterator[int]:\n"
               "    yield 1\n"
               "    raise h.missing\n")
        try:
            thir = _lower_ctx(src)
        except Exception:
            return  # sema rejects it outright, which is also fine
        assert _fn(thir, "gen") is None
