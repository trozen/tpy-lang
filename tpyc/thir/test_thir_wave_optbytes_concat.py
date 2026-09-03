"""A NARROWED value-repr `Optional[bytes]` binding at a bytes-CONCAT
operand: the deref feeds `bytes_concat` exactly as the str flavor feeds
`str_concat`. The repeat arm and un-narrowed reads keep rejecting."""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical, _assert_rejects_at, _assert_routes_byte_identical,
    _lower_ctx_witnessed, _thir_ctx,
)

_H = ("from tpy import Int32\n"
      "class H:\n"
      "    v: Int32\n"
      "    def __init__(self) -> None:\n"
      "        self.v = 0\n"
      "    def getb(self, k: str) -> bytes | None:\n"
      "        if self.v > 0:\n"
      "            return b\"x\"\n"
      "        return None\n")


class TestNarrowedOptBytesConcat:

    def test_narrowed_param_concat_routes(self):
        # The http.client _build_request shape.
        src = ("from tpy import Int32\n"
               "def build(body: bytes | None) -> bytes:\n"
               "    data = b\"head\"\n"
               "    if body is not None:\n"
               "        data = data + body\n"
               "    return data\n"
               "print(len(build(b\"x\")))\n")
        cpp = _assert_routes_byte_identical(src)[1]
        assert "::tpy::bytes_concat(data, (*body))" in cpp
        _, faces = _lower_ctx_witnessed(src)
        assert faces.get("binop.opt_view_narrowed", 0) == 1

    def test_narrowed_operand_on_the_left_routes(self):
        src = ("from tpy import Int32\n"
               "def build(body: bytes | None) -> bytes:\n"
               "    data = b\"head\"\n"
               "    if body is not None:\n"
               "        data = body + data\n"
               "    return data\n"
               "print(len(build(b\"x\")))\n")
        cpp = _assert_routes_byte_identical(src)[1]
        assert "::tpy::bytes_concat((*body), data)" in cpp

    def test_narrowed_local_concat_routes(self):
        # A registered LOCAL binding: the deref is owned storage, not a span.
        src = (_H
               + "def build(h: H) -> bytes:\n"
               + "    data = b\"head\"\n"
               + "    b = h.getb(\"k\")\n"
               + "    if b is not None:\n"
               + "        data = data + b\n"
               + "    return data\n"
               + "print(len(build(H())))\n")
        _assert_routes_byte_identical(src)

    def test_str_concat_sibling_still_routes(self):
        # The landed str row the bytes leg was modelled on.
        src = ("from tpy import Int32\n"
               "def build(s: str | None) -> str:\n"
               "    data = \"head\"\n"
               "    if s is not None:\n"
               "        data = data + s\n"
               "    return data\n"
               "print(len(build(\"x\")))\n")
        _assert_routes_byte_identical(src)

    def test_unnarrowed_bytes_concat_stays_ast(self):
        src = ("from tpy import Int32\n"
               "def build(body: bytes | None) -> Int32:\n"
               "    data = b\"head\"\n"
               "    data = data + body\n"
               "    return len(data)\n"
               "print(build(b\"x\"))\n")
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:stmt.var_decl")

    def test_narrowed_bytes_repeat_stays_ast(self):
        # `*` is the repeat arm, which pins its bytes side through the
        # resolved dunder's receiver slot -- the narrow legs are concat-only.
        src = ("from tpy import Int32\n"
               "def build(body: bytes | None, n: Int32) -> Int32:\n"
               "    if body is not None:\n"
               "        return len(body * n)\n"
               "    return 0\n"
               "print(build(b\"x\", 2))\n")
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:stmt.return")
