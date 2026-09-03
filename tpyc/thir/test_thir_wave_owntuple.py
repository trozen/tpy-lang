"""Own-record tuple wave arms: the `tuple[Own[A], Own[B]]` storage decl
from a call, the name-source unpack holder binds (last-use move vs copy),
the `t[N].field` value read off the stored element, and the MIXED
borrow+Own call source (`ref, owned = split(p)`)."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _lower_ctx, _fn, _assert_byte_identical,
    _assert_routes_byte_identical, _lower_ctx_witnessed,
)

_PAIR = (
    "from tpy import Own, nocopy, Int32\n"
    "@nocopy\n"
    "class Counter:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
    "def make_pair() -> tuple[Own[Counter], Own[Counter]]:\n"
    "    return (Counter(1), Counter(2))\n"
)

_MIX = (
    "from tpy import Own, Int32, copy\n"
    "class Point:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n"
    "        self.x = x\n"
    "def split(p: Point) -> tuple[Point, Own[Point]]:\n"
    "    return (p, copy(p))\n"
)

_MIX_MAIN = (
    "def main() -> None:\n"
    "    p = Point(Int32(1))\n"
    "    ref, owned = split(p)\n"
    "    p.x = Int32(9)\n"
    "    print(ref.x, owned.x)\n"
    "main()\n"
)


class TestOwnRecordTuple:
    def test_call_decl_and_last_use_move_unpack(self):
        # `t = make_pair()` is the plain spelled copy; the unpack at t's
        # last use moves the holder and each element out.
        src = (_PAIR
               + "def main() -> None:\n"
               + "    t = make_pair()\n"
               + "    a, b = t\n"
               + "    a.n += 10\n"
               + "    print(a.n, b.n)\n"
               + "main()\n")
        assert _fn(_lower_ctx(src), "main") is not None
        cpp = _assert_byte_identical(src)
        assert "std::tuple<Counter, Counter> t = make_pair();" in cpp[1]
        assert "auto&& __tup_1 = std::move(t);" in cpp[1]
        assert "Counter a = std::move(std::get<0>(__tup_1));" in cpp[1]

    def test_not_last_use_copies_holder(self):
        # t is read again after the unpack, so the holder COPIES
        # (`auto __tup_1 = t;`) and the source elements stay live.
        src = ("from tpy import Own, Int32\n"
               "class Box:\n"
               "    val: Int32\n"
               "    def __init__(self, v: Int32) -> None:\n"
               "        self.val = v\n"
               "def make() -> tuple[Own[Box], Own[Box]]:\n"
               "    return (Box(1), Box(2))\n"
               "def main() -> None:\n"
               "    t = make()\n"
               "    a, b = t\n"
               "    print(a.val + b.val)\n"
               "    print(t[0].val + t[1].val)\n"
               "main()\n")
        assert _fn(_lower_ctx(src), "main") is not None
        cpp = _assert_byte_identical(src)
        assert "auto __tup_1 = t;" in cpp[1]
        assert "std::get<0>(t).val" in cpp[1]

    def test_nested_own_tuple_chain_read_stays_ast(self):
        # Nested own-tuple elements are narrowed OUT of the family (no
        # routable consumer exists until the tuple-over-tuple chain read
        # lands), so the decl rejects and the body keeps falling back.
        src = ("from tpy import Own, Int32\n"
               "class Handle:\n"
               "    fd: Int32\n"
               "    def __init__(self, fd: Int32) -> None:\n"
               "        self.fd = fd\n"
               "def two() -> tuple[tuple[Own[Handle], Own[Handle]], Own[Handle]]:\n"
               "    return ((Handle(1), Handle(2)), Handle(3))\n"
               "def main() -> None:\n"
               "    pp = two()\n"
               "    print(pp[0][0].fd)\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.var_decl:decl.slot_type")

    def test_mixed_borrow_own_call_source_routes(self):
        # `split(p) -> tuple[Point, Own[Point]]` returns
        # `std::tuple<Point*, Point>`: the plain rvalue capture serves both
        # slot shapes -- the ref target aliases the pointer, the move target
        # drains the by-value slot. No tuple_to_pointer lift.
        cpp = _assert_routes_byte_identical(_MIX + _MIX_MAIN)
        assert "auto __tup_1 = split(p);" in cpp[1]
        assert ("auto&& ref = ::tpy::unwrap_ref(::tpy::tuple_elem_ref("
                "std::get<0>(__tup_1)));") in cpp[1]
        assert "Point owned = std::move(std::get<1>(__tup_1));" in cpp[1]

    def test_mixed_source_witnesses_its_admission_face(self):
        # The admission is the only distinguishing site (the renders are the
        # shared ref/move ones), so the face is what proves the new leg fired.
        thir, faces = _lower_ctx_witnessed(_MIX + _MIX_MAIN)
        assert _fn(thir, "main") is not None
        assert faces.get("stmt.tuple_unpack.own_ref_mix_source") == 1

    def test_mixed_source_with_scalar_and_str_elements_routes(self):
        # Value scalars and strs sit alongside the borrow/Own pair unchanged.
        src = (_MIX
               + "def split3(p: Point) -> tuple[Point, Own[Point], Int32, str]:\n"
               + "    return (p, copy(p), Int32(7), 'hi')\n"
               + "def main() -> None:\n"
               + "    p = Point(Int32(1))\n"
               + "    ref, owned, n, s = split3(p)\n"
               + "    p.x = Int32(9)\n"
               + "    print(ref.x, owned.x, n, s)\n"
               + "main()\n")
        cpp = _assert_routes_byte_identical(src)
        assert "int32_t n = std::get<2>(__tup_1);" in cpp[1]

    def test_mixed_source_discarded_targets_route(self):
        # A `_` slot emits nothing on either side of the mix.
        src = (_MIX
               + "def main() -> None:\n"
               + "    p = Point(Int32(1))\n"
               + "    _, owned = split(p)\n"
               + "    ref, _ = split(p)\n"
               + "    p.x = Int32(9)\n"
               + "    print(owned.x, ref.x)\n"
               + "main()\n")
        _assert_routes_byte_identical(src)

    def test_mixed_method_call_source_stays_ast(self):
        # The mix is admitted at the FREE-call source only; a method source
        # rejects at the method-result gate (no witness for that spelling).
        src = (_MIX
               + "class Holder:\n"
               + "    p: Point\n"
               + "    def __init__(self, p: Point) -> None:\n"
               + "        self.p = p\n"
               + "    def split(self) -> tuple[Point, Own[Point]]:\n"
               + "        return (self.p, copy(self.p))\n"
               + "def main() -> None:\n"
               + "    h = Holder(Point(Int32(1)))\n"
               + "    ref, owned = h.split()\n"
               + "    print(ref.x, owned.x)\n"
               + "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.method_call:method.ret_type")

    def test_mixed_own_union_element_stays_ast(self):
        # An `Own[union]` element needs a value-variant -> ptr-variant bind
        # the unpack does not carry: the source keeps rejecting.
        src = (_MIX
               + "class Cell:\n"
               + "    n: Int32\n"
               + "    def __init__(self, n: Int32) -> None:\n"
               + "        self.n = n\n"
               + "def split_union(p: Point) -> tuple[Point, Own[Point | Cell]]:\n"
               + "    u: Point | Cell = copy(p)\n"
               + "    return (p, u)\n"
               + "def main() -> None:\n"
               + "    p = Point(Int32(1))\n"
               + "    ref, owned = split_union(p)\n"
               + "    print(ref.x)\n"
               + "    if isinstance(owned, Point):\n"
               + "        print(owned.x)\n"
               + "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.return:return.slot_type")

    def test_mixed_tuple_name_source_stays_ast(self):
        # A NAME holding the mixed tuple is a STORAGE local, whose holder
        # bind (move vs copy) and element forms differ from the call
        # capture's -- only the call source is admitted.
        src = (_MIX
               + "def main() -> None:\n"
               + "    p = Point(Int32(1))\n"
               + "    t = split(p)\n"
               + "    ref, owned = t\n"
               + "    print(ref.x, owned.x)\n"
               + "main()\n")
        _assert_rejects_at(_reject_tally(src), "body:stmt.tuple_unpack")

    def test_own_tuple_ternary_source_stays_ast(self):
        # A TERNARY source of an Own-element tuple is outside the
        # name/call source slice -- the unpack keeps rejecting it.
        src = ("from tpy import Own, Int32\n"
               "class Box:\n"
               "    val: Int32\n"
               "    def __init__(self, v: Int32) -> None:\n"
               "        self.val = v\n"
               "def make(v: Int32) -> tuple[Own[Box], Own[Box]]:\n"
               "    return (Box(v), Box(v))\n"
               "def main() -> None:\n"
               "    t1 = make(1)\n"
               "    t2 = make(2)\n"
               "    a, b = t1 if len('x') == 1 else t2\n"
               "    print(a.val, b.val)\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src), "body:stmt.tuple_unpack")
