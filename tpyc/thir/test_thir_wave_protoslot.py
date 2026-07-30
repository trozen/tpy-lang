"""Protocol-body wave arms: the Own[Self]/protocol-result storage sink in
a monomorphized template body, the raw protocol-operand binop, the
protocol-typed print name, and the open-T container-element REF_ALIAS."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
)

_DUP = (
    "from __future__ import annotations\n"
    "from typing import Protocol, Self\n"
    "from tpy import Int32, Own\n"
    "class Duplicable(Protocol):\n"
    "    def duplicate(self) -> Own[Self]: ...\n"
    "class Value:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n"
    "        self.x = x\n"
    "    def duplicate(self) -> Own[Value]:\n"
    "        return Value(self.x * 2)\n"
)


class TestProtocolBody:
    def test_self_result_storage_decl_routes_auto(self):
        # `result = d.duplicate()` in the template body -> `auto result =
        # d.duplicate();` (the Own[Self] rvalue in the `auto` slot).
        src = (_DUP
               + "def double_it(d: Duplicable) -> None:\n"
               + "    result = d.duplicate()\n"
               + "def main() -> None:\n"
               + "    v = Value(21)\n"
               + "    double_it(v)\n"
               + "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "double_it") is not None
        assert faces.get("method.protocol_self_storage_ret", 0) >= 1
        cpp = _assert_byte_identical(src)
        assert "auto result = d.duplicate();" in cpp[0]

    def test_protocol_operand_binop_routes_raw(self):
        # `result = x + y` over structural-protocol params renders the raw
        # C++ operator into the `auto` slot; print streams it raw.
        src = ("from typing import Protocol, Self\n"
               "from tpy import Int32\n"
               "class Addable(Protocol):\n"
               "    def __add__(self, other: Self) -> Self: ...\n"
               "def add_values(x: Addable, y: Addable) -> None:\n"
               "    result = x + y\n"
               "    print(result)\n"
               "def main() -> None:\n"
               "    a: Int32 = 21\n"
               "    b: Int32 = 21\n"
               "    add_values(a, b)\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "add_values") is not None
        assert faces.get("binop.protocol_raw", 0) == 1
        assert faces.get("print.protocol_name", 0) == 1
        cpp = _assert_byte_identical(src)
        assert "auto result = (x + y);" in cpp[0]

    def test_tparam_elem_subscript_alias_routes(self):
        # `v = self.items[self.pos]` in a generic record body binds the
        # per-instantiation `T&` element alias.
        src = ("from tpy import Int32\n"
               "class Holder[T]:\n"
               "    items: list[T]\n"
               "    pos: Int32\n"
               "    def __init__(self, items: list[T]) -> None:\n"
               "        self.items = items\n"
               "        self.pos = 0\n"
               "    def first(self) -> T:\n"
               "        v = self.items[self.pos]\n"
               "        return v\n"
               "def main() -> None:\n"
               "    h = Holder([1, 2, 3])\n"
               "    print(h.first())\n"
               "main()\n")
        cpp = _assert_byte_identical(src)
        assert ("T& v = ::tpy::__getitem__(this->items, this->pos);"
                in cpp[0])

    def test_protocol_floordiv_maps_to_slash(self):
        # `a // b` over protocol operands maps to C++ `/` (a verbatim `//`
        # would be a line comment) -- the AST protocol arm's cpp_op rule.
        src = ("from typing import Protocol, Self\n"
               "from tpy import Int32\n"
               "class Divs(Protocol):\n"
               "    def __floordiv__(self, other: Self) -> Self: ...\n"
               "def halve(x: Divs, y: Divs) -> None:\n"
               "    result = x // y\n"
               "    print(result)\n"
               "def main() -> None:\n"
               "    a: Int32 = 10\n"
               "    b: Int32 = 2\n"
               "    halve(a, b)\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "halve") is not None
        cpp = _assert_byte_identical(src)
        assert "auto result = (x / y);" in cpp[0]
        assert "(x // y)" not in cpp[0] and "(x // y)" not in cpp[1]

    def test_protocol_result_at_print_position_stays_ast(self):
        # The Own[Self] widening is a STORAGE-sink slice: the same call in
        # print position (a VALUE sink; the open-Self result carries no
        # sema type for the record row) must keep falling back.
        src = (_DUP
               + "def double_it(d: Duplicable) -> None:\n"
               + "    print(d.duplicate())\n"
               + "def main() -> None:\n"
               + "    double_it(Value(21))\n"
               + "main()\n")
        assert _fn(_lower_ctx(src), "double_it") is None
        _assert_byte_identical(src)

    def test_protocol_binop_nonname_operand_stays_ast(self):
        # The raw protocol binop is sliced to bare NAME operands; a nested
        # binop operand must keep falling back.
        src = ("from typing import Protocol, Self\n"
               "from tpy import Int32\n"
               "class Addable(Protocol):\n"
               "    def __add__(self, other: Self) -> Self: ...\n"
               "def add3(x: Addable, y: Addable) -> None:\n"
               "    result = x + y + y\n"
               "    print(result)\n"
               "def main() -> None:\n"
               "    a: Int32 = 1\n"
               "    b: Int32 = 2\n"
               "    add3(a, b)\n"
               "main()\n")
        assert _fn(_lower_ctx(src), "add3") is None
        _assert_byte_identical(src)

    def test_reassigned_tparam_elem_stays_ast(self):
        # A REASSIGNED open-T element local takes the `T*` reseat
        # machinery (`T* result = &(...)`) -- not carried, keeps rejecting.
        src = ("from tpy import Int32\n"
               "def last[T](a: list[T]) -> T:\n"
               "    result: T = a[0]\n"
               "    for i in range(1, len(a)):\n"
               "        result = a[i]\n"
               "    return result\n"
               "def main() -> None:\n"
               "    print(last([1, 2, 3]))\n"
               "main()\n")
        assert _fn(_lower_ctx(src), "last") is None
        _assert_byte_identical(src)
