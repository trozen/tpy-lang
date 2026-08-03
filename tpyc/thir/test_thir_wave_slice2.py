"""Long-tail slice-receiver rows: a str-LITERAL receiver (position-neutral
const char[N], lands bare in the template), a bytearray NAME receiver
(`bytes_slice(ba, ...)` yielding the span view), and a pointer-slot
module-var container receiver (`sys.argv[1:]` -- the slice template is a
pinned consumer of the `(*slot)` read, like the native-slot arg)."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _fn, _assert_byte_identical,
)


class TestSliceReceiverLongTail:
    def test_str_literal_receiver_routes(self):
        src = ("def main() -> None:\n"
               "    name = (\"xalpha\")[1:]\n"
               "    stepped = (\"xalpha\")[::2]\n"
               "    print(name, stepped)\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        cpp = _assert_byte_identical(src)
        assert ("std::string_view name = ::tpy::str_slice(\"xalpha\", "
                "::tpy::BasicSlice{1, std::nullopt});" in cpp[1])

    def test_bytearray_name_receiver_routes(self):
        src = ("def main() -> None:\n"
               "    ba = bytearray(b\"0123456789\")\n"
               "    v = ba[1:3]\n"
               "    print(len(v))\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        cpp = _assert_byte_identical(src)
        assert ("std::span<const uint8_t> v = ::tpy::bytes_slice(ba, "
                "::tpy::BasicSlice{1, 3});" in cpp[1])

    def test_module_var_slice_receiver_routes(self):
        # The stepped form's owned result binds the decl; the non-stepped
        # ARG form is pinned by the flipped argparse cases (its other
        # consumers reject on separate gates).
        src = ("import sys\n"
               "def main() -> None:\n"
               "    every_other = sys.argv[::2]\n"
               "    print(len(every_other))\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        cpp = _assert_byte_identical(src)
        assert ("::tpy::list_stepped_slice((*::tpystd::sys::argv), "
                "::tpy::Slice{std::nullopt, std::nullopt, 2})" in cpp[1])

    def test_bytearray_field_receiver_stays_ast(self):
        # The FieldAccess branch keys on the viewfam field type; a
        # bytearray FIELD receiver keeps rejecting (boundary).
        src = ("class Buf:\n"
               "    data: bytearray\n"
               "    def __init__(self) -> None:\n"
               "        self.data = bytearray(b\"0123456789\")\n"
               "def field_slice(b: Buf) -> None:\n"
               "    v = b.data[1:3]\n"
               "    print(len(v))\n"
               "def main() -> None:\n"
               "    field_slice(Buf())\n"
               "main()\n")
        assert _fn(_lower_ctx(src), "field_slice") is None
        _assert_byte_identical(src)

    def test_module_var_nonslice_subscript_stays_ast(self):
        # The pinned consumer is the SLICE template only: a plain indexed
        # read of the pointer-slot module var keeps rejecting.
        src = ("import sys\n"
               "def main() -> None:\n"
               "    if len(sys.argv) > 0:\n"
               "        print(sys.argv[0])\n"
               "main()\n")
        assert _fn(_lower_ctx(src), "main") is None
        _assert_byte_identical(src)
