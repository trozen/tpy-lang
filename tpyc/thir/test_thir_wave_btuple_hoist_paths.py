"""Pins for the three borrow-form tuple hoist/bind paths a MIXED own+borrow
tuple local takes besides a straight decl: an INNER-scope if cascade (the
predecl lands inside the enclosing block, where the branch-scoped registries
pop with it), a try/except (the predecl lands at the try), and a walrus whose
source is a mixed own+borrow CALL. Boundaries: sema places a hoist at the
OUTERMOST block that reads the name (so an inner-if hoist never outlives its
block), an OWNING tuple-call source keeps rejecting at the try (its reseat
needs a chain-head rebind slot), and a plain all-borrow call source keeps
rejecting at the walrus."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _assert_byte_identical,
    _assert_routes_byte_identical,
    _fn,
    _lower_ctx,
    _lower_ctx_witnessed,
)

_HDR = (
    "from tpy import Int32, Own\n"
    "class Box:\n"
    "    val: Int32\n"
    "    def __init__(self, val: Int32) -> None:\n"
    "        self.val = val\n"
    "def make_mixed(b: Box) -> tuple[Own[Box], Box]:\n"
    "    return (Box(1), b)\n"
    "def make_own(v: Int32) -> tuple[Own[Box], Own[Box]]:\n"
    "    return (Box(v), Box(v + 1))\n"
    "def make_borrow(b: Box, c: Box) -> tuple[Box, Box]:\n"
    "    return (b, c)\n"
)


class TestInnerScopeBorrowTupleHoist:
    def test_inner_if_in_for_mixed_own_hoist_routes(self):
        src = _HDR + (
            "def f(b: Box, c: Box) -> Int32:\n"
            "    total = 0\n"
            "    for i in range(2):\n"
            "        if i == 0:\n"
            "            p = make_mixed(b)\n"
            "        else:\n"
            "            p = make_mixed(c)\n"
            "        p[1].val = 44 + i\n"
            "        total = total + p[0].val\n"
            "    return total\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert faces["if.hoist_mixed_own_tuple"] >= 1
        _assert_routes_byte_identical(src)

    def test_post_loop_same_name_decl_classifies_fresh(self):
        # The branch-scoped registries and the `declared` copy pop with the
        # enclosing block, so a same-named binding AFTER the loop is a plain
        # first decl (`std::tuple<Box, Box*> p = make_mixed(c);`) -- the AST
        # emits exactly that, which is what makes the inner-scope predecl
        # safe.
        src = _HDR + (
            "def f(b: Box, c: Box) -> Int32:\n"
            "    for i in range(2):\n"
            "        if i == 0:\n"
            "            p = make_mixed(b)\n"
            "        else:\n"
            "            p = make_mixed(c)\n"
            "        p[1].val = 30 + i\n"
            "    p = make_mixed(c)\n"
            "    p[1].val = 31\n"
            "    return p[0].val\n"
        )
        _assert_routes_byte_identical(src)

    def test_post_loop_read_hoists_at_the_for_not_the_if(self):
        # The scope rule the inner-if row relies on: a read AFTER the loop
        # puts the predecl on the FOR, so the inner if never sees the name
        # (the for-each hoist family owns that shape and still defers).
        src = _HDR + (
            "def f(b: Box, c: Box) -> Int32:\n"
            "    for i in range(2):\n"
            "        if i == 0:\n"
            "            p = make_mixed(b)\n"
            "        else:\n"
            "            p = make_mixed(c)\n"
            "        p[1].val = 44 + i\n"
            "    return p[0].val\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.for_each:foreach.hoist_type")


class TestTryBorrowTupleHoist:
    def test_try_mixed_own_hoist_routes(self):
        src = _HDR + (
            "def f(b: Box) -> Int32:\n"
            "    try:\n"
            "        p = make_mixed(b)\n"
            "    except ValueError:\n"
            "        p = make_mixed(b)\n"
            "    p[1].val = 66\n"
            "    return p[0].val\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert faces["try.hoist_borrow_tuple"] >= 1
        assert faces["try.hoist_mixed_own_tuple"] >= 1
        _assert_routes_byte_identical(src)

    def test_try_borrow_literal_hoist_routes(self):
        src = _HDR + (
            "def f(b: Box, c: Box) -> Int32:\n"
            "    try:\n"
            "        p = (b, c)\n"
            "    except ValueError:\n"
            "        p = (c, b)\n"
            "    p[0].val = 41\n"
            "    return p[1].val\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert faces["try.hoist_borrow_tuple"] >= 1
        assert "try.hoist_mixed_own_tuple" not in faces
        _assert_routes_byte_identical(src)

    def test_try_inside_loop_mixed_hoist_routes(self):
        src = _HDR + (
            "def f(b: Box, c: Box) -> Int32:\n"
            "    total = 0\n"
            "    for i in range(2):\n"
            "        try:\n"
            "            p = make_mixed(b)\n"
            "        except ValueError:\n"
            "            p = make_mixed(c)\n"
            "        p[1].val = 50 + i\n"
            "        total = total + p[0].val\n"
            "    return total\n"
        )
        _assert_routes_byte_identical(src)

    def test_try_owning_tuple_call_source_still_defers(self):
        # An OWNING tuple-call source reseats through a chain-head rebind
        # slot; the try predecl has nowhere to put one, so the row keeps
        # `owning_call_ok=False` and the body stays AST.
        src = _HDR + (
            "def f(v: Int32) -> Int32:\n"
            "    try:\n"
            "        t = make_own(v)\n"
            "    except ValueError:\n"
            "        t = make_own(v + 1)\n"
            "    return t[0].val + t[1].val\n"
        )
        _assert_rejects_at(_reject_tally(src), "body:stmt.try:try.hoist")


class TestWalrusMixedOwnCallSource:
    def test_walrus_mixed_call_source_routes(self):
        src = _HDR + (
            "def f(b: Box) -> Int32:\n"
            "    if (p := make_mixed(b))[0].val > 0:\n"
            "        p[1].val = 77\n"
            "    return p[0].val\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert faces["expr.walrus_btuple_mixed_call"] >= 1
        _assert_routes_byte_identical(src)

    def test_walrus_mixed_method_call_source_routes(self):
        src = _HDR + (
            "class Maker:\n"
            "    seed: Int32\n"
            "    def __init__(self, seed: Int32) -> None:\n"
            "        self.seed = seed\n"
            "    def make(self, b: Box) -> tuple[Own[Box], Box]:\n"
            "        return (Box(self.seed), b)\n"
            "def f(m: Maker, b: Box) -> Int32:\n"
            "    if (p := m.make(b))[0].val > 0:\n"
            "        p[1].val = 71\n"
            "    return p[0].val\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert faces["expr.walrus_btuple_mixed_call"] >= 1
        _assert_routes_byte_identical(src)

    def test_walrus_plain_borrow_call_source_still_defers(self):
        # An all-BORROW tuple-returning call is not the mixed render: its
        # result is a borrow tuple the AST binds through a different rung,
        # so the walrus source row keeps rejecting it.
        src = _HDR + (
            "def f(b: Box, c: Box) -> Int32:\n"
            "    if (t := make_borrow(b, c))[0].val > 0:\n"
            "        t[1].val = 73\n"
            "    return t[0].val\n"
        )
        _assert_rejects_at(_reject_tally(src), "body:expr.walrus")
