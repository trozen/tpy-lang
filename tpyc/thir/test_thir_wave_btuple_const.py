"""The borrow-tuple const fixpoint (`ensure_borrow_tuple_const`): a
reassigned ptr-repr tuple local's element pointers spell `const T*` when ANY
binding source reads const storage -- OR over all sources, to a fixpoint
over name chains, mirroring `_compute_borrow_tuple_const`. The decl and the
reseat lift both target the fixpoint verdict. Name-source and ternary INITS
keep rejecting (unwitnessed rows); the mutable inverse keeps the bare `T*`.
"""

from ..codegen_cpp import CodeGenOptions
from .testutil import (
    _reject_tally,
    _assert_byte_identical,
    _assert_routes_byte_identical,
    _compile,
    _entry,
)

_SRC = (
    "from tpy import Int32\n"
    "class Box:\n"
    "    val: Int32\n"
    "    def __init__(self, val: Int32) -> None:\n        self.val = val\n"
    "class Holder:\n"
    "    pair: tuple[Int32, Box]\n"
    "    def __init__(self, b: Box) -> None:\n"
    "        self.pair = (1, b)\n"
)


class TestBtupleConstFixpoint:
    SRC = (
        _SRC
        + "def one_const_source(h: Holder, h2: Holder) -> Int32:\n"
        + "    t = h.pair\n"
        + "    t = h2.pair\n"
        + "    return t[1].val\n"
        + "def mutable_stays(h: Holder, h2: Holder) -> Int32:\n"
        + "    h.pair[1].val = 5\n"
        + "    h2.pair[1].val = 6\n"
        + "    t = h.pair\n"
        + "    t = h2.pair\n"
        + "    return t[1].val\n"
        + "def main() -> None:\n"
        + "    print(one_const_source(Holder(Box(1)), Holder(Box(2))))\n"
        + "    print(mutable_stays(Holder(Box(7)), Holder(Box(8))))\n"
        + "main()\n"
    )

    def test_routes_byte_identical(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC, comments=False)
        # Const-inferred receivers flip the decl AND both lifts to const.
        assert ("std::tuple<int32_t, const Box*> t = "
                "::tpy::tuple_to_pointer<std::tuple<int32_t, const Box*>>"
                "(h.pair);") in cpp
        # The mutated sibling keeps the bare pointers.
        assert ("std::tuple<int32_t, Box*> t = "
                "::tpy::tuple_to_pointer<std::tuple<int32_t, Box*>>"
                "(h.pair);") in cpp


class TestUnwitnessedInitShapesStayFenced:
    # Name-source (`u = t`) and ternary inits of a reassigned borrow-tuple
    # local have no mirrored row -- the body falls back byte-identically.
    SRC = (
        _SRC
        + "def name_chain(h: Holder, h2: Holder) -> Int32:\n"
        + "    t = h.pair\n"
        + "    t = h2.pair\n"
        + "    u = t\n"
        + "    u = h2.pair\n"
        + "    return u[1].val\n"
        + "def main() -> None:\n"
        + "    print(name_chain(Holder(Box(3)), Holder(Box(4))))\n"
        + "main()\n"
    )

    def test_name_source_init_stays_ast(self):
        assert any((k.startswith('body:stmt.var_decl') for k in _reject_tally(self.SRC))), _reject_tally(self.SRC)
