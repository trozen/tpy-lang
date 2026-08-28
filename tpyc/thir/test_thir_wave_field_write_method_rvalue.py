"""A record-returning METHOD call RVALUE at the record FIELD-WRITE copy sink.

`t.w = e.make(4);` -- the field's copy-assign absorbs the prvalue, so the
call renders bare exactly as it does at an owned-record decl slot. Threaded
off the field-write copy-sink flag, so the widening cannot reach the decl
sinks, which bind a REF_ALIAS off the same result. A BORROW-returning method
result stays out on both. Corpus witness: `asyncio.create_task`
(`task._waker = handle->make_waker_for_slot(..);`)."""

from .testutil import (_assert_byte_identical, _assert_rejects_at,
                       _assert_routes_byte_identical, _compile, _entry,
                       _lower_ctx_witnessed)
from ..codegen_cpp import CodeGenOptions


_SRC = ("from tpy import Int32, Own, Ptr, ValueType, take_ptr\n"
        "class Waker(ValueType):\n"
        "    g: Int32\n"
        "    def __init__(self, g: Int32) -> None:\n"
        "        self.g = g\n"
        "class Big:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "class Task:\n"
        "    w: Waker\n"
        "    b: Big\n"
        "    def __init__(self) -> None:\n"
        "        self.w = Waker(0)\n"
        "        self.b = Big(0)\n"
        "class Exec:\n"
        "    n: Int32\n"
        "    held: Big\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "        self.held = Big(1)\n"
        "    def make(self, g: Int32) -> Waker:\n"
        "        return Waker(g + self.n)\n"
        "    def own_big(self, g: Int32) -> Own[Big]:\n"
        "        return Big(g)\n"
        "    def borrow_big(self) -> Big:\n"
        "        return self.held\n")


def _fallback(src: str):
    compiler, modules = _compile(src)
    compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False,
                                                comment_line_numbers=False,
                                                thir_codegen=True))
    return dict(compiler._thir_fallback)


class TestMethodRvalueFieldWrite:
    PLAIN = (_SRC
             + "def put(t: Task, e: Exec) -> None:\n"
             + "    t.w = e.make(4)\n"
             + "def main() -> None:\n"
             + "    t = Task()\n"
             + "    put(t, Exec(1))\n"
             + "    print(t.w.g)\n"
             + "main()\n")

    PTR = (_SRC
           + "def put(t: Task, p: Ptr[Exec]) -> None:\n"
           + "    t.w = p.make(3)\n"
           + "def main() -> None:\n"
           + "    t = Task()\n"
           + "    e = Exec(1)\n"
           + "    put(t, take_ptr(e))\n"
           + "    print(t.w.g)\n"
           + "main()\n")

    def test_plain_receiver_routes_witnessed(self):
        thir, w = _lower_ctx_witnessed(self.PLAIN)
        assert w.get("field_write.method_rvalue_copy", 0) >= 1
        out = _assert_routes_byte_identical(self.PLAIN)
        assert "t.w = e.make(4);" in "".join(out)

    def test_ptr_receiver_routes_witnessed(self):
        # The corpus receiver kind: the Ptr-deref arm's own record-result
        # verdict has to carry the same sink.
        thir, w = _lower_ctx_witnessed(self.PTR)
        assert w.get("field_write.method_rvalue_copy", 0) >= 1
        out = _assert_routes_byte_identical(self.PTR)
        assert "t.w = ::tpy::deref_check(p).make(3);" in "".join(out)

    def test_own_returning_method_routes(self):
        # A non-value record behind `Own` is the same prvalue copy.
        src = (_SRC
               + "def put(t: Task, e: Exec) -> None:\n"
               + "    t.b = e.own_big(5)\n"
               + "def main() -> None:\n"
               + "    t = Task()\n"
               + "    put(t, Exec(1))\n"
               + "    print(t.b.n)\n"
               + "main()\n")
        out = _assert_routes_byte_identical(src)
        assert "t.b = e.own_big(5);" in "".join(out)


class TestMethodRvalueFieldWriteBoundary:
    def test_borrow_returning_method_keeps_rejecting(self):
        # BOUNDARY: a `T&`-returning method result is the REF_ALIAS frontier
        # -- admitting it here would let the same result reach the decl
        # sinks' binding shape through a flag meant for the copy.
        src = (_SRC
               + "def put(t: Task, e: Exec) -> None:\n"
               + "    t.b = e.borrow_big()\n"
               + "def main() -> None:\n"
               + "    t = Task()\n"
               + "    put(t, Exec(1))\n"
               + "    print(t.b.n)\n"
               + "main()\n")
        _assert_rejects_at(_fallback(src), "body:stmt.assign",
                           "assign.field_write_shape")
        _assert_byte_identical(src)

    def test_user_deref_receiver_keeps_rejecting(self):
        # BOUNDARY: the user-Deref chain arm is a separate render row and
        # was not widened -- the copy sink must not reach it.
        src = (_SRC
               + "from tplib import Box\n"
               + "def put(t: Task, b: Box[Exec]) -> None:\n"
               + "    t.w = b.make(5)\n"
               + "def main() -> None:\n"
               + "    t = Task()\n"
               + "    put(t, Box(Exec(2)))\n"
               + "    print(t.w.g)\n"
               + "main()\n")
        _assert_rejects_at(_fallback(src), "body:expr.method_call",
                           "method.qualcall.ret.record_f1")
        _assert_byte_identical(src)
