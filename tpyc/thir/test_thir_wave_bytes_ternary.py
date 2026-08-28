"""A bytes ternary whose BOTH arms are owned-bytes-returning CALLS: the
arms render bare and the whole ternary is owned storage, so the owned sink
adds no `bytes_copy`. Arms that are runtime spans keep deferring."""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical, _assert_rejects_at, _assert_routes_byte_identical,
    _lower_ctx_witnessed, _thir_ctx,
)

_SRC = ("from tpy import Int32\n"
        "class Src:\n"
        "    def all(self) -> bytes:\n"
        "        return b\"abc\"\n"
        "    def some(self, n: Int32) -> bytes:\n"
        "        return b\"ab\"\n")


class TestBytesOwnedCallTernary:

    def test_owned_call_arms_route(self):
        # The http.client read() shape: a length-keyed pick between two
        # owned-bytes reads, bound to an owned local.
        src = (_SRC
               + "def g(s: Src, n: Int32) -> Int32:\n"
               + "    data = s.all() if n < 0 else s.some(n)\n"
               + "    return len(data)\n")
        cpp = _assert_routes_byte_identical(src)[1]
        assert "bytes_copy" not in cpp
        _, faces = _lower_ctx_witnessed(src)
        assert faces.get("ifexpr.bytes_owned_calls", 0) == 1

    def test_free_call_arms_route(self):
        # The free-function flavor of the same pair.
        src = ("from tpy import Int32\n"
               "def mk(n: Int32) -> bytes:\n"
               "    return b\"ab\"\n"
               "def mk2(n: Int32) -> bytes:\n"
               "    return b\"cd\"\n"
               "def g(n: Int32) -> Int32:\n"
               "    data = mk(n) if n < 0 else mk2(n)\n"
               "    return len(data)\n")
        _assert_routes_byte_identical(src)

    def test_owned_call_arms_at_return_and_arg_sinks(self):
        # The other two owned sinks the copy wrap would have fired at.
        src = (_SRC
               + "def take(b: bytes) -> Int32:\n"
               + "    return len(b)\n"
               + "def fr(s: Src, n: Int32) -> bytes:\n"
               + "    return s.all() if n < 0 else s.some(n)\n"
               + "def fg(s: Src, n: Int32) -> Int32:\n"
               + "    return take(s.all() if n < 0 else s.some(n))\n")
        cpp = _assert_routes_byte_identical(src)[1]
        assert "bytes_copy" not in cpp

    def test_owned_call_arms_at_container_element(self):
        src = (_SRC
               + "def fa(s: Src, n: Int32) -> Int32:\n"
               + "    xs = [s.all() if n < 0 else s.some(n)]\n"
               + "    return len(xs)\n")
        _assert_routes_byte_identical(src)

    def test_view_param_arm_stays_ast(self):
        # A bytes PARAM arm is a runtime span, so the pair is not owned and
        # the mixed per-arm materialization is still unmirrored.
        src = (_SRC
               + "def g(s: Src, b: bytes, n: Int32) -> Int32:\n"
               + "    data = s.all() if n < 0 else b\n"
               + "    return len(data)\n")
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:ifexpr.bytes_mixed")
        _assert_byte_identical(src)

    def test_bytes_literal_arm_stays_ast(self):
        # A bytes literal renders `bytes_literal_owned(..)`, a different arm
        # shape from a call rvalue -- it keeps deferring.
        src = (_SRC
               + "def g(s: Src, n: Int32) -> Int32:\n"
               + "    data = s.all() if n < 0 else b\"zz\"\n"
               + "    return len(data)\n")
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:ifexpr.bytes_mixed")
        _assert_byte_identical(src)

    def test_view_returning_call_arms_stay_span(self):
        # Both arms VIEW-returning calls: the ternary IS a span, so the
        # owned sink must keep copying -- the new leg must not claim it.
        src = ("from tpy import Int32, BytesView\n"
               "class Src:\n"
               "    def v1(self, b: bytes) -> BytesView:\n"
               "        return b\n"
               "    def v2(self, b: bytes) -> BytesView:\n"
               "        return b\n"
               "def g(s: Src, b: bytes, n: Int32) -> Int32:\n"
               "    data = s.v1(b) if n < 0 else s.v2(b)\n"
               "    return len(data)\n")
        cpp = _assert_routes_byte_identical(src)[1]
        assert "std::span<const uint8_t> data" in cpp
        _, faces = _lower_ctx_witnessed(src)
        assert "ifexpr.bytes_owned_calls" not in faces
