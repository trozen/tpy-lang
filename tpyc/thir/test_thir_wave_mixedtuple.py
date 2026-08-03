"""Mixed owned+borrow tuple (`tuple[Own[A], A]`) at the single-binding
`auto` alias decl: the BORROW-form call result binds whole (owned elements
by value, ref elements as pointers) and reads pick `.` vs `->` per element.
The REASSIGNED mixed local (the const-borrow-form fixpoint family) and the
mixed unpack stay on the AST path."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
    _assert_routes_byte_identical,
)

_HDR = (
    "from tpy import Int32, Own\n"
    "class Cell:\n"
    "    val: Int32\n"
    "    def __init__(self, val: Int32) -> None:\n"
    "        self.val = val\n"
    "class Maker:\n"
    "    def mixed(self, c: Cell) -> tuple[Own[Cell], Cell]:\n"
    "        return (Cell(1), c)\n"
)


class TestMixedOwnTupleAliasDecl:
    def test_mixed_alias_decl_routes_per_element_reads(self):
        # `auto x = m.mixed(c);` -- owned element reads `.`, borrowed `->`;
        # the write through the borrowed element mutates the source.
        src = (_HDR +
               "def main() -> None:\n"
               "    c = Cell(7)\n"
               "    m = Maker()\n"
               "    x = m.mixed(c)\n"
               "    x[1].val = 42\n"
               "    print(x[0].val, x[1].val, c.val)\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces.get("decl.mixed_own_alias", 0) == 1
        assert faces.get("method.btuple_slot", 0) == 1
        cpp = _assert_routes_byte_identical(src)
        assert "auto x = m.mixed(c);" in cpp[1]
        assert "std::get<0>(x).val" in cpp[1]
        assert "std::get<1>(x)->val = 42;" in cpp[1]

    def test_mixed_reassigned_local_stays_ast(self):
        # The reassigned mixed local declares ONE fixed slot type via the
        # AST's const-borrow-form fixpoint -- unmirrored, keeps falling back.
        src = (_HDR +
               "def main() -> None:\n"
               "    c = Cell(7)\n"
               "    d = Cell(9)\n"
               "    m = Maker()\n"
               "    x = m.mixed(c)\n"
               "    x = m.mixed(d)\n"
               "    print(x[0].val, x[1].val)\n"
               "main()\n")
        assert _fn(_lower_ctx(src), "main") is None
        _assert_byte_identical(src)

    def test_mixed_unpack_stays_ast(self):
        # `a, b = m.mixed(c)` -- the own-borrow hybrid unpack render is its
        # own family and keeps rejecting.
        src = (_HDR +
               "def main() -> None:\n"
               "    c = Cell(7)\n"
               "    m = Maker()\n"
               "    a, b = m.mixed(c)\n"
               "    print(a.val, b.val)\n"
               "main()\n")
        assert _fn(_lower_ctx(src), "main") is None
        _assert_byte_identical(src)
