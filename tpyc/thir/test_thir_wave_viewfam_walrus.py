"""The owned str/bytes walrus target at its FIRST binding: the bare owned
slot on the temp sink's named row plus the in-place assign. A target that
resolved VIEW form is a different pre-declaration and keeps rejecting."""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical, _assert_rejects_at, _assert_routes_byte_identical,
    _lower_ctx_witnessed, _thir_ctx,
)


class TestOwnedViewfamWalrusDecl:

    def test_owned_str_first_decl_routes(self):
        # The socket sockaddr shape: a view param bound to an owned str
        # target inside an if-condition.
        src = ("from tpy import Int32\n"
               "def f(host: str) -> Int32:\n"
               "    if len(hostname := host) == 0:\n"
               "        return 0\n"
               "    return len(hostname)\n")
        _assert_routes_byte_identical(src)
        _, faces = _lower_ctx_witnessed(src)
        assert faces.get("expr.walrus_owned_viewfam", 0) == 1

    def test_owned_bytes_first_decl_routes(self):
        # The bytes half of the same family: `std::vector<uint8_t> data;`.
        src = ("from tpy import Int32\n"
               "def f(b: bytes) -> Int32:\n"
               "    if len(data := b + b\"!\") == 0:\n"
               "        return 0\n"
               "    return len(data)\n")
        _assert_routes_byte_identical(src)

    def test_while_head_first_decl_routes(self):
        # A while-condition first decl: the named pre-decl flushes BEFORE
        # the loop, so the binding survives the loop it is read after.
        src = ("from tpy import Int32\n"
               "def f(s: str) -> Int32:\n"
               "    n = 0\n"
               "    while len(cur := s + \"x\") > n:\n"
               "        n += 1\n"
               "        if n > 3:\n"
               "            break\n"
               "    return n + len(cur)\n")
        _assert_routes_byte_identical(src)

    def test_sibling_branch_rebind_declares_once(self):
        # The pre-decl registry is FUNCTION-scoped: the second branch's
        # occurrence assigns in place rather than shadowing the first.
        src = ("from tpy import Int32\n"
               "def f(s: str, k: Int32) -> Int32:\n"
               "    if k > 0:\n"
               "        if len(t := s + \"a\") == 0:\n"
               "            return 0\n"
               "    else:\n"
               "        if len(t := s + \"b\") == 0:\n"
               "            return 1\n"
               "    return len(t)\n")
        cpp = _assert_routes_byte_identical(src)[1]
        assert cpp.count("std::string t;") == 1

    def test_view_form_target_stays_ast(self):
        # A target whose binding resolved VIEW form (`std::string_view t;`)
        # needs the pending-view pre-decl, which is not mirrored.
        src = ("from tpy import Int32, StrView\n"
               "def f(s: StrView) -> Int32:\n"
               "    if len(t := s) == 0:\n"
               "        return 0\n"
               "    return len(t)\n")
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:expr.walrus")
        _assert_byte_identical(src)
