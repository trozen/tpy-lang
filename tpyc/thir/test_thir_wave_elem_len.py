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
from .testutil import (
    _assert_rejects_at,
    _reject_tally, _lower_ctx, _lower_ctx_witnessed, _fn, _thir_ctx,
                       _assert_byte_identical, _assert_routes_byte_identical)


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

    def test_slice_arg_routes_via_native_row(self):
        # A slice yields a fresh container RVALUE, not an element lvalue --
        # the element gate must not claim it. It routes through its OWN row
        # instead, the native slice-subscript arg (`__len__(::tpy::
        # list_slice(m, ..))`), witnessed apart from the element face.
        src = ("from tpy import Int32\n"
               "def f(m: list[list[Int32]]) -> Int32:\n"
               "    return len(m[0:2])\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("arg.native_slice_subscript")
        assert "subscript.container_elem" not in faces
        _assert_routes_byte_identical(src)

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
    The call-arg and for-head positions now route through their OWN gates
    (the elem-subscript ref-arg row and the container route's subscript
    leg) -- pinned below as routing, with the begin/end render the oracle
    demands.
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

    def test_call_arg_position_routes(self):
        # The container-element subscript at a matching ref param now
        # routes (the _record_elem_subscript_arg container flavor): the
        # checked lvalue binds the `std::vector<T>&` slot inline.
        src = ("from tpy import Int32\n"
               "def take(xs: list[Int32]) -> Int32:\n"
               "    return len(xs)\n"
               "def f(g: dict[str, list[Int32]]) -> Int32:\n"
               "    return take(g[\"a\"])\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

    def test_for_head_position_routes(self):
        # A NATIVE-iterable container element (`for v in g["a"]:`) now
        # routes through the CONTAINER route's subscript leg -- the
        # begin/end capture the oracle demands (the historical hazard was
        # an iter-proto admission rendering the WRONG universal loop; the
        # container-route leg renders `auto& __obj_N = __getitem__(...)`
        # + begin/end, byte-verified).
        src = ("from tpy import Int32\n"
               "def f(g: dict[str, list[Int32]]) -> Int32:\n"
               "    total = 0\n"
               "    for v in g[\"a\"]:\n"
               "        total += v\n"
               "    return total\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)


class TestGateBoundaries:
    """The shapes the gate excludes.

    An unproven-Optional RECEIVER is NOT pinned here because the shape is
    unreachable: sema rejects subscripting a nullable container before
    lowering ever sees it, so the runtime-check guard the deleted per-sink
    bypasses skipped has no witness to assert on. A single slice at the len
    sink routes through the native slice-subscript arg row (its own row,
    not this gate); the double-slice exclusion below is the reject this
    boundary still decides.
    """

    def test_double_slice_still_rejects_at_the_len_sink(self):
        # The outer slice's RECEIVER is itself a slice result; the
        # slice-shape resolver takes a one-level receiver, so the shape
        # stays AST -- asserted by the EXACT fallback tally.
        src = ("from tpy import Int32\n"
               "def f(m: list[Int32]) -> Int32:\n"
               "    return len(m[0:4][0:2])\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.return:subscript.slice_shape")


class TestElemRowsDictSetFlavors:
    """The dict/set flavors of the wave-24 element rows: a set-typed
    element at a matching ref param, a dict element iterated at the
    for-head, and the named non-container reject (a str element loop
    stays out of the subscript leg)."""

    def test_set_elem_arg_and_dict_elem_iter_route(self):
        src = ("from tpy import Int32\n"
               "def grow(s: set[Int32], v: Int32) -> None:\n"
               "    s.add(v)\n"
               "def main() -> None:\n"
               "    g: dict[str, set[Int32]] = {\"a\": {1}}\n"
               "    grow(g[\"a\"], 2)\n"
               "    m: list[dict[str, Int32]] = [{\"k\": 3}]\n"
               "    for k in m[0]:\n"
               "        print(k)\n"
               "    print(len(g[\"a\"]))\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        _assert_byte_identical(src)
        assert 'grow(::tpy::__getitem__(g, "a"), 2);' in _body(thir, "main")

    def test_str_elem_loop_stays_out_of_the_leg(self):
        # A str ELEMENT char loop is not a container element -- the leg's
        # named reject (foreach.subscript_elem_family) falls it back.
        src = ("from tpy import Int32\n"
               "def main() -> None:\n"
               "    xs: list[str] = [\"ab\"]\n"
               "    for c in xs[0]:\n"
               "        print(c)\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.for_each:foreach.subscript_elem_family")


class TestLenCallArg:
    """`len(<str-returning call>)`: the call result is an rvalue rendered
    in place -- never a pointer-local, so the family restriction's
    pointer-local concern does not apply."""

    def test_str_call_args_route(self):
        from .testutil import _assert_routes_byte_identical
        src = ("from tpy import Int32\n"
               "def make_tag() -> str:\n"
               "    return \"hello\"\n"
               "class Dog:\n"
               "    name: str\n"
               "    def __init__(self, name: str) -> None:\n"
               "        self.name = name\n"
               "    def bark(self) -> str:\n"
               "        return self.name + \"!\"\n"
               "def f(d: Dog) -> Int32:\n"
               "    return len(d.bark()) + len(make_tag())\n"
               "def main() -> None:\n"
               "    print(f(Dog(\"rex\")))\n"
               "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "::tpy::__len__(d.bark())" in cpp
        assert "::tpy::__len__(make_tag())" in cpp

    def test_record_call_arg_rides_the_dunder_lane(self):
        # BOUNDARY: a non-str-family call result (a record with __len__)
        # is not this leg's family -- _is_len_call rejects it and the
        # shape rides the sema-resolved __len__ method-call lane instead.
        from ..compilation_context import activate_compiler
        from .lower import lower_module
        from .testutil import _compile, _entry
        src = ("from tpy import Int32, Own\n"
               "class Box:\n"
               "    def __init__(self) -> None:\n"
               "        pass\n"
               "    def __len__(self) -> Int32:\n"
               "        return 3\n"
               "def make_box() -> Own[Box]:\n"
               "    return Box()\n"
               "def f() -> Int32:\n"
               "    return len(make_box())\n"
               "f()\n")
        _assert_byte_identical(src)
        compiler, modules = _compile(src)
        entry = _entry(modules)
        from .lower.checks import _is_len_call
        import tpyc.parse.nodes as N
        fn = [x for x in entry.ast.functions if x.name == "f"][0]
        ret = fn.body[0]
        with activate_compiler(compiler):
            assert not _is_len_call(ret.value, {}, entry.analyzer)
