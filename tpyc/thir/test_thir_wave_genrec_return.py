"""Genrec track cell C (first slice): the `Own[Tree[T]]` return-slot rows.
A container literal takes the ru-instance spelled render
(`return std::vector<Tree<int32_t>>{1, 2, 3};` -- with fixed-int ctor
elements folding to their bare tokens); a scalar member value returns bare
(`return 7;`, the converting ctor absorbs it). A NAME source stays a named
reject (`return.genrec_source`), byte-identically."""

from ..codegen_cpp import CodeGenOptions
from .testutil import (
    _reject_tally,
    _assert_byte_identical,
    _assert_routes_byte_identical,
    _compile,
    _entry,
)

_TREE = (
    "from tpy import Int32, Own\n"
    "type Tree[T] = T | list[Tree[T]]\n"
)


class TestGenrecReturnRows:
    SRC = (
        _TREE
        + "def make_leaf() -> Own[Tree[Int32]]:\n"
        + "    return Int32(7)\n"
        + "def make_branch() -> Own[Tree[Int32]]:\n"
        + "    return [Int32(1), Int32(2), Int32(3)]\n"
        + "def main() -> None:\n"
        + "    leaf: Tree[Int32] = 3\n"
        + "    print(2)\n"
        + "main()\n"
    )

    def test_routes_byte_identical(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC, comments=False)
        assert "return 7;" in cpp
        assert "return std::vector<Tree<int32_t>>{1, 2, 3};" in cpp


class TestGenrecReturnScalarName:
    # The arm admits any _resolved_scalar source, not just literals: a
    # scalar-typed NAME returns bare too (the converting ctor absorbs the
    # name read).
    SRC = (
        _TREE
        + "def from_name(n: Int32) -> Own[Tree[Int32]]:\n"
        + "    return n\n"
        + "def main() -> None:\n    print(1)\n"
        + "main()\n"
    )

    def test_routes_byte_identical(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC, comments=False)
        assert "return n;" in cpp


class TestGenrecReturnNameStaysFenced:
    SRC = (
        _TREE
        + "def pass_through(t: Own[Tree[Int32]]) -> Own[Tree[Int32]]:\n"
        + "    return t\n"
        + "def main() -> None:\n    pass\n"
        + "main()\n"
    )

    def test_name_source_stays_ast(self):
        assert any(('return.genrec_source' in k for k in _reject_tally(self.SRC))), _reject_tally(self.SRC)
