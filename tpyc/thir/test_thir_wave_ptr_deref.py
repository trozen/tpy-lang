"""Pins for the deref_to_target Ptr[T] template row: `_deref_codegen`'s
PtrType arm is position-uniform (`::tpy::deref_check(x)` at arg / return /
init), so the coerce lowers through the `{0}` template like the scalar
casts. The record-wrapper `.__deref__()` flavor keeps its dedicated arg
row and stays off this template."""

from __future__ import annotations

from .testutil import _assert_byte_identical, _compile, _entry


def _gen_thir(source: str):
    from ..codegen_cpp.context import CodeGenOptions
    compiler, modules = _compile(source)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False))
    return hpp + cpp, compiler._thir_face_witnesses


class TestPtrDerefCoerce:
    def test_ptr_field_return_routes(self):
        # The generics/bound_chain shape: a Ptr[T] field returned through
        # the deref-check coercion at a generic T return slot.
        src = (
            "from tpy import Ptr, Own\n"
            "from tpy.unsafe import unsafe_take, unsafe_release\n"
            "class Leaf:\n"
            "    label: str\n"
            "    def __init__(self, label: str) -> None:\n"
            "        self.label = label\n"
            "    def kind(self) -> str:\n"
            "        return \"Leaf:\" + self.label\n"
            "class Holder[T]:\n"
            "    _payload: Ptr[T]\n"
            "    def __init__(self, payload: Ptr[T]) -> None:\n"
            "        self._payload = payload\n"
            "    def __del__(self) -> None:\n"
            "        unsafe_release(self._payload)\n"
            "    def get(self) -> T:\n"
            "        return self._payload\n"
            "def make[T](value: Own[T]) -> Own[Holder[T]]:\n"
            "    return Holder[T](unsafe_take(value))\n"
            "def main() -> None:\n"
            "    h = make(Leaf(\"a\"))\n"
            "    print(h.get().kind())\n"
            "main()\n"
        )
        out, faces = _gen_thir(src)
        assert "return ::tpy::deref_check(this->_payload);" in out
        _assert_byte_identical(src)

    def test_ptr_arg_position_routes(self):
        src = (
            "from tpy import Int32, Ptr\n"
            "class Point:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "def show(p: Point) -> None:\n"
            "    print(p.x)\n"
            "def main() -> None:\n"
            "    pt = Point(7)\n"
            "    ptr: Ptr[Point] = pt\n"
            "    show(ptr)\n"
            "main()\n"
        )
        out, faces = _gen_thir(src)
        assert "show(::tpy::deref_check(ptr));" in out
        _assert_byte_identical(src)
