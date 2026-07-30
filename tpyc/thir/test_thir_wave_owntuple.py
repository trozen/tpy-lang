"""Own-record tuple wave arms: the `tuple[Own[A], Own[B]]` storage decl
from a call, the name-source unpack holder binds (last-use move vs copy),
and the `t[N].field` value read off the stored element."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _fn, _assert_byte_identical,
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
        assert _fn(_lower_ctx(src), "main") is None
        _assert_byte_identical(src)

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
        assert _fn(_lower_ctx(src), "main") is None
        _assert_byte_identical(src)
