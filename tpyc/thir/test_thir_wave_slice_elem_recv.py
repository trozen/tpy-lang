"""A str/bytes slice taken off a container ELEMENT read.

The slice `@cpp_template` interpolates its receiver, so the checked element
read is the whole receiver render. The element's own lowering owns its shape:
one it has no arm for raises there and falls the body back whole, which is
why the gate checks only that the element is a str/bytes-family value."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (_assert_byte_identical, _assert_rejects_at,
                       _assert_routes_byte_identical, _compile, _entry)


class TestSliceOverContainerElement:
    def test_element_receiver_routes(self):
        src = ("from tpy import Int32, StrView\n"
               "class Buf:\n"
               "    chunks: list[str]\n"
               "    pos: Int32\n"
               "    def __init__(self) -> None:\n"
               "        self.chunks = [\"hello world\"]\n"
               "        self.pos = 0\n"
               "    def read(self, size: Int32) -> StrView:\n"
               "        stop = self.pos + size\n"
               "        out = self.chunks[0][self.pos:stop]\n"
               "        self.pos = stop\n"
               "        return out\n"
               "def main() -> None:\n"
               "    b = Buf()\n"
               "    print(b.read(5))\n"
               "main()\n")
        # `read` is a method, so its inline body lands in the header.
        hpp, _cpp = _assert_routes_byte_identical(src, comments=False)
        assert ("::tpy::str_slice(::tpy::__getitem__(this->chunks, 0), "
                "::tpy::BasicSlice{this->pos, stop})") in hpp

    def test_slice_of_a_slice_receiver_stays_ast(self):
        # BOUNDARY: only an ELEMENT read is admitted. A slice receiver is a
        # view rvalue whose render is unwitnessed here.
        src = ("from tpy import StrView\n"
               "class Buf:\n"
               "    chunks: list[str]\n"
               "    def __init__(self) -> None:\n"
               "        self.chunks = [\"hello world\"]\n"
               "    def read(self) -> StrView:\n"
               "        return self.chunks[0][0:6][1:3]\n"
               "def main() -> None:\n"
               "    print(Buf().read())\n"
               "main()\n")
        compiler, modules = _compile(src)
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   comment_line_numbers=False,
                                   thir_codegen=True))
        _assert_rejects_at(dict(compiler._thir_fallback), "body:stmt.return",
                           "subscript.slice_shape")
        _assert_byte_identical(src)
