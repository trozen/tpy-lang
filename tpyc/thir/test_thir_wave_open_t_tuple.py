"""The open-T tuple family across a generic-protocol boundary: a
`tuple[T, Int32]` produced inside a generic body, declared into a local,
and passed back into a protocol method.

Five coupled rows share one render family (`val_or_ptr_t<T>` elements,
decided at instantiation): the generic tuple literal's `self.<field>`
element source, the open-T tuple arg slot, the protocol-method
tuple-literal arg admission, the protocol-method open-T tuple RESULT, and
the open-T tuple decl slot.

NOTE the decl slot spells the BARE `std::tuple<T, int32_t>` while the
producer returns `std::tuple<val_or_cptr_t<T>, int32_t>`. The two coincide
only for a value `T`; sema blocks the non-value instantiation (a record
arg fails protocol conformance), so the mirror is exact and reaches no
further than the AST does.
"""

from __future__ import annotations

from .testutil import (
    _reject_tally,
    _assert_byte_identical,
    _assert_rejects_at,
    _assert_routes_byte_identical,
    _lower_ctx_witnessed,
    _compile,
    _entry,
)

_PAIR = (
    "from typing import Protocol\n"
    "from tpy import Int32\n"
    "class HasPair[T](Protocol):\n"
    "    def pair(self) -> tuple[T, Int32]: ...\n"
    "    def consume(self, p: tuple[T, Int32]) -> Int32: ...\n"
    "class Cell[T]:\n"
    "    value: T\n"
    "    def __init__(self, value: T) -> None:\n"
    "        self.value = value\n"
    "    def pair(self) -> tuple[T, Int32]:\n"
    "        return (self.value, Int32(1))\n"
    "    def consume(self, p: tuple[T, Int32]) -> Int32:\n"
    "        return p[1]\n"
    "def second_of[T](s: HasPair[T]) -> Int32:\n"
    "    p = s.pair()\n"
    "    return p[1]\n"
    "def use_consume[T](s: HasPair[T], v: T) -> Int32:\n"
    "    return s.consume((v, Int32(2)))\n"
    "def main() -> None:\n"
    "    c = Cell[Int32](Int32(42))\n"
    "    print(second_of(c))\n"
    "    print(use_consume(c, Int32(100)))\n"
    "main()\n")


_CELL = ("from typing import Protocol\n"
         "from tpy import Int32\n"
         "class HasPair[T](Protocol):\n"
         "    def pair(self) -> tuple[T, Int32]: ...\n"
         "class Cell[T]:\n"
         "    value: T\n"
         "    def __init__(self, value: T) -> None:\n"
         "        self.value = value\n"
         "    def pair(self) -> tuple[T, Int32]:\n"
         "        return (self.value, Int32(1))\n")


def _reject_tags(src: str) -> dict:
    return _reject_tally(src)


class TestOpenTTupleProtocolFamily:
    def test_all_five_rows_route_byte_identical(self):
        thir, wit = _lower_ctx_witnessed(_PAIR)
        assert wit.get("gentuple.field_elem", 0) == 1
        assert wit.get("decl.open_t_tuple_slot", 0) == 1
        assert wit.get("method.protocol_open_t_tuple_ret", 0) == 1
        assert wit.get("arg.open_t_tuple_literal", 0) == 1
        hpp, cpp = _assert_routes_byte_identical(_PAIR)
        both = hpp + cpp
        # The producer wraps each open element; the decl spells the bare T.
        assert ("std::tuple<::tpy::val_or_ptr_t<T>, int32_t>{"
                "::tpy::to_val_or_ptr<::tpy::val_or_ptr_t<T>>(this->value), 1}"
                ) in both
        assert "std::tuple<T, int32_t> p = s.pair();" in both

    def test_two_open_field_elements_route(self):
        # Both elements open (`tuple[T, T]`), both sourced from self fields.
        src = ("from typing import Protocol\n"
               "from tpy import Int32\n"
               "class HasTwo[T](Protocol):\n"
               "    def both(self) -> tuple[T, T]: ...\n"
               "class Pair[T]:\n"
               "    value: T\n    other: T\n"
               "    def __init__(self, value: T, other: T) -> None:\n"
               "        self.value = value\n        self.other = other\n"
               "    def both(self) -> tuple[T, T]:\n"
               "        return (self.value, self.other)\n"
               "def use_both[T](s: HasTwo[T]) -> Int32:\n"
               "    p = s.both()\n"
               "    return Int32(1)\n"
               "def main() -> None:\n"
               "    c = Pair[Int32](Int32(4), Int32(5))\n"
               "    print(use_both(c))\n"
               "main()\n")
        _assert_routes_byte_identical(src)

    def test_open_t_param_names_at_the_arg_slot_route(self):
        # The arg row's other element source: plain open-T param NAMES.
        src = ("from typing import Protocol\n"
               "from tpy import Int32\n"
               "class HasTwo[T](Protocol):\n"
               "    def take_both(self, p: tuple[T, T]) -> Int32: ...\n"
               "class Pair[T]:\n"
               "    value: T\n"
               "    def __init__(self, value: T) -> None:\n"
               "        self.value = value\n"
               "    def take_both(self, p: tuple[T, T]) -> Int32:\n"
               "        return Int32(1)\n"
               "def feed[T](s: HasTwo[T], a: T, b: T) -> Int32:\n"
               "    return s.take_both((a, b))\n"
               "def main() -> None:\n"
               "    c = Pair[Int32](Int32(4))\n"
               "    print(feed(c, Int32(1), Int32(2)))\n"
               "main()\n")
        _assert_routes_byte_identical(src)


class TestOpenTTupleBoundaries:
    def test_tuple_name_at_the_open_slot_stays_ast(self):
        # BOUNDARY: the arg row is keyed on the tuple LITERAL. A tuple NAME
        # at the same open-T slot passes a whole borrow tuple, a render this
        # family does not spell.
        src = ("from typing import Protocol\n"
               "from tpy import Int32\n"
               "class HasTwo[T](Protocol):\n"
               "    def both(self) -> tuple[T, T]: ...\n"
               "    def take_both(self, p: tuple[T, T]) -> Int32: ...\n"
               "class Pair[T]:\n"
               "    value: T\n    other: T\n"
               "    def __init__(self, value: T, other: T) -> None:\n"
               "        self.value = value\n        self.other = other\n"
               "    def both(self) -> tuple[T, T]:\n"
               "        return (self.value, self.other)\n"
               "    def take_both(self, p: tuple[T, T]) -> Int32:\n"
               "        return Int32(1)\n"
               "def relay[T](s: HasTwo[T]) -> Int32:\n"
               "    p = s.both()\n"
               "    return s.take_both(p)\n"
               "def main() -> None:\n"
               "    c = Pair[Int32](Int32(4), Int32(5))\n"
               "    print(relay(c))\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.method_call:method.protocol.arg_shape")

    def test_open_t_tuple_result_at_a_print_stays_ast(self):
        # BOUNDARY, the statement-position twin: the print gate has no
        # open-T tuple form.
        src = (_CELL
               + "def show[T](s: HasPair[T]) -> Int32:\n"
               + "    print(s.pair())\n    return Int32(0)\n"
               + "def main() -> None:\n"
               + "    c = Cell[Int32](Int32(42))\n    print(show(c))\n"
               + "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.expr_stmt:print.arg.tuple_method_call")

    def test_generic_tuple_call_at_the_matching_return_routes(self):
        # The call rvalue's own return slot IS the enclosing return slot, so
        # no element wrap applies and the value returns bare.
        src = (_CELL
               + "def relay[T](s: HasPair[T]) -> tuple[T, Int32]:\n"
               + "    return s.pair()\n"
               + "def main() -> None:\n"
               + "    c = Cell[Int32](Int32(42))\n"
               + "    p = relay(c)\n    print(p[1])\n"
               + "main()\n")
        _, wit = _lower_ctx_witnessed(src)
        assert wit.get("ret.generic_tuple_call", 0) == 1
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "return s.pair();" in hpp + cpp

    def test_generic_tuple_name_at_the_matching_return_stays_ast(self):
        # BOUNDARY: a NAME of the same generic tuple is an lvalue whose
        # return render is the holder's, not the call rvalue's.
        src = (_CELL
               + "def relay[T](s: HasPair[T]) -> tuple[T, Int32]:\n"
               + "    p = s.pair()\n"
               + "    return p\n"
               + "def main() -> None:\n"
               + "    c = Cell[Int32](Int32(42))\n"
               + "    q = relay(c)\n    print(q[1])\n"
               + "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.return:return.generic_tuple_source")

    def test_generic_tuple_field_at_the_matching_return_stays_ast(self):
        # BOUNDARY, the other lvalue source: a member read of the same
        # generic tuple.
        src = ("from tpy import Int32\n"
               "class Holder[T]:\n"
               "    p: tuple[T, Int32]\n"
               "    def __init__(self, v: T) -> None:\n"
               "        self.p = (v, Int32(1))\n"
               "    def get(self) -> tuple[T, Int32]:\n"
               "        return self.p\n"
               "def main() -> None:\n"
               "    h = Holder[Int32](Int32(42))\n"
               "    q = h.get()\n    print(q[1])\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.return:return.generic_tuple_source")
