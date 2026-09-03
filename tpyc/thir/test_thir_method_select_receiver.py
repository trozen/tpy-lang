"""Ternary and walrus method receivers. Over BARE value operands they render
as the plain C++ select / comma form with `.` access on both paths, so a
str/bytes-value or plain-record select receiver routes. An operand that is a
pointer-local or a narrowed Optional name renders through its pointer, and
every other non-name receiver kind (a literal, an f-string, a container) has
no row: those reject at the receiver row, never escape the classifier."""

from __future__ import annotations

from .testutil import (
    _reject_tally,
    _assert_byte_identical, _assert_rejects_at, _assert_routes_byte_identical,
    _thir_ctx,
)

_SHAPE = ("from tpy import Int32\n"
          "class Shape:\n"
          "    n: Int32\n"
          "    def __init__(self, n: Int32) -> None:\n"
          "        self.n = n\n"
          "    def area(self) -> Int32:\n"
          "        return self.n\n")


class TestSelectReceiverRoutes:
    def test_walrus_str_receiver(self):
        src = ("def main() -> None:\n"
               "    s = (t := \"ab\").upper()\n"
               "    print(s, t)\n"
               "main()\n")
        _assert_routes_byte_identical(src)

    def test_ternary_str_receiver(self):
        src = ("def main() -> None:\n"
               "    s = \"ab\"\n"
               "    t = \"cd\"\n"
               "    c = True\n"
               "    print((s if c else t).upper())\n"
               "main()\n")
        _assert_routes_byte_identical(src)

    def test_ternary_record_receiver(self):
        src = (_SHAPE
               + "def main() -> None:\n"
               "    a = Shape(1)\n"
               "    b = Shape(2)\n"
               "    c = True\n"
               "    print((a if c else b).area())\n"
               "main()\n")
        _assert_routes_byte_identical(src)

    def test_walrus_record_receiver(self):
        src = (_SHAPE
               + "def main() -> None:\n"
               "    a = Shape(1)\n"
               "    print((q := a).area(), q.area())\n"
               "main()\n")
        _assert_routes_byte_identical(src)

    def test_walrus_bytes_receiver(self):
        src = ("def main() -> None:\n"
               "    print((b := b\"ab\").hex(), b.hex())\n"
               "main()\n")
        _assert_routes_byte_identical(src)

    def test_own_record_params_ternary_receiver(self):
        # `Own[Shape]` params peel to the record on both paths and render
        # bare, like a plain record local.
        src = ("from tpy import Int32, Own\n"
               "class Shape:\n"
               "    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        self.n = n\n"
               "    def area(self) -> Int32:\n"
               "        return self.n\n"
               "def f(a: Own[Shape], b: Own[Shape], c: bool) -> Int32:\n"
               "    return (a if c else b).area()\n"
               "def main() -> None:\n"
               "    print(f(Shape(1), Shape(2), True))\n"
               "main()\n")
        _assert_routes_byte_identical(src)

    def test_generic_record_walrus_receiver(self):
        # A generic-record instance is an F1 record too.
        src = ("from tpy import Int32\n"
               "class Stack[T]:\n"
               "    items: list[T]\n"
               "    def __init__(self) -> None:\n"
               "        self.items = []\n"
               "    def size(self) -> Int32:\n"
               "        return len(self.items)\n"
               "def f(s: Stack[Int32]) -> Int32:\n"
               "    if (q := s).size() > 0:\n"
               "        return q.size()\n"
               "    return 0\n"
               "def main() -> None:\n"
               "    print(f(Stack[Int32]()))\n"
               "main()\n")
        _assert_routes_byte_identical(src)


class TestSelectReceiverRejects:
    def _rejects(self, src: str, landmark: str) -> None:
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, landmark, "method.recv.other")

    def test_narrowed_optional_operands_reject(self):
        # Both operands are narrowed `Shape | None` names: the AST renders
        # them through their pointers, so the select row must not claim
        # the shape (its bare render would diverge).
        src = (_SHAPE
               + "def f(a: Shape | None, b: Shape | None, c: bool) -> Int32:\n"
               "    if a is not None and b is not None:\n"
               "        return (a if c else b).area()\n"
               "    return 0\n"
               "def main() -> None:\n"
               "    print(f(Shape(1), Shape(2), True))\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.method_call:method.recv.other")

    def test_narrowed_value_optional_str_operand_rejects(self):
        # A narrowed `str | None` is value-repr (no pointer-local), so this
        # is the Optional-declared clause of the operand guard on its own.
        src = ("def f(s: str | None, t: str) -> str:\n"
               "    if s is not None:\n"
               "        return (s if len(t) > 1 else t).upper()\n"
               "    return t\n"
               "def main() -> None:\n"
               "    print(f(\"ab\", \"cd\"))\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.method_call:method.recv.other")

    def test_module_global_operand_rejects(self):
        # A module global is not a declared local: the guard's first clause.
        src = (_SHAPE
               + "G = Shape(7)\n"
               "def main() -> None:\n"
               "    b = Shape(2)\n"
               "    c = True\n"
               "    print((G if c else b).area())\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.expr_stmt:method.recv.other")

    def test_nested_select_rejects_at_the_ternary(self):
        # A record-valued inner ternary is not a shape the ternary lowering
        # admits, so the reject lands there, before the receiver row.
        src = (_SHAPE
               + "def main() -> None:\n"
               "    a = Shape(1)\n"
               "    b = Shape(2)\n"
               "    d = Shape(3)\n"
               "    c = True\n"
               "    e = False\n"
               "    print((a if c else (b if e else d)).area())\n"
               "    print((q := (a if e else b)).area(), q.area())\n"
               "main()\n")
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:stmt.expr_stmt",
                           "ifexpr.record_arm")

    def test_container_ternary_receiver_rejects(self):
        # A list is neither a str value nor a record: the boundary of the
        # select row's admitted families.
        src = ("def main() -> None:\n"
               "    xs = [1]\n"
               "    ys = [2]\n"
               "    c = True\n"
               "    (xs if c else ys).append(3)\n"
               "    print(len(xs), len(ys))\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.method_call:method.recv.other")

    def test_bytes_ternary_rejects_at_the_ternary(self):
        # The receiver row admits a bytes-value select, but the ternary
        # lowering itself has no bytes arm yet, so the reject lands there.
        src = ("def main() -> None:\n"
               "    b = b\"ab\"\n"
               "    d = b\"cd\"\n"
               "    c = True\n"
               "    print((b if c else d).hex())\n"
               "main()\n")
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:stmt.expr_stmt",
                           "ifexpr.bytes_mixed")

    def test_list_literal_receiver_rejects(self):
        self._rejects("def main() -> None:\n"
                      "    print([1, 2, 1].count(1))\n"
                      "main()\n", "body:stmt.expr_stmt")

    def test_fstring_receiver_rejects(self):
        self._rejects("def main() -> None:\n"
                      "    x = 5\n"
                      "    print(f\"{x}\".upper())\n"
                      "main()\n", "body:stmt.expr_stmt")
