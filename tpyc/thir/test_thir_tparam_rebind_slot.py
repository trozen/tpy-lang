"""Pins for the REBIND-SLOT binding of a BARE open type-param local -- a
local declared `acc: U` and reseated by a later rvalue call.

The plain-copy `T newitem = ...;` row covers the single-assignment shape only.
Once the name is rebound, the same two-slot pointer machinery an F1 record's
rebind takes applies: an open `U` has no borrow form and no storage form of its
own, so the C++ template traits settle the shape at instantiation and the
binding renders `U __slot_1 = <init>; U* acc = &__slot_1;` with the reseats
going through a hoisted `std::optional<U>`.

`functools.reduce` is the shape's live consumer, and its committed render in
`tests/cases/harness/stdlib_render` is the oracle these pins mirror.
"""

from __future__ import annotations

from pathlib import Path

from .testutil import (_assert_byte_identical, _assert_rejects_at,
                       _assert_routes_byte_identical, _thir_ctx)

_MAKE = (
    "from tpy import Own, Int32\n"
    "def make[T](x: Own[T]) -> Own[T]:\n"
    "    return x\n"
)

_REBOUND = _MAKE + (
    "def relay[T](x: Own[T], y: Own[T]) -> Own[T]:\n"
    "    v: T = make(x)\n"
    "    v = make(y)\n"
    "    return v\n"
    "def main() -> None:\n"
    "    print(relay(7, 9))\n"
    "main()\n"
)

# The stdlib oracle: `functools.reduce`'s two bodies, as committed.
_ORACLE = (Path(__file__).resolve().parents[2] / "tests" / "cases"
           / "harness" / "stdlib_render" / "expected" / "include"
           / "functools.hpp")


class TestOpenTypeParamRebindSlot:
    def test_rebound_open_type_param_slot_routes(self):
        hpp, cpp = _assert_routes_byte_identical(_REBOUND, comments=False)
        assert "T* v = &__slot_1;" in hpp + cpp

    def test_render_mirrors_the_stdlib_oracle(self):
        # The three lines the binding owns -- the hoisted reseat slot, the
        # init slot, and the pointer itself -- plus the reseat and the
        # move-out read that consume it.
        oracle = _ORACLE.read_text()
        for line in ("    std::optional<U> __slot_2;",
                     "    U __slot_1 = U(initial);",
                     "    U* acc = &__slot_1;",
                     "        acc = &*(__slot_2 = func((*acc), x));",
                     "    return std::move((*acc));"):
            assert line in oracle, line

        hpp, cpp = _assert_routes_byte_identical(_REBOUND, comments=False)
        out = hpp + cpp
        for line in ("    std::optional<T> __slot_2;",
                     "    T __slot_1 = make<T>(std::move(x));",
                     "    T* v = &__slot_1;",
                     "    v = &*(__slot_2 = make<T>(std::move(y)));",
                     "    return std::move((*v));"):
            assert line in out, line

    def test_method_call_source_keeps_rejecting(self):
        # BOUNDARY: the same rebound open slot fed by a METHOD call. Nothing
        # in the corpus renders it, so the row stays out until one does.
        src = (
            "from tpy import Own, Int32, copy\n"
            "class Cell[T]:\n"
            "    v: T\n"
            "    def __init__(self, v: Own[T]) -> None:\n"
            "        self.v = v\n"
            "    def take(self) -> Own[T]:\n"
            "        return copy(self.v)\n"
            "def relay[T](c: Cell[T], d: Cell[T]) -> Own[T]:\n"
            "    v: T = c.take()\n"
            "    v = d.take()\n"
            "    return v\n"
            "def main() -> None:\n"
            "    print(relay(Cell(1), Cell(2)))\n"
            "main()\n"
        )
        _ctx, fell = _thir_ctx(src)
        _assert_rejects_at(fell, "body:stmt.var_decl", "decl.slot_type")

    def test_ternary_source_keeps_rejecting(self):
        # BOUNDARY: an rvalue TERNARY into the same rebound slot. The arms
        # each carry their own hoists, so the init is a different render.
        src = _MAKE + (
            "def relay[T](x: Own[T], y: Own[T], c: bool) -> Own[T]:\n"
            "    v: T = make(x) if c else make(y)\n"
            "    v = make(y)\n"
            "    return v\n"
            "def main() -> None:\n"
            "    print(relay(7, 9, True))\n"
            "main()\n"
        )
        _ctx, fell = _thir_ctx(src)
        _assert_rejects_at(fell, "body:stmt.var_decl", "decl.slot_type")

    def test_single_assignment_slot_keeps_the_plain_copy(self):
        # The arm must not steal the un-rebound neighbour: without a reseat
        # there is no slot and no pointer, just the spelled copy.
        src = _MAKE + (
            "def hold[T](x: Own[T]) -> Own[T]:\n"
            "    v: T = make(x)\n"
            "    return v\n"
            "def main() -> None:\n"
            "    print(hold(7))\n"
            "main()\n"
        )
        hpp, cpp = _assert_byte_identical(src, comments=False)
        out = hpp + cpp
        assert "T v = make<T>(std::move(x));" in out
        assert "__slot_1" not in out
