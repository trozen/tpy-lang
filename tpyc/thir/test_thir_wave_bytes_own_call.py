"""An owned-bytes CALL rvalue at an `Own[bytes]` element slot.

The owned-NAME face at that slot already binds bare -- a `@cpp_template`
callee takes the lvalue natively, so the Own cascade emits no copy temp. A
prvalue has nothing to move from and needs no conversion, so it binds the
same way. A VIEW-returning callee still owes the view->owned materialize and
is a different render, so it keeps rejecting."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _assert_byte_identical, _assert_routes_byte_identical, _compile, _entry,
    _lower_ctx_witnessed)


class TestOwnedBytesCallRvalueAtOwnSlot:
    _SRC = ("from tpy import Int32\n"
            "class Sink:\n"
            "    chunks: list[bytes]\n"
            "    def __init__(self) -> None:\n"
            "        self.chunks = []\n"
            "    def add(self, v: bytes) -> None:\n"
            "        self.chunks.append(bytes(v))\n"
            "def main() -> None:\n"
            "    s = Sink()\n"
            "    s.add(b\"ab\")\n"
            "    print(len(s.chunks))\n"
            "main()\n")

    def test_conversion_call_routes(self):
        hpp, _cpp = _assert_routes_byte_identical(self._SRC, comments=False)
        assert "this->chunks.push_back(::tpy::bytes_copy(v))" in hpp

    def test_the_row_is_witnessed(self):
        _thir, faces = _lower_ctx_witnessed(self._SRC)
        assert faces.get("arg.bytes_owned_call", 0) == 1

