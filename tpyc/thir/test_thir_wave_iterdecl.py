"""Iterator-decl wave arms: the native-iterator value slot (`SpanIter`),
the structural-protocol `auto` slot (`it = iter(c)`), the native_function
free-form method call (`b.__iter__()` -> `::tpy::__iter__(b)`), and the
protocol-result print arg."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
    _assert_routes_byte_identical,
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

    def test_view_receiver_dunder_iter_decl_routes(self):
        # `char_it = chars.__iter__()` -- a str/StrView receiver's
        # @cpp_template `__iter__` returns the Iterator[Char] PROTOCOL, which
        # C++ concepts cannot type: the AST spells `auto` and the rvalue
        # carries the concrete type. The protocol family already carried the
        # row; the view family was missing it.
        src = ("from tpy import StrView\n"
               "def main() -> None:\n"
               "    chars: str = 'hi'\n"
               "    char_it = chars.__iter__()\n"
               "    for c in char_it:\n"
               "        print(c)\n"
               "    sv: StrView = StrView('abc')\n"
               "    sv_it = sv.__iter__()\n"
               "    for c2 in sv_it:\n"
               "        print(c2)\n"
               "main()\n")
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("method.view_self_storage_ret", 0) == 2
        cpp = _assert_routes_byte_identical(src)
        assert "auto char_it = ::tpy::__iter__(chars);" in cpp[1]

    def test_view_dunder_iter_non_storage_sinks_stay_ast(self):
        # BOUNDARY: the widened row is STORAGE-sink only. The same protocol
        # result at a for-head iterable and at a print arg carries no
        # mirrored render, so both keep falling back.
        for tail in ("    for c in chars.__iter__():\n        print(c)\n",
                     "    print(chars.__iter__())\n"):
            src = ("def main() -> None:\n"
                   "    chars: str = 'hi'\n"
                   + tail + "main()\n")
            assert _fn(_lower_ctx(src), "main") is None

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
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.var_decl:decl.slot_type")

    def test_auto_readonly_dunder_iter_routes(self):
        # An @auto_readonly `__iter__` is an is_clone_pair overload set (the
        # overload check admitted it all along); the RESULT half opened in
        # the wave-4 native-iter ret row (`method.native_iter_ret`), so the
        # bodied dunder now routes -- the plain spelled SpanIter copy decl.
        src = ("from tpy import Int32, SpanIter\n"
               "from tplib import ArrayList\n"
               "def main() -> None:\n"
               "    a = ArrayList[Int32, 8]()\n"
               "    a.append(1)\n"
               "    it: SpanIter[Int32] = a.__iter__()\n"
               "    for x in it:\n"
               "        print(x)\n"
               "main()\n")
        assert _fn(_lower_ctx(src), "main") is not None
        _assert_byte_identical(src)
