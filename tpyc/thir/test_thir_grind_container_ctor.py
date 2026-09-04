"""Constructor-shaped container rows: the `[e] * n` member-init cell, the
iterator-rvalue temp at a nullable protocol ctor slot, and the computed str
argument to `super().__init__`."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _assert_routes_byte_identical,
    _fn,
    _lower_ctx_witnessed,
    _reject_tally,
    _thir_ctx_witnessed,
)

_REPEAT_SRC = (
    "from tpy import Int32\n"
    "class Grid:\n"
    "    cells: list[Int32]\n"
    "    tags: list[Int32]\n"
    "    def __init__(self, n: Int32):\n"
    "        self.cells = [0] * 8\n"
    "        self.tags = [n] * 4\n"
    "def main() -> None:\n"
    "    g = Grid(7)\n"
    "    print(len(g.cells), g.cells[0], g.tags[0])\n"
    "main()\n"
)

_ITER_TEMP_SRC = (
    "from tpy import Int32\n"
    "from tplib import ArrayList\n"
    "def main() -> None:\n"
    "    d = dict([(\"one\", Int32(1)), (\"two\", Int32(2))])\n"
    "    from_dict = ArrayList[tuple[str, Int32], 16](d.items())\n"
    "    print(len(from_dict))\n"
    "main()\n"
)

_BASE_INIT_SRC = (
    "from tpy import Int32\n"
    "class Tagged(Exception):\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        super().__init__(\"tag\" + str(n))\n"
    "        self.n = n\n"
    "def main() -> None:\n"
    "    try:\n"
    "        raise Tagged(5)\n"
    "    except Tagged as e:\n"
    "        print(e.n)\n"
    "main()\n"
)


class TestCtorListRepeatMil:
    def test_routes_the_member_init_cell(self):
        # The ctor-side driver: `_lower_ctx_witnessed` lowers free functions
        # only, so a MIL face is invisible to it.
        ctx, faces, reasons = _thir_ctx_witnessed(_REPEAT_SRC)
        assert ctx is not None and not reasons
        assert faces["mil.container_repeat"] >= 2

    def test_renders_the_threaded_repeat(self):
        hpp = _assert_routes_byte_identical(_REPEAT_SRC)[0]
        assert ("cells(::tpy::from_range<std::vector<int32_t>>("
                "::tpy::repeat_range<int32_t>(8, {0})))" in hpp)
        assert ("tags(::tpy::from_range<std::vector<int32_t>>("
                "::tpy::repeat_range<int32_t>(4, {n})))" in hpp)

    def test_comprehension_member_init_routes(self):
        # The adjacent source shape: a comprehension takes the same cell, with
        # its statement-expression as the member-init value.
        src = _REPEAT_SRC.replace("        self.cells = [0] * 8\n",
                                  "        self.cells = [i for i in range(8)]\n")
        ctx, faces, reasons = _thir_ctx_witnessed(src)
        assert ctx is not None and not reasons
        assert faces["mil.container_comp"] == 1
        hpp = _assert_routes_byte_identical(src)[0]
        assert "cells(({" in hpp and "__result.push_back(i);" in hpp

    def test_comprehension_call_element_keeps_rejecting(self):
        # BOUNDARY: the comprehension's own element rules still apply, and a
        # member-init reject fails the whole constructor.
        src = _REPEAT_SRC.replace(
            "from tpy import Int32\n",
            "from tpy import Int32, Own\n"
            "def mk(i: Int32) -> Own[list[Int32]]:\n    return [i]\n"
        ).replace("    cells: list[Int32]\n", "    cells: list[list[Int32]]\n"
        ).replace("        self.cells = [0] * 8\n",
                  "        self.cells = [mk(i) for i in range(8)]\n"
        ).replace("g.cells[0], ", "")
        _assert_rejects_at(_reject_tally(src), "ctor",
                           shape="comp.container_value")


class TestProtocolUnionIterTemp:
    def test_routes_the_iter_temp(self):
        thir, faces = _lower_ctx_witnessed(_ITER_TEMP_SRC)
        assert _fn(thir, "main") is not None
        assert faces["argtemp.protocol_union_iter"] == 1

    def test_renders_the_spelled_temp_and_addr(self):
        cpp = _assert_routes_byte_identical(_ITER_TEMP_SRC)[1]
        assert ("::tpy::dict_items_view<std::string, int32_t> __tmp_1 = "
                "::tpy::dict_items(d);" in cpp)
        assert "16>(&(__tmp_1));" in cpp

    def test_plain_container_name_keeps_the_addr_row(self):
        # The boundary is the row NEXT DOOR, not a reject: a container NAME
        # at the same slot takes the bare address-of lift with no temp.
        # The annotation on `src` is load-bearing: unannotated, the literal
        # resolves to `Array[Int32, 3]` and the ctor arg rejects at
        # `call.ctor_arg.union` -- a pending-literal resolution gap (the
        # slot is not a resolution sink), filed, not this row's.
        src = ("from tpy import Int32\n"
               "from tplib import ArrayList\n"
               "def main() -> None:\n"
               "    src: list[Int32] = [1, 2, 3]\n"
               "    a = ArrayList[Int32, 8](src)\n"
               "    print(len(a))\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces.get("argtemp.protocol_union_iter", 0) == 0
        assert "8>(&(src));" in _assert_routes_byte_identical(src)[1]


class TestBaseInitStrConcat:
    def test_routes_the_computed_base_arg(self):
        ctx, _faces, reasons = _thir_ctx_witnessed(_BASE_INIT_SRC)
        assert ctx is not None and not reasons

    def test_renders_the_concat_in_the_base_cell(self):
        hpp = _assert_routes_byte_identical(_BASE_INIT_SRC)[0]
        assert ("::tpy::Exception((::tpy::str_concat(\"tag\", "
                "::tpy::fixed_to_str<int32_t>(n))))" in hpp)

    def test_repr_conversion_operand_routes(self):
        # `repr()` is the conversion sibling of `str()`: also a pure
        # expression, so it lands in the same cell.
        src = _BASE_INIT_SRC.replace('"tag" + str(n)', '"tag" + repr(n)')
        ctx, _faces, reasons = _thir_ctx_witnessed(src)
        assert ctx is not None and not reasons
        hpp = _assert_routes_byte_identical(src)[0]
        assert ("::tpy::Exception((::tpy::str_concat(\"tag\", "
                "::tpy::repr_of(n))))" in hpp)

    def test_bigint_operand_routes_and_renders(self):
        # The `int` (BigInt) spelling of the same shape: the conversion is
        # a method on the value rather than a free template, and it is a
        # pure expression too.
        src = _BASE_INIT_SRC.replace("from tpy import Int32\n", "").replace(
            "    n: Int32\n", "    n: int\n").replace(
            "def __init__(self, n: Int32)", "def __init__(self, n: int)")
        ctx, _faces, reasons = _thir_ctx_witnessed(src)
        assert ctx is not None and not reasons
        hpp = _assert_routes_byte_identical(src)[0]
        assert ("::tpy::Exception((::tpy::str_concat(\"tag\", "
                "(n).to_string())))" in hpp)

    def test_bare_conversion_arg_routes_and_renders(self):
        # The conversion standing ALONE rather than as a concat operand:
        # same pure render, so it is the same cell one level up.
        src = _BASE_INIT_SRC.replace('"tag" + str(n)', 'str(n)')
        ctx, _faces, reasons = _thir_ctx_witnessed(src)
        assert ctx is not None and not reasons
        hpp = _assert_routes_byte_identical(src)[0]
        assert ("::tpy::Exception(::tpy::fixed_to_str<int32_t>(n))" in hpp)

    def test_fstring_arg_routes_and_renders(self):
        # `std::format` is a pure expression like `str_concat`, so the
        # f-string spelling of the same message lands in the cell too.
        src = _BASE_INIT_SRC.replace('"tag" + str(n)', 'f"tag{n}"')
        ctx, _faces, reasons = _thir_ctx_witnessed(src)
        assert ctx is not None and not reasons
        hpp = _assert_routes_byte_identical(src)[0]
        assert '::tpy::Exception(std::format("tag{}", n))' in hpp

    def test_fstring_format_spec_keeps_rejecting(self):
        # BOUNDARY: a conversion or format spec spells its own render, which
        # this row has not read -- only the bare interpolation is admitted.
        src = _BASE_INIT_SRC.replace('"tag" + str(n)', 'f"tag{n:04d}"')
        _assert_rejects_at(_reject_tally(src), "ctor", shape="ctor.base_init")

    def test_walrus_part_keeps_rejecting(self):
        # BOUNDARY: a named expression inside the message would bind in a
        # cell with no flush point, and the format call's part evaluation
        # order is unspecified, so the write and a later read of the same
        # name would race; the concat spelling refuses it the same way.
        src = _BASE_INIT_SRC.replace('"tag" + str(n)',
                                     'f"x{(m := n + 1)}y{m}"')
        _assert_rejects_at(_reject_tally(src), "ctor", shape="ctor.base_init")
        src = _BASE_INIT_SRC.replace('"tag" + str(n)',
                                     '"x" + str((m := n + 1)) + str(m)')
        _assert_rejects_at(_reject_tally(src), "ctor", shape="ctor.base_init")

    def test_non_conversion_call_operand_keeps_rejecting(self):
        # A user call operand is outside the temp-free rows the cell needs
        # (the base-init position has no flush point).
        src = _BASE_INIT_SRC.replace(
            "class Tagged(Exception):\n",
            "def fmt(n: Int32) -> str:\n"
            "    return str(n) + \"!\"\n"
            "class Tagged(Exception):\n").replace(
            "        super().__init__(\"tag\" + str(n))\n",
            "        super().__init__(\"tag\" + fmt(n))\n")
        _assert_rejects_at(_reject_tally(src), "ctor", shape="ctor.base_init")
