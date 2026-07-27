"""Nested-container ELEMENT lvalues in value positions.

`groups["a"]` off `dict[str, list[Int32]]` is a checked read into the
container's own storage (`::tpy::__getitem__(groups, "a")`). The AST has ONE
element emitter and it is consumer-blind, so the read lands bare in every
value position -- one general gate arm covers all of them, and the two sinks
that used to precheck the read (the builtin `len(...)` arg, the kind-keyed
print wrap) now go through that gate like everything else.

The gate is what these units are for: it applies the checks a precheck
skipped, so a slice result and an unproven-Optional receiver must still
reject. A routing pin here asserts on the lowered function, not on emitted
text -- fallback emits byte-identical AST, which a text assertion cannot
tell apart.
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
        assert faces.get("subscript.container_elem")
        assert ("::tpy::__len__(::tpy::__getitem__(groups, \"a\"))"
                in _body(thir, "f"))
        _assert_byte_identical(src)

    def test_list_element_arg_routes_bare(self):
        src = ("from tpy import Int32\n"
               "def f(m: list[list[Int32]], i: Int32) -> Int32:\n"
               "    return len(m[0]) + len(m[i])\n")
        assert _fn(_lower_ctx(src), "f") is not None
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
        # the gate must not claim it.
        src = ("from tpy import Int32\n"
               "def f(m: list[list[Int32]]) -> Int32:\n"
               "    return len(m[0:2])\n")
        assert _fn(_lower_ctx(src), "f") is None

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
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        assert ("::tpy::ListPrinter(::tpy::__getitem__(groups, \"a\"))"
                in _body(thir, "f"))
        _assert_byte_identical(src)

    def test_set_and_dict_elements_pick_their_own_printer(self):
        # The printer KIND selection stays where it is -- it is semantic
        # routing, not a gate bypass.
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
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)


class TestGeneralValuePositions:
    """What the ONE gate arm covers, and what still has its own row.

    The bind and the method-receiver positions route through rows that never
    consulted this gate (`LocalBinding.REF_ALIAS`, the container-method
    receiver), so they are pinned as already-routing, not as this arm's work.
    The call-arg and for-head positions still REJECT: their own gates -- not
    `ret_ok` -- decide them, which is what "the arm is position-blind but the
    positions are not all open" means here.
    """

    def test_local_bind_routes_through_its_own_row(self):
        src = ("from tpy import Int32\n"
               "def f(g: dict[str, list[Int32]]) -> Int32:\n"
               "    row = g[\"a\"]\n"
               "    return len(row)\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

    def test_method_receiver_routes(self):
        src = ("from tpy import Int32\n"
               "def f(g: dict[str, list[Int32]]) -> None:\n"
               "    g[\"a\"].append(4)\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

    def test_call_arg_position_still_rejects(self):
        # The free-call arg gate, not `ret_ok`, decides this one.
        src = ("from tpy import Int32\n"
               "def take(xs: list[Int32]) -> Int32:\n"
               "    return len(xs)\n"
               "def f(g: dict[str, list[Int32]]) -> Int32:\n"
               "    return take(g[\"a\"])\n")
        assert _fn(_lower_ctx(src), "f") is None

    def test_for_head_position_still_rejects(self):
        src = ("from tpy import Int32\n"
               "def f(g: dict[str, list[Int32]]) -> Int32:\n"
               "    total = 0\n"
               "    for v in g[\"a\"]:\n"
               "        total += v\n"
               "    return total\n")
        assert _fn(_lower_ctx(src), "f") is None


class TestGateBoundaries:
    """The shapes the gate excludes.

    An unproven-Optional RECEIVER is NOT pinned here because the shape is
    unreachable: sema rejects subscripting a nullable container before
    lowering ever sees it, so the runtime-check guard the deleted per-sink
    bypasses skipped has no witness to assert on. The slice and doubly-nested
    exclusions below are the ones this gate actually decides.
    """

    def test_slice_result_rejects_at_the_len_sink(self):
        src = ("from tpy import Int32\n"
               "def f(m: list[list[Int32]]) -> Int32:\n"
               "    return len(m[0:2])\n")
        assert _fn(_lower_ctx(src), "f") is None
