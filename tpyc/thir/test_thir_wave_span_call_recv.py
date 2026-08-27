"""A list/Span slice taken off a CALL rvalue receiver.

The slice `@cpp_template` interpolates its receiver, so a container-returning
call renders bare inside it and the call's own lowering keeps owning its
shape -- the same leg the str-family slice arm already grants a call."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (_assert_byte_identical, _assert_rejects_at,
                       _assert_routes_byte_identical, _compile, _entry)

_BOX = (
    "from tpy import Int32, Span, readonly, basic_slice\n"
)


class TestContainerSliceOverCallReceiver:
    def test_span_returning_method_receiver_routes(self):
        src = (_BOX
               + "class Box:\n"
               + "    xs: list[Int32]\n"
               + "    def __init__(self) -> None:\n"
               + "        self.xs = [1, 2, 3, 4]\n"
               + "    def span(self) -> Span[readonly[Int32]]:\n"
               + "        return self.xs[0:len(self.xs)]\n"
               + "    def part(self, index: basic_slice"
                 ") -> Span[readonly[Int32]]:\n"
               + "        return self.span()[index]\n"
               + "def main() -> None:\n"
               + "    b = Box()\n"
               + "    print(len(b.part(basic_slice(1, 3))))\n"
               + "main()\n")
        # `part` is a method, so its inline body lands in the header.
        hpp, _cpp = _assert_routes_byte_identical(src, comments=False)
        assert "::tpy::list_slice(this->span(), index)" in hpp

    def test_element_read_receiver_stays_ast(self):
        # BOUNDARY: the container slice arm takes a name, a field or a call
        # receiver. A nested-container ELEMENT read is none of those, and
        # its render is unwitnessed here.
        src = (_BOX
               + "class Box:\n"
               + "    grid: list[list[Int32]]\n"
               + "    def __init__(self) -> None:\n"
               + "        self.grid = [[1, 2, 3, 4]]\n"
               + "    def part(self) -> Span[readonly[Int32]]:\n"
               + "        return self.grid[0][1:3]\n"
               + "def main() -> None:\n"
               + "    print(len(Box().part()))\n"
               + "main()\n")
        compiler, modules = _compile(src)
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   comment_line_numbers=False,
                                   thir_codegen=True))
        _assert_rejects_at(dict(compiler._thir_fallback), "body:stmt.return",
                           "subscript.slice_shape")
        _assert_byte_identical(src)
