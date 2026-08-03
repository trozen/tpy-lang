"""Long-tail walrus rungs: the always-true truthy walrus (record slot +
enum), the enum first-decl walrus (the scalar row's enum family), and the
OWNED-tuple walrus off an `Own[tuple[..]]`-returning call (the deferred
optional slot; subscript reads are STORAGE -- dot access). The
branch-hoisted walrus with a sibling borrow binding stays AST."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
    _assert_routes_byte_identical,
)

_PAIR = (
    "from tpy import Int32, Own\n"
    "class Box:\n"
    "    val: Int32\n"
    "    def __init__(self, v: Int32) -> None:\n"
    "        self.val = v\n"
    "def make_pair(v: Int32) -> Own[tuple[Int32, Box]]:\n"
    "    return (v, Box(v))\n"
)


class TestWalrusLongTail:
    def test_always_true_walrus_routes(self):
        # `if (r := make(7)):` -- the ALWAYS_TRUE wrap over the owned-slot
        # walrus (`(r = make(..), *r)`) and the enum plain form.
        src = ("from enum import Enum\n"
               "from tpy import Own, Int32\n"
               "class Color(Enum):\n"
               "    RED = 1\n"
               "    GREEN = 2\n"
               "class Rec:\n"
               "    v: Int32\n"
               "    def __init__(self, v: Int32):\n"
               "        self.v = v\n"
               "def make(n: Int32) -> Own[Rec]:\n"
               "    return Rec(n)\n"
               "def pick() -> Color:\n"
               "    return Color.GREEN\n"
               "def main() -> None:\n"
               "    if (r := make(7)):\n"
               "        print(r.v)\n"
               "    if (c := pick()):\n"
               "        print(c.name)\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        # The record walrus rides the ALWAYS_TRUE wrap; the enum one flows
        # the enum-truthy mode arm (enum.truthy_plain) -- one fire each.
        assert faces.get("cond.walrus_always_true", 0) == 1
        assert faces.get("enum.truthy_plain", 0) == 1
        cpp = _assert_routes_byte_identical(src)
        assert "if ((static_cast<void>((r = make(7), *r)), true))" in cpp[1]
        assert "Color c;" in cpp[1]
        assert "if ((static_cast<void>((c = pick())), true))" in cpp[1]

    def test_owned_tuple_walrus_routes_storage_reads(self):
        # `(t := make_pair(5))[0]`: the deferred `std::optional<std::tuple>`
        # slot; element reads are STORAGE (`std::get<1>((*t)).val`, dot).
        src = (_PAIR +
               "def use() -> Int32:\n"
               "    if (t := make_pair(5))[0] > 0:\n"
               "        return t[0] + t[1].val\n"
               "    return 0\n"
               "def main() -> None:\n"
               "    print(use())\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert faces.get("expr.walrus_owned_slot", 0) == 1
        cpp = _assert_routes_byte_identical(src)
        assert "std::optional<std::tuple<int32_t, Box>> t;" in cpp[1]
        assert "std::get<0>((t = make_pair(5), *t))" in cpp[1]
        assert "std::get<1>((*t)).val" in cpp[1]

    def test_branch_hoisted_walrus_stays_ast(self):
        # A walrus target with a sibling-branch borrow binding (the
        # if.hoist_type family) keeps falling back.
        src = (_PAIR +
               "class Holder:\n"
               "    pair: tuple[Int32, Box]\n"
               "    def __init__(self) -> None:\n"
               "        self.pair = (1, Box(2))\n"
               "def use(h: Holder, c: bool) -> Int32:\n"
               "    if c:\n"
               "        if (t := make_pair(9))[0] > 0:\n"
               "            return t[1].val\n"
               "    else:\n"
               "        t = h.pair\n"
               "        return t[0]\n"
               "    return 0\n"
               "def main() -> None:\n"
               "    print(use(Holder(), True))\n"
               "main()\n")
        assert _fn(_lower_ctx(src), "use") is None
        _assert_byte_identical(src)

    # No reassigned-owned-tuple-walrus boundary pin: sema rejects walrus
    # reassignment of a non-value local outright ("use a separate
    # assignment statement"), so the owned-slot arm can never race the
    # rebind-slot arm on the same target.
