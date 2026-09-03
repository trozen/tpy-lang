"""The generator-method family rows: the member-gen iterable row widened to
the ctor-rvalue receiver lift (`Counter __tmp_N = Counter(..);` +
`__tmp_N.each()` -- the frame captures the receiver by reference, so the
temporary must outlive the call), omitted trailing defaults
(`b.upto_m()` -- the C++ signature carries the default), inferred method
targs (`f.items<int32_t>(42)` -- the member tail's method_targs suffix);
and the plain @native member on an unproven `Ptr[record]` receiver
(`::tpy::deref_check(s).outer()` -- keyed on sema's `ptr_non_null` fact).
"""

from __future__ import annotations

import io

from .emit import emit_thir_body
from .testutil import (_lower_ctx, _lower_ctx_witnessed, _fn,
                       _assert_byte_identical)


def _body(thir, name: str) -> str:
    buf = io.StringIO()
    emit_thir_body(buf, _fn(thir, name))
    return buf.getvalue()


_COUNTER = (
    "from typing import Iterator\n"
    "from tpy import Int32\n"
    "class Counter:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
    "    def each(self) -> Iterator[Int32]:\n"
    "        for i in range(self.n):\n"
    "            yield i\n"
    "    def pair(self) -> Iterator[Int32]:\n"
    "        yield self.n\n"
    "        yield self.n + 1\n"
    "    def total(self) -> Int32:\n        return self.n\n"
)


class TestGenRecvTemp:
    def test_ctor_rvalue_receiver_lifts_into_the_loop_scope(self):
        src = _COUNTER + (
            "def f() -> None:\n"
            "    for v in Counter(3).each():\n"
            "        print(v)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "Counter __tmp_1 = Counter(3);" in body
        assert "auto __src_0 = __tmp_1.each();" in body
        assert faces["method.gen_recv_temp"] >= 1
        _assert_byte_identical(src)

    def test_iterator_object_decl_receiver_lift(self):
        # The DECL position (`it = Counter(3).pair()`): the receiver temp
        # flushes before the decl line; the resumable-frame factory result
        # lands in the `auto` iterator-object local.
        src = _COUNTER + (
            "def f() -> None:\n"
            "    it = Counter(3).pair()\n"
            "    for v in it:\n"
            "        print(v)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "Counter __tmp_1 = Counter(3);" in body
        assert "auto it = __tmp_1.pair();" in body
        assert faces["method.gen_recv_temp"] >= 1
        _assert_byte_identical(src)

    def test_non_generator_temp_receiver_stays_ast(self):
        # The lift is the GENERATOR factory's (frame borrows the receiver);
        # a plain method on a ctor rvalue keeps its own rvalue-receiver
        # rows and must not take the named-temp render.
        src = _COUNTER + (
            "def f() -> None:\n"
            "    print(Counter(3).total())\n"
        )
        thir = _lower_ctx(src)
        body = _body(thir, "f") if _fn(thir, "f") is not None else ""
        assert "__tmp_1.total()" not in body
        _assert_byte_identical(src)


class TestGenMethodDefaultsAndTargs:
    def test_omitted_default_renders_the_truncated_call(self):
        src = (
            "from typing import Iterator\n"
            "from tpy import Int32\n"
            "class Box:\n"
            "    def upto(self, stop: Int32 = 2) -> Iterator[Int32]:\n"
            "        i: Int32 = 0\n"
            "        while i < stop:\n"
            "            yield i\n"
            "            i += 1\n"
            "def f(b: Box) -> None:\n"
            "    for v in b.upto():\n"
            "        print(v)\n"
            "    for v in b.upto(3):\n"
            "        print(v)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        body = _body(thir, "f")
        assert "b.upto();" in body
        assert "b.upto(3);" in body
        assert faces["call.omit_defaults"] >= 1
        _assert_byte_identical(src)

    def test_inferred_targs_spell_the_template_suffix(self):
        src = (
            "from typing import Iterator\n"
            "from tpy import Int32\n"
            "class Foo:\n"
            "    def items[U](self, x: U) -> Iterator[U]:\n"
            "        yield x\n"
            "        yield x\n"
            "def f(o: Foo) -> None:\n"
            "    for v in o.items(42):\n"
            "        print(v)\n"
        )
        body = _body(_lower_ctx(src), "f")
        assert "o.items<int32_t>(42);" in body
        _assert_byte_identical(src)

    def test_explicit_targs_member_gen_call_boundary(self):
        # The gate's targ condition requires the INFERRED args the member
        # tail's suffix spells; an EXPLICIT-targs spelling either shares
        # that channel (then it must render the same suffix, byte-checked)
        # or rejects to the AST path -- both verified here.
        src = (
            "from typing import Iterator\n"
            "from tpy import Int32\n"
            "class Foo:\n"
            "    def items[U](self, x: U) -> Iterator[U]:\n"
            "        yield x\n"
            "        yield x\n"
            "def f(o: Foo) -> None:\n"
            "    for v in o.items[Int32](42):\n"
            "        print(v)\n"
        )
        thir = _lower_ctx(src)
        fn = _fn(thir, "f")
        if fn is not None:
            assert "o.items<int32_t>(42);" in _body(thir, "f")
        _assert_byte_identical(src)


class TestPtrNativeMember:
    # The @native stubs live in the corpus case's src dir; reuse them via
    # extra_lib_dirs so the unit and the end-to-end case share one fixture.
    CASE_SRC = "tests/cases/native/native_include_propagation_transitive/src"

    def _dirs(self):
        from pathlib import Path
        return [Path(__file__).resolve().parents[2] / self.CASE_SRC]

    def test_unproven_ptr_receiver_takes_the_checked_member(self):
        src = (
            "from s import S\n"
            "from tpy import Ptr\n"
            "def f(s: Ptr[S]) -> bool:\n"
            "    return s.outer.inner.flag\n"
        )
        thir, faces = _lower_ctx_witnessed(src, extra_lib_dirs=self._dirs())
        body = _body(thir, "f")
        assert "::tpy::deref_check(s).outer().inner.flag" in body
        assert faces["method.ptr_native_member"] >= 1
        _assert_byte_identical(src, extra_lib_dirs=self._dirs())

    def test_field_ptr_receiver_stays_ast(self):
        # The core predicate is NAME-receiver only: a `Ptr[S]` FIELD read
        # as the receiver (`h.p.outer`) must keep rejecting -- the AST's
        # deref spelling for the field-held pointer is its own arm.
        src = (
            "from s import S\n"
            "from tpy import Ptr\n"
            "class H:\n"
            "    p: Ptr[S]\n"
            "    def __init__(self, p: Ptr[S]) -> None:\n"
            "        self.p = p\n"
            "def f(h: H) -> bool:\n"
            "    return h.p.outer.inner.flag\n"
        )
        thir, _ = _lower_ctx_witnessed(src, extra_lib_dirs=self._dirs())
        assert _fn(thir, "f") is None

    def test_native_member_with_args_stays_ast(self, tmp_path):
        # The gate requires a zero-arg member; an arg-taking @native method
        # on the Ptr receiver keeps rejecting.
        (tmp_path / "s2.py").write_text(
            '# tpy: native_module\n'
            '# tpy: cpp_namespace("xcore")\n'
            '# tpy: include("<x/s.hpp>")\n'
            'from tpy.extern import native\n'
            'from tpy import Int32\n'
            '@native("xcore::S2")\n'
            'class S2:\n'
            '    @native("pick")\n'
            '    def pick(self, i: Int32) -> Int32: ...\n')
        src = (
            "from s2 import S2\n"
            "from tpy import Ptr, Int32\n"
            "def f(s: Ptr[S2]) -> Int32:\n"
            "    return s.pick(1)\n"
        )
        thir, _ = _lower_ctx_witnessed(src, extra_lib_dirs=[tmp_path])
        assert _fn(thir, "f") is None

    def test_repeat_access_keeps_the_checked_spelling_on_both_paths(self):
        # The AST's post-access narrowing does NOT fire for the @native
        # property chain (both derefs stay `::tpy::deref_check(s)`), and the
        # gate mirrors its `ptr_non_null` test exactly -- if the AST ever
        # starts proving here (flipping to `s->outer()`), this byte pin
        # breaks loudly and the gate's unwitnessed proven face gets its
        # witness.
        src = (
            "from s import S\n"
            "from tpy import Ptr\n"
            "def f(s: Ptr[S]) -> bool:\n"
            "    a = s.outer.inner.flag\n"
            "    b = s.outer.inner.flag\n"
            "    return a and b\n"
        )
        thir, _ = _lower_ctx_witnessed(src, extra_lib_dirs=self._dirs())
        body = _body(thir, "f")
        assert body.count("::tpy::deref_check(s).outer()") == 2
        _assert_byte_identical(src, extra_lib_dirs=self._dirs())
