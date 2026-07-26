"""Three method-call ARG rows the record / marker loops were missing.

 * the callable family beyond lambdas -- a func-ref name, a Callable-value
   name, a callable-object record -- each of which renders itself
   independently of the slot, exactly as at a free call;
 * args of a USER-deref chain (`r.__deref__().m(args)`), whose kind the arg
   gate could not name: `_marker_call_kind` rejects every deref-marked call
   by construction, and the deref lowering arm has already validated the
   call shape by the time the gate runs;
 * `copy(p)` into an `Own[record]` slot -- the copy-construct rvalue
   (`Point(p)`) binds the `T&&` slot like any record rvalue, which is why
   the container-method loop already carried the row.
"""

from __future__ import annotations

import io

from .emit import emit_thir_body
from .testutil import _lower_ctx, _fn, _assert_byte_identical


def _body(thir, name: str) -> str:
    buf = io.StringIO()
    emit_thir_body(buf, _fn(thir, name))
    return buf.getvalue()


_SRC = (
    "from typing import Callable\n"
    "from tpy import Int32, Own, copy\n"
    "class Point:\n"
    "    x: Int32\n"
    "    def __init__(self) -> None:\n        self.x = 0\n"
    "    def set_x(self, v: Int32) -> None:\n        self.x = v\n"
    "    def blend(self, other: Own['Point']) -> Int32:\n"
    "        return self.x + other.x\n"
    "class Ref:\n"
    "    p: Point\n"
    "    def __init__(self, p: Own[Point]) -> None:\n        self.p = p\n"
    "    def __deref__(self) -> Point:\n        return self.p\n"
    "class Doubler:\n"
    "    k: Int32\n"
    "    def __init__(self, k: Int32) -> None:\n        self.k = k\n"
    "    def __call__(self, n: Int32) -> Int32:\n        return n * self.k\n"
    "class Sink:\n"
    "    total: Int32\n"
    "    def __init__(self) -> None:\n        self.total = 0\n"
    "    def store(self, p: Own[Point]) -> None:\n        self.total += p.x\n"
    "    def run(self, f: Callable[[Int32], Int32], v: Int32) -> Int32:\n"
    "        return f(v)\n"
    "class Factory:\n"
    "    @staticmethod\n"
    "    def consume(p: Own[Point]) -> Int32:\n        return p.x\n"
    "def double(n: Int32) -> Int32:\n    return n * 2\n"
)


class TestMethodCallableArgs:
    def test_func_ref_callable_value_and_object_pass_bare(self):
        src = _SRC + ("def f(s: Sink, cb: Callable[[Int32], Int32],\n"
                      "      d: Doubler) -> Int32:\n"
                      "    return s.run(double, 1) + s.run(cb, 2) + s.run(d, 3)\n")
        body = _body(_lower_ctx(src), "f")
        # `double_` -- the func-ref name escapes away from the C++ keyword-ish
        # collision set, exactly as at a free call.
        assert "s.run(double_, 1)" in body
        assert "s.run(cb, 2)" in body
        assert "s.run(d, 3)" in body
        _assert_byte_identical(src)


class TestUserDerefArgs:
    def test_args_lower_through_the_deref_chain(self):
        src = _SRC + ("def f(r: Ref, q: Point) -> Int32:\n"
                      "    r.set_x(7)\n"
                      "    return r.blend(copy(q))\n")
        body = _body(_lower_ctx(src), "f")
        assert "r.__deref__().set_x(7);" in body
        assert "r.__deref__().blend(Point(q))" in body
        _assert_byte_identical(src)


class TestCopyIntoOwnSlot:
    def test_record_method_and_static_method_faces(self):
        src = _SRC + ("def f(s: Sink, p: Point) -> Int32:\n"
                      "    s.store(copy(p))\n"
                      "    return Factory.consume(copy(p))\n")
        body = _body(_lower_ctx(src), "f")
        assert "s.store(Point(p));" in body
        assert "Factory::consume(Point(p))" in body
        _assert_byte_identical(src)

    def test_own_lvalue_name_keeps_its_copy_temp(self):
        # The copy row must not swallow the plain lvalue-NAME Own arg: that
        # one hoists a copy temp and moves it, where `copy(p)` binds the
        # `T&&` slot inline.
        src = _SRC + ("def f(s: Sink, p: Point) -> None:\n"
                      "    s.store(p)\n")
        body = _body(_lower_ctx(src), "f")
        assert "auto __tmp_1 = p;" in body
        assert "s.store(std::move(__tmp_1));" in body
        assert "s.store(Point(p))" not in body
        _assert_byte_identical(src)
