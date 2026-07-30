"""Iterator-decl wave arms: the native-iterator value slot (`SpanIter`),
the structural-protocol `auto` slot (`it = iter(c)`), the native_function
free-form method call (`b.__iter__()` -> `::tpy::__iter__(b)`), and the
protocol-result print arg."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
)

_SPAN_SRC = (
    "from tpy import Int32, Array, Span, SpanIter\n"
    "def main() -> None:\n"
    "    arr: Array[Int32, 3] = [10, 20, 30]\n"
    "    s: Span[Int32] = arr\n"
    "    it = SpanIter(s)\n"
    "    for x in it:\n"
    "        print(x)\n"
    "main()\n"
)

_ITER_SRC = (
    "from typing import Iterator\n"
    "from tpy import Int32, Array, Span\n"
    "def main() -> None:\n"
    "    a: Array[Int32, 3] = [10, 20, 30]\n"
    "    s: Span[Int32] = a\n"
    "    it = iter(s)\n"
    "    for x in it:\n"
    "        print(x)\n"
    "main()\n"
)


class TestNativeIterSlot:
    def test_spaniter_ctor_decl_routes_spelled(self):
        # `it = SpanIter(s)` -> the resolved ctor's pre-substituted template
        # into the spelled value slot.
        thir, faces = _lower_ctx_witnessed(_SPAN_SRC)
        assert _fn(thir, "main") is not None
        assert faces.get("call.native_iter_instantiation", 0) == 1
        cpp = _assert_byte_identical(_SPAN_SRC)
        assert ("::tpy::SpanIter<int32_t> it = "
                "::tpy::SpanIter<int32_t>(s);" in cpp[1])

    def test_iter_builtin_decl_routes_auto(self):
        # `it = iter(s)` binds the Iterator[T] protocol slot as `auto`; the
        # native helper render carries the concrete type.
        thir = _lower_ctx(_ITER_SRC)
        assert _fn(thir, "main") is not None
        cpp = _assert_byte_identical(_ITER_SRC)
        assert "auto it = ::tpy::__iter__(s);" in cpp[1]

    def test_container_dunder_iter_method_routes(self):
        # `it = b.__iter__()` -- the @native(..., function=True) method
        # renders the qualified free symbol over the receiver.
        src = ("from tpy import Int32\n"
               "def main() -> None:\n"
               "    b: list[Int32] = [1, 2]\n"
               "    it = b.__iter__()\n"
               "    for x in it:\n"
               "        print(x)\n"
               "    print(len(b))\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces.get("method.native_function_form", 0) == 1
        cpp = _assert_byte_identical(src)
        assert "auto it = ::tpy::__iter__(b);" in cpp[1]

    def test_print_iter_streams_raw(self):
        # `print(iter(s))` -- the protocol-result call streams RAW.
        src = ("from tpy import Int32, Array, Span\n"
               "def main() -> None:\n"
               "    a: Array[Int32, 3] = [1, 2, 3]\n"
               "    s: Span[Int32] = a\n"
               "    print(iter(s))\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces.get("print.protocol_call", 0) == 1
        cpp = _assert_byte_identical(src)
        assert "std::cout << ::tpy::__iter__(s) << " in cpp[1]

    def test_pointer_receiver_dunder_iter_stays_ast(self):
        # The native_function-form arm admits bare non-pointer name
        # receivers only: a reassigned (pointer-local) receiver's deref
        # render is not carried, so the body must keep falling back.
        src = ("from tpy import Int32\n"
               "def main() -> None:\n"
               "    a: list[Int32] = [1, 2]\n"
               "    b: list[Int32] = [3]\n"
               "    x = a\n"
               "    x = b\n"
               "    it = x.__iter__()\n"
               "    for v in it:\n"
               "        print(v)\n"
               "main()\n")
        assert _fn(_lower_ctx(src), "main") is None
        _assert_byte_identical(src)

    def test_auto_readonly_dunder_iter_stays_ast(self):
        # An @auto_readonly `__iter__` is a bodied multi-overload method
        # (never native_function), so it bypasses the new free-form arm
        # entirely and rejects at the record method gate's result/overload
        # checks -- the case-level fence this pin holds is that the body
        # keeps falling back.
        src = ("from tpy import Int32, SpanIter\n"
               "from tplib import ArrayList\n"
               "def main() -> None:\n"
               "    a = ArrayList[Int32, 8]()\n"
               "    a.append(1)\n"
               "    it: SpanIter[Int32] = a.__iter__()\n"
               "    for x in it:\n"
               "        print(x)\n"
               "main()\n")
        assert _fn(_lower_ctx(src), "main") is None
        _assert_byte_identical(src)
