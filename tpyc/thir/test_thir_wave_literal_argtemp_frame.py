"""Pins for the frame-capturing container-literal ArgTemp rows: the plain
generator-factory instantiation arm threads `allow_temps` (the factory's
own literal args hoist their `__tmp_N` at the statement), a readonly
slot's literal hoists for a frame-capturing callee (the inline
`const T&` bind would dangle), and the empty readonly rvalue's INLINE arm
declines frame-capturing callees (guard proven load-bearing by dualgen:
without it the inline render shifts the statement's temp numbering).
Boundary: a non-empty literal at a PLAIN callee's readonly slot keeps the
AST's inline render and stays rejected."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _assert_byte_identical,
    _assert_routes_byte_identical,
    _fn,
    _lower_ctx_witnessed,
)

_GEN = (
    "from tpy import Int32, readonly\n"
    "from typing import Iterator\n"
    "def drain_list(xs: list[Int32]) -> Iterator[Int32]:\n"
    "    while xs:\n"
    "        yield xs.pop(0)\n"
    "def tail(xs: readonly[list[Int32]]) -> Iterator[Int32]:\n"
    "    for i in range(1, len(xs)):\n"
    "        yield xs[i]\n"
)


class TestFrameLiteralArgTemp:
    def test_gen_factory_literal_args_hoist(self):
        # Empty and non-empty literals at the mutable slot of a generator
        # factory nested in a container instantiation: the inner arg's
        # `__tmp_N` flushes at the statement.
        src = _GEN + (
            "def main() -> None:\n"
            "    print(list(drain_list([])))\n"
            "    print(list(drain_list([7, 8])))\n"
        )
        _assert_routes_byte_identical(src)

    def test_readonly_frame_literal_hoists(self):
        # readonly slot + generator callee: the literal hoists like a
        # mutable one (non-empty and EMPTY -- the empty flavor must skip
        # the inline readonly-rvalue arm or the temp numbering diverges).
        src = _GEN + (
            "def main() -> None:\n"
            "    print(sum(tail([1, 2])))\n"
            "    print(sum(tail([])))\n"
        )
        _assert_routes_byte_identical(src)


class TestFrameLiteralBoundaries:
    def test_readonly_dict_literal_at_frame_callee_still_defers(self):
        # The gate's frame row is ArrayLiteral-ONLY: the dict/set render
        # arm is not frame-aware, so admitting a readonly dict literal
        # here would fall through to the inline bind the frame borrows
        # past (dualgen-verified dangling + divergence). Must keep
        # rejecting until the dict/set arm learns the hoist.
        src = (
            "from tpy import Int32, readonly\n"
            "from typing import Iterator\n"
            "def gen_vals(m: readonly[dict[str, Int32]])"
            " -> Iterator[Int32]:\n"
            "    for k in m:\n"
            "        yield m[k]\n"
            "def main() -> None:\n"
            "    print(sum(gen_vals({'a': 1, 'b': 2})))\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.expr_stmt:call.arg_shape.container")

    def test_plain_readonly_nonempty_literal_still_defers(self):
        # A PLAIN callee's readonly slot binds the literal INLINE on the
        # AST path (`take_ro({1, 2, 3})`) -- the temp rows must not take
        # it; identity holds via fallback.
        src = (
            "from tpy import Int32, readonly\n"
            "def take_ro(xs: readonly[list[Int32]]) -> Int32:\n"
            "    return len(xs)\n"
            "def main() -> None:\n"
            "    print(take_ro([1, 2, 3]))\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.expr_stmt:call.arg_shape.container")
