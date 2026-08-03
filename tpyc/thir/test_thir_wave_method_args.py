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


class TestCoercedByteArrayMoveArg:
    """The bytearray->bytes identity-coerce move row: a movable bytearray
    NAME at its last use into a `list[bytes]` element slot moves temp-free
    (`push_back(std::move(buf))`); non-last-use flavors stay AST (the
    inline_template callee binds the lvalue BARE there, a render the copy
    row would diverge from). Each shape dualgen-probed when the row landed;
    the resumable flavor's corpus witnesses are the frame_local_last_use_move
    pair."""

    _F = ("def f(n: int) -> int:\n"
          "    out: list[bytes] = []\n"
          '    buf = bytearray(b"abcd")\n'
          "    out.append(buf)\n")

    def test_sync_last_use_moves(self):
        src = self._F + "    return len(out) + len(out[0]) + n\nf(1)\n"
        body = _body(_lower_ctx(src), "f")
        assert "out.push_back(std::move(buf));" in body
        _assert_byte_identical(src)

    def test_sync_non_last_use_defers(self):
        src = (self._F
               + "    buf.append(120)\n"
               + "    return len(out[0]) + len(buf) + n\nf(1)\n")
        assert _fn(_lower_ctx(src), "f") is None
        _assert_byte_identical(src)

    def test_resumable_non_last_use_defers(self):
        # The coerce-over-frame-slot fence holds for the non-move residue:
        # the AST's rendered-string needs_copy test flips on the `(*buf)`
        # render, an unwitnessed bare shape.
        src = ("from typing import Iterator\n\n"
               "def gen() -> Iterator[int]:\n"
               "    out: list[bytes] = []\n"
               '    buf = bytearray(b"abcd")\n'
               "    out.append(buf)\n"
               "    yield len(out[0])\n"
               "    buf.append(120)\n"
               "    yield len(buf)\n\n"
               "def main() -> None:\n    pass\nmain()\n")
        assert _fn(_lower_ctx(src), "gen") is None
        _assert_byte_identical(src)

    def test_free_call_own_bytes_last_use_moves(self):
        # The FREE-call flavor of the same row: the caller body routes with
        # the temp-free move even while the callee body (an Own[bytes]
        # param read) still falls back on its own blocker.
        src = ("from tpy import Int32, Own\n\n"
               "def take(b: Own[bytes]) -> Int32:\n"
               "    return Int32(len(b))\n\n"
               "def f() -> Int32:\n"
               '    buf = bytearray(b"abcd")\n'
               "    return take(buf)\n\n"
               "def main() -> None:\n    print(f())\nmain()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        body = _body(thir, "f")
        assert "take(std::move(buf))" in body
        _assert_byte_identical(src)

    def test_bytes_into_bytearray_param_defers(self):
        # The REVERSE identity direction (bytes name at a bytearray& param):
        # the disposition alone admits nothing -- no arg row consumes it,
        # the body falls back byte-identically. Pinned so the direction
        # cannot silently start routing without a witness.
        src = ("from tpy import Int32\n\n"
               "def take_ba(b: bytearray) -> Int32:\n"
               "    return Int32(len(b))\n\n"
               "def f() -> Int32:\n"
               '    bs = b"abcd"\n'
               "    return take_ba(bs)\n\n"
               "def main() -> None:\n    print(f())\nmain()\n")
        assert _fn(_lower_ctx(src), "f") is None
        _assert_byte_identical(src)


class TestSpanCoerceMethodArg:
    def test_array_local_at_span_method_slot(self):
        # The span-family coerce at a METHOD slot rides the free-call row
        # (`sink.first(::tpy::as_mut_span(span_src))`).
        src = ("from tpy import Int32, Span\n"
               "class Sink:\n"
               "    def first(self, xs: Span[Int32]) -> Int32:\n"
               "        return xs[0]\n"
               "def main() -> None:\n"
               "    sink = Sink()\n"
               "    span_src = [1, 2, 3]\n"
               "    print(sink.first(span_src))\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        body = _body(thir, "main")
        assert "sink.first(::tpy::as_mut_span(span_src))" in body
        _assert_byte_identical(src)

    def test_readonly_span_method_slot(self):
        # The readonly flavor: `as_span` at a Span[readonly[T]] method slot.
        src = ("from tpy import Int32, Span, readonly\n"
               "class Sink:\n"
               "    def first(self, xs: Span[readonly[Int32]]) -> Int32:\n"
               "        return xs[0]\n"
               "def main() -> None:\n"
               "    sink = Sink()\n"
               "    src = [7, 8, 9]\n"
               "    print(sink.first(src))\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        body = _body(thir, "main")
        assert "sink.first(::tpy::as_span(src))" in body
        _assert_byte_identical(src)
