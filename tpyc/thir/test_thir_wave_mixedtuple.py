"""Mixed owned+borrow tuple (`tuple[Own[A], A]`) at the single-binding
`auto` alias decl: the BORROW-form call result binds whole (owned elements
by value, ref elements as pointers) and reads pick `.` vs `->` per element.
The REASSIGNED mixed local rebinds through a PLAIN assign (same form, no
lift); the mixed unpack stays on the AST path."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
    _assert_rejects_at,
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

    def test_mixed_reassigned_local_reseats_plainly(self):
        # The reassigned mixed local declares ONE fixed slot type (the AST's
        # const-borrow-form fixpoint) and every rebind is a PLAIN assign --
        # the hybrid render IS the local's form, so there is no lift and no
        # rebind slot, unlike the all-Own tuple's emplace reseat.
        src = (_HDR +
               "def main() -> None:\n"
               "    c = Cell(7)\n"
               "    d = Cell(9)\n"
               "    m = Maker()\n"
               "    x = m.mixed(c)\n"
               "    x = m.mixed(d)\n"
               "    print(x[0].val, x[1].val)\n"
               "main()\n")
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "std::tuple<Cell, Cell*> x = m.mixed(c);" in out
        assert "x = m.mixed(d);" in out
        assert "std::get<0>(x).val" in out
        assert "std::get<1>(x)->val" in out
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["btuple.reseat_mixed_call"] >= 1

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


class TestMixedOwnTupleFrameSlot:
    """The RESUMABLE flavor: a mixed tuple local in a frame is an OWNING
    `frame_slot<std::tuple<A, B*>>` (MIXED_TUPLE_SLOT) -- the write
    emplaces the mixed-returning call, element reads split `.`/`->` off
    the deref'd slot, keyed on the ELEMENT types exactly like the sync
    alias's chooser."""

    _GEN = (
        "from typing import Iterator\n"
        "from tpy import Int32, Own\n"
        "class Box:\n"
        "    val: Int32\n"
        "    def __init__(self, val: Int32) -> None:\n"
        "        self.val = val\n"
        "def make_mixed(b: Box) -> tuple[Own[Box], Box]:\n"
        "    return (Box(1), b)\n")

    def test_frame_mixed_slot_routes(self):
        src = (self._GEN
               + "def gen(b: Box) -> Iterator[Int32]:\n"
               + "    p = make_mixed(b)\n"
               + "    p[1].val = 88\n"
               + "    yield p[0].val\n"
               + "    yield p[1].val\n"
               + "def main() -> None:\n"
               + "    b = Box(7)\n"
               + "    for v in gen(b):\n"
               + "        print(v)\n"
               + "    print(b.val)\n"
               + "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "p.emplace(make_mixed(b));" in cpp
        assert "std::get<1>((*p))->val = 88;" in cpp
        assert "return std::get<0>((*p)).val;" in cpp

    def test_frame_own_pair_emplace_routes(self):
        # The per-element-own (non-mixed) neighbour rides the same
        # tuple_source emplace admission.
        src = (self._GEN
               + "def make_pair() -> tuple[Own[Box], Own[Box]]:\n"
               + "    return (Box(1), Box(2))\n"
               + "def gen() -> Iterator[Int32]:\n"
               + "    p = make_pair()\n"
               + "    yield p[0].val\n"
               + "    yield p[1].val\n"
               + "def main() -> None:\n"
               + "    for v in gen():\n"
               + "        print(v)\n"
               + "main()\n")
        _assert_routes_byte_identical(src)

    def test_frame_mixed_whole_pass_stays_ast(self):
        # BOUNDARY: the mixed slot passed WHOLE binds the hybrid render
        # verbatim on the AST (`show((*p))`); the storage move/lift/decay
        # rows must not capture a MIXED slot (a tuple_to_storage wrap here
        # was a dualgen-caught divergence), so the body falls back.
        from .testutil import _thir_ctx
        src = (self._GEN
               + "def show(t: tuple[Own[Box], Box]) -> Int32:\n"
               + "    return t[0].val + t[1].val\n"
               + "def gen(b: Box) -> Iterator[Int32]:\n"
               + "    p = make_mixed(b)\n"
               + "    yield show(p)\n"
               + "    yield p[1].val\n"
               + "def main() -> None:\n"
               + "    b = Box(7)\n"
               + "    for v in gen(b):\n"
               + "        print(v)\n"
               + "main()\n")
        _ctx, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "resumable:expr.call",
                           "call.arg_shape.tuple")
        _assert_byte_identical(src)
