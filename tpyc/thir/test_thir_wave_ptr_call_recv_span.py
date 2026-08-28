"""A `Ptr[T]`-returning CALL receiver at a `@cpp_template` member.

The template expands over the receiver's own render, so an rvalue `T*`
interpolates exactly like the NAME and FIELD receivers already admitted --
and its `Span[T]` result is a value view that lands bare even with an OPEN
element. Corpus witness: tplib.array_list's `__span__`
(`self._storage.ptr().span(Int32.trunc(self._size))`)."""

from .testutil import (_assert_byte_identical, _assert_rejects_at,
                       _assert_routes_byte_identical, _compile, _entry,
                       _lower_ctx_witnessed)
from ..codegen_cpp import CodeGenOptions


_HEAD = ("from tpy import Int32, Ptr, Span, take_ptr, nocopy\n"
         "from tpy.extern import native\n")


def _fallback(src: str):
    compiler, modules = _compile(src)
    compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False,
                                                comment_line_numbers=False,
                                                thir_codegen=True))
    return dict(compiler._thir_fallback)


class TestPtrCallReceiverTemplate:
    CONCRETE = (_HEAD
                + "class Buf:\n"
                + "    a: Int32\n"
                + "    def __init__(self) -> None:\n"
                + "        self.a = 0\n"
                + "    def ptr(self) -> Ptr[Int32]:\n"
                + "        return take_ptr(self.a)\n"
                + "    def span(self, n: Int32) -> Span[Int32]:\n"
                + "        return self.ptr().span(n)\n"
                + "def main() -> None:\n"
                + "    b = Buf()\n"
                + "    print(len(b.span(1)))\n"
                + "main()\n")

    OPEN_T = (_HEAD
              + "class GBuf[T]:\n"
              + "    a: T\n"
              + "    def __init__(self, a: T) -> None:\n"
              + "        self.a = a\n"
              + "    def ptr(self) -> Ptr[T]:\n"
              + "        return take_ptr(self.a)\n"
              + "    def span(self, n: Int32) -> Span[T]:\n"
              + "        return self.ptr().span(n)\n"
              + "def main() -> None:\n"
              + "    b = GBuf(3)\n"
              + "    print(len(b.span(1)))\n"
              + "main()\n")

    def test_concrete_element_routes_witnessed(self):
        _, witnesses = _lower_ctx_witnessed(self.CONCRETE)
        assert witnesses.get("method.ptr_template_call_recv", 0) >= 1
        assert witnesses.get("method.ptr_template_span", 0) >= 1
        _assert_routes_byte_identical(self.CONCRETE)

    def test_open_element_routes_witnessed(self):
        _, witnesses = _lower_ctx_witnessed(self.OPEN_T)
        assert witnesses.get("method.ptr_template_call_recv", 0) >= 1
        assert witnesses.get("method.ptr_template_span_open_t", 0) >= 1
        _assert_routes_byte_identical(self.OPEN_T)

    def test_deref_template_over_call_receiver_routes(self):
        # `__deref__` is the same template family over the same rvalue
        # pointer -- `::tpy::deref_check(this->ptr())`.
        src = (_HEAD
               + "class Node:\n"
               + "    v: Int32\n"
               + "    def __init__(self, v: Int32) -> None:\n"
               + "        self.v = v\n"
               + "    def bump(self) -> Int32:\n"
               + "        return self.v + 1\n"
               + "class H:\n"
               + "    n: Node\n"
               + "    def __init__(self) -> None:\n"
               + "        self.n = Node(2)\n"
               + "    def ptr(self) -> Ptr[Node]:\n"
               + "        return take_ptr(self.n)\n"
               + "    def go(self) -> Int32:\n"
               + "        return self.ptr().__deref__().bump()\n"
               + "def main() -> None:\n"
               + "    print(H().go())\n"
               + "main()\n")
        _, witnesses = _lower_ctx_witnessed(src)
        assert witnesses.get("method.ptr_template_call_recv", 0) >= 1
        _assert_routes_byte_identical(src)

    def test_name_and_field_receivers_still_route(self):
        src = (_HEAD
               + "class H:\n"
               + "    p: Ptr[Int32]\n"
               + "    def __init__(self, p: Ptr[Int32]) -> None:\n"
               + "        self.p = p\n"
               + "    def viafield(self, n: Int32) -> Int32:\n"
               + "        return Int32(len(self.p.span(n)))\n"
               + "def vianame(p: Ptr[Int32], n: Int32) -> Int32:\n"
               + "    return Int32(len(p.span(n)))\n"
               + "def main() -> None:\n"
               + "    a = 5\n"
               + "    print(H(take_ptr(a)).viafield(1) + vianame(take_ptr(a), 1))\n"
               + "main()\n")
        _, witnesses = _lower_ctx_witnessed(src)
        assert witnesses.get("method.ptr_template_call_recv", 0) == 0
        _assert_routes_byte_identical(src)


class TestPtrCallReceiverTemplateBoundary:
    def test_plain_native_member_on_call_receiver_keeps_rejecting(self):
        # A @native MEMBER (no cpp_template) reaches through the pointer and
        # needs the deref_check spelling, which has no call-receiver render.
        src = (_HEAD
               + "@native('tpy::MovableMutex')\n"
               + "@nocopy\n"
               + "class RawMu:\n"
               + "    def __init__(self) -> None: ...\n"
               + "    def lock(self) -> None: ...\n"
               + "class H:\n"
               + "    m: RawMu\n"
               + "    def __init__(self) -> None:\n"
               + "        self.m = RawMu()\n"
               + "    def ptr(self) -> Ptr[RawMu]:\n"
               + "        return take_ptr(self.m)\n"
               + "    def go(self) -> None:\n"
               + "        self.ptr().lock()\n"
               + "def main() -> None:\n"
               + "    H().go()\n"
               + "    print(1)\n"
               + "main()\n")
        _assert_rejects_at(_fallback(src), "body:expr.method_call")
        _assert_byte_identical(src)
