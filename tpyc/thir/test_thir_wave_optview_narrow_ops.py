"""A NARROWED value-repr `Optional[str]` binding as a COMPARE operand: its
read is the `(*ka)` deref both paths spell, so the gate judges the inner str
rather than the declared Optional. The bytes flavor of the same deref has no
witnessed compare render and keeps rejecting."""

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
      "    def get(self, k: str) -> str | None:\n"
      "        if self.v > 0:\n"
      "            return \"x\"\n"
      "        return None\n"
      "    def getb(self, k: str) -> bytes | None:\n"
      "        if self.v > 0:\n"
      "            return b\"x\"\n"
      "        return None\n")


class TestNarrowedOptStrCompare:

    def test_narrowed_local_against_literal_routes(self):
        # The http.client _check_close shape: an Optional[str] local, None-
        # tested in the first conjunct and compared in the second.
        src = (_H
               + "    def check(self) -> bool:\n"
               + "        ka = self.get(\"keep-alive\")\n"
               + "        if ka is None or ka == \"\":\n"
               + "            return True\n"
               + "        return False\n"
               + "print(H().check())\n")
        cpp = _assert_routes_byte_identical(src)[0]
        assert "((!ka.has_value()) || ((*ka) == \"\"))" in cpp

    def test_narrowed_param_routes(self):
        # The param flavor: the deref is a borrow string_view.
        src = (_H
               + "    def check(self, ka: str | None) -> bool:\n"
               + "        if ka is None or ka == \"\":\n"
               + "            return True\n"
               + "        return False\n"
               + "print(H().check(\"a\"))\n")
        _assert_routes_byte_identical(src)

    def test_narrowed_ordering_and_pair_route(self):
        src = (_H
               + "    def order(self) -> bool:\n"
               + "        ka = self.get(\"k\")\n"
               + "        if ka is not None and ka < \"m\":\n"
               + "            return True\n"
               + "        return False\n"
               + "    def pair(self) -> bool:\n"
               + "        a = self.get(\"k\")\n"
               + "        b = self.get(\"j\")\n"
               + "        if a is not None and b is not None and a == b:\n"
               + "            return True\n"
               + "        return False\n"
               + "print(H().order())\n"
               + "print(H().pair())\n")
        _assert_routes_byte_identical(src)

    def test_narrowed_bytes_compare_stays_ast(self):
        # The bytes deref is a span/vector pair -- a different compare
        # render, and unwitnessed.
        src = (_H
               + "    def check(self) -> bool:\n"
               + "        kb = self.getb(\"k\")\n"
               + "        if kb is None or kb == b\"\":\n"
               + "            return True\n"
               + "        return False\n"
               + "print(H().check())\n")
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:stmt.if")

    def test_unnarrowed_optional_compare_stays_ast(self):
        # A whole-optional compare has no inner to read; the gate must keep
        # judging the declared binding.
        src = (_H
               + "    def check(self) -> bool:\n"
               + "        ka = self.get(\"k\")\n"
               + "        if ka == self.get(\"j\"):\n"
               + "            return True\n"
               + "        return False\n"
               + "print(H().check())\n")
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:stmt.if")
