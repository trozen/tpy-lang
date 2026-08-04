"""Comprehension FILTER temps: `temps_ok` on the condition lowering + the
emit's loop-body-indent flush before the `if`. A per-iteration owned-move
temp in a filter (`if is_small(Box(i))`) lands inside the loop scope where
the loop var is declared -- the AST's per-clause cond-temp placement.
Element-position temps stay rejected (set elements must be copyable; the
element lowering never threads allow_temps)."""

from .testutil import _assert_routes_byte_identical

_BOX = (
    "from tpy import Int32\n"
    "from tplib.box import Box\n"
    "def is_small(b: Box[Int32]) -> bool:\n"
    "    return b < Box(3)\n"
    "def is_even(n: Int32) -> bool:\n"
    "    return n % 2 == 0\n"
)


class TestCompConditionTemps:
    SRC = (
        _BOX
        + "def list_comp(n: Int32) -> None:\n"
        + "    xs = [i for i in range(n) if is_small(Box(i))]\n"
        + "    print(xs)\n"
        + "def set_comp(n: Int32) -> None:\n"
        + "    s = {i for i in range(n) if is_small(Box(i))}\n"
        + "    print(len(s))\n"
        + "def dict_comp(n: Int32) -> None:\n"
        + "    d = {i: i * 2 for i in range(n) if is_small(Box(i))}\n"
        + "    print(len(d))\n"
        + "def two_conds(n: Int32) -> None:\n"
        + "    xs = [i for i in range(n) if is_even(i) if is_small(Box(i))]\n"
        + "    print(xs)\n"
        + "def main() -> None:\n"
        + "    list_comp(6)\n    set_comp(6)\n    dict_comp(6)\n"
        + "    two_conds(6)\n"
        + "main()\n"
    )

    def test_routes_byte_identical(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC, comments=False)
        # The move-temp chain renders at loop-body indent before the if.
        assert "auto __tmp_1 = i;" in cpp
        assert "if (is_small(__tmp_2))" in cpp


class TestOwnedElementStaysFenced:
    # The ELEMENT position keeps rejecting: an owned-element comp
    # (`[Box(i) ...]`) rides the owns_elements gate to the AST path,
    # byte-identically -- filters got flush rights, elements did not.
    SRC = (
        _BOX
        + "def owned_elems(n: Int32) -> None:\n"
        + "    xs = [Box(i) for i in range(n)]\n"
        + "    print(len(xs))\n"
        + "def main() -> None:\n    owned_elems(4)\n"
        + "main()\n"
    )

    def test_owned_element_comp_stays_ast(self):
        from ..codegen_cpp import CodeGenOptions
        from .testutil import _assert_byte_identical, _compile, _entry
        _assert_byte_identical(self.SRC)
        compiler, modules = _compile(self.SRC)
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=True,
                                   comment_line_numbers=False,
                                   thir_codegen=True))
        assert any(k.startswith("body:") for k in compiler._thir_fallback), (
            compiler._thir_fallback)
