"""Pins for the subscript receiver-family rows: protocol-typed
template-param receivers (the shared checked __getitem__), varargs views
with range-proven indexes, and Own[container] param reads -- plus the
unproven-index and write boundaries."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _assert_byte_identical,
    _fn,
    _lower_ctx,
    _lower_ctx_witnessed,
)

_PRELUDE = "from tpy import Int32, Own\n"


class TestProtocolReceiverSubscript:
    def test_sequence_receiver_routes_checked(self):
        src = _PRELUDE + (
            "from typing import Sequence\n"
            "from tpy import Char\n"
            "def first(s: Sequence[Char]) -> Char:\n"
            "    return s[0]\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "first") is not None
        assert faces["subscript.protocol_recv"] >= 1
        _assert_byte_identical(src)

    def test_variable_index_routes_checked(self):
        src = _PRELUDE + (
            "from typing import Sequence\n"
            "def pick(s: Sequence[Int32], i: Int32) -> Int32:\n"
            "    return s[i]\n"
        )
        assert _fn(_lower_ctx(src), "pick") is not None
        _assert_byte_identical(src)


class TestVarargsSubscript:
    def test_proven_index_routes_raw(self):
        src = _PRELUDE + (
            "def total(*args: Int32) -> Int32:\n"
            "    s = 0\n"
            "    for i in range(len(args)):\n"
            "        s = s + args[i]\n"
            "    return s\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "total") is not None
        assert faces["subscript.varargs_recv"] >= 1
        _assert_byte_identical(src)

    def test_unproven_index_takes_the_checked_read(self):
        # Without a range proof there is no bounds-safe RAW render -- but the
        # emit does not need one: it falls through to the checked
        # `::tpy::__getitem__(args, i)`, the same branch a container receiver
        # takes. The gate used to demand the proof and reject here; the emit
        # picks the form off `bounds_safe` instead.
        src = _PRELUDE + (
            "def tail(*args: Int32) -> Int32:\n"
            "    n: Int32 = len(args)\n"
            "    if n > 0:\n"
            "        return args[n - 1]\n"
            "    return -1\n"
        )
        _assert_byte_identical(src)
        assert _fn(_lower_ctx(src), "tail") is not None


class TestOwnContainerReceiver:
    def test_own_list_param_read_routes(self):
        src = _PRELUDE + (
            "def first_val[T](items: Own[list[T]]) -> T:\n"
            "    return items[0]\n"
        )
        assert _fn(_lower_ctx(src), "first_val") is not None
        _assert_byte_identical(src)

    def test_own_list_write_stays_ast(self):
        # The Own receiver admission is READ-only; the setitem family keeps
        # its Own exclusion.
        src = _PRELUDE + (
            "def bump(items: Own[list[Int32]]) -> Int32:\n"
            "    items[0] = 9\n"
            "    return items[0]\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.assign:setitem.family")
