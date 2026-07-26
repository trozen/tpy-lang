"""Nested-container ELEMENT lvalues in the two native sinks that consume a
whole container: the builtin `len(...)` arg and the kind-keyed print wrap.

`groups["a"]` off `dict[str, list[Int32]]` is a checked read into the
container's own storage (`::tpy::__getitem__(groups, "a")`), so it renders
bare where a container NAME does. Both sinks precheck the element read: the
value-position element gate would reject it (its result is a container), and
the REF_ALIAS shape check (`_container_ref_alias_elem_subscript`) is what
licenses skipping that gate -- it excludes exactly the shapes the precheck
would otherwise drop (an unproven Optional element read, a slice, an
unroutable index).
"""

from __future__ import annotations

import io

from .emit import emit_thir_body
from .testutil import (_lower_ctx, _lower_ctx_witnessed, _fn,
                       _assert_byte_identical)


def _body(thir, name: str) -> str:
    buf = io.StringIO()
    emit_thir_body(buf, _fn(thir, name))
    return buf.getvalue()


_ROWS = (
    "from tpy import Int32, Own\n"
    "class Row:\n"
    "    cells: list[Int32]\n"
    "    def __init__(self, cells: Own[list[Int32]]):\n        self.cells = cells\n"
    "class Grid:\n"
    "    rows: list[Row]\n"
    "    lookup: dict[str, list[Int32]]\n"
    "    def __init__(self):\n        self.rows = []\n        self.lookup = {}\n"
)


class TestLenElementSubscript:
    def test_dict_element_arg_routes_bare(self):
        src = ("from tpy import Int32\n"
               "def f(groups: dict[str, list[Int32]]) -> Int32:\n"
               "    return len(groups[\"a\"])\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("len.elem_subscript")
        assert ("::tpy::__len__(::tpy::__getitem__(groups, \"a\"))"
                in _body(thir, "f"))
        _assert_byte_identical(src)

    def test_list_element_arg_routes_bare(self):
        src = ("from tpy import Int32\n"
               "def f(m: list[list[Int32]], i: Int32) -> Int32:\n"
               "    return len(m[0]) + len(m[i])\n")
        _assert_byte_identical(src)

    def test_field_receiver_element_arg_routes(self):
        src = _ROWS + ("def f(g: Grid) -> Int32:\n"
                       "    return len(g.lookup[\"k\"])\n")
        thir = _lower_ctx(src)
        assert ("::tpy::__len__(::tpy::__getitem__(g.lookup, \"k\"))"
                in _body(thir, "f"))
        _assert_byte_identical(src)

    def test_field_off_record_element_routes(self):
        # The other half of the row: the ARG is a field whose receiver is a
        # record-element subscript (`g.rows[0].cells`).
        src = _ROWS + ("def f(g: Grid) -> Int32:\n"
                       "    return len(g.rows[0].cells)\n")
        thir = _lower_ctx(src)
        assert ("::tpy::__len__(::tpy::__getitem__(g.rows, 0).cells)"
                in _body(thir, "f"))
        _assert_byte_identical(src)

    def test_slice_arg_stays_ast(self):
        # A slice yields a fresh container RVALUE, not an element lvalue --
        # the element row must not claim it.
        src = ("from tpy import Int32\n"
               "def f(m: list[list[Int32]]) -> Int32:\n"
               "    return len(m[0:2])\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is None
        assert not faces.get("len.elem_subscript")

    def test_doubly_nested_element_stays_ast(self):
        # `m[0][1]`: the receiver is itself an element subscript, which the
        # one-level receiver resolver does not admit.
        src = ("from tpy import Int32\n"
               "def f(m: list[list[list[Int32]]]) -> Int32:\n"
               "    return len(m[0][1])\n")
        assert _fn(_lower_ctx(src), "f") is None


class TestPrintElementSubscript:
    def test_list_element_streams_through_list_printer(self):
        src = ("from tpy import Int32\n"
               "def f(groups: dict[str, list[Int32]]) -> None:\n"
               "    print(groups[\"a\"])\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("print.elem_subscript")
        assert ("::tpy::ListPrinter(::tpy::__getitem__(groups, \"a\"))"
                in _body(thir, "f"))
        _assert_byte_identical(src)

    def test_set_and_dict_elements_pick_their_own_printer(self):
        src = ("from tpy import Int32\n"
               "def f(a: dict[str, set[Int32]],\n"
               "      b: dict[str, dict[str, Int32]]) -> None:\n"
               "    print(a[\"s\"])\n"
               "    print(b[\"d\"])\n")
        body = _body(_lower_ctx(src), "f")
        assert "::tpy::SetPrinter(::tpy::__getitem__(a, \"s\"))" in body
        assert "::tpy::DictPrinter(::tpy::__getitem__(b, \"d\"))" in body
        _assert_byte_identical(src)

    def test_scalar_element_keeps_the_plain_stream(self):
        # The wrap gate must not claim a scalar element -- it streams bare.
        src = ("from tpy import Int32\n"
               "def f(m: list[Int32]) -> None:\n"
               "    print(m[0])\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert not faces.get("print.elem_subscript")
        _assert_byte_identical(src)
