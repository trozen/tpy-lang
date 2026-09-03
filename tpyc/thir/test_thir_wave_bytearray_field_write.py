"""`self.buf = new_buf` at a plain `bytearray` field.

A bytearray member is the same owning `std::vector<uint8_t>` the other
container families store, so the write is the object copy -- or the move at
an owned source's last use. It needs one thing they do not: bytearray is the
view-family member whose (family, form) pair does NOT fix the render, so the
storage convert must carry an explicit non-materialize decision or the
validator rejects the node.

An `Optional[bytearray]` field is pointer-repr here and takes a different
lift, so it stays out. Corpus witness: hashlib `_drain_blocks`.
"""

from __future__ import annotations

from .testutil import (
    _reject_tally,
    _assert_byte_identical, _assert_rejects_at,
    _assert_routes_byte_identical, _compile, _entry,
)
from ..codegen_cpp import CodeGenOptions


_ACC = (
    "from tpy import Int32, Own\n"
    "class Acc:\n"
    "    buf: bytearray\n"
    "    def __init__(self, buf: Own[bytearray]) -> None:\n"
    "        self.buf = buf\n"
)


def _emit(src: str):
    compiler, modules = _compile(src)
    hpp, cpp = compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False))
    return compiler, hpp + cpp


class TestBytearrayFieldWrite:
    SRC = (_ACC
           + "    def swap_move(self) -> None:\n"
           + "        nb: bytearray = bytearray()\n"
           + "        nb.append(1)\n"
           + "        self.buf = nb\n"
           + "    def swap_copy(self) -> Int32:\n"
           + "        nb: bytearray = bytearray()\n"
           + "        nb.append(2)\n"
           + "        self.buf = nb\n"
           + "        return Int32(len(nb))\n"
           + "    def set_ref(self, v: bytearray) -> None:\n"
           + "        self.buf = v\n")

    def test_move_and_copy_route(self):
        _assert_routes_byte_identical(self.SRC)
        _, code = _emit(self.SRC)
        assert "this->buf = std::move(nb);" in code   # last use
        assert "this->buf = nb;" in code              # still live below
        assert "this->buf = v;" in code               # borrowed param

    def test_optional_bytearray_field_stays_ast(self):
        # BOUNDARY (dualgen-probed): an `Optional[bytearray]` field is
        # pointer-repr, whose write is the `ptr_to_optional_move` lift -- a
        # different render, so the plain-field rule must not reach it.
        src = ("from tpy import Own\n"
               "class Opt:\n"
               "    opt: bytearray | None\n"
               "    def __init__(self) -> None:\n"
               "        self.opt = None\n"
               "    def fill(self, v: Own[bytearray]) -> None:\n"
               "        nb: bytearray = bytearray()\n"
               "        self.opt = nb\n")
        _assert_rejects_at(_reject_tally(src), 'body:stmt.assign', 'assign.field_write_shape')

    def test_bytes_field_keeps_the_view_materialize(self):
        # BOUNDARY: the `bytes` sibling is a VIEW source at an owning slot,
        # so its convert is the materialize copy, not the object move this
        # row decides on.
        src = ("class Blob:\n"
               "    data: bytes\n"
               "    def __init__(self) -> None:\n"
               "        self.data = b\"\"\n"
               "    def set(self, b: bytes) -> None:\n"
               "        self.data = b\n")
        _assert_routes_byte_identical(src)
        _, code = _emit(src)
        assert "this->data = ::tpy::bytes_copy(b);" in code
