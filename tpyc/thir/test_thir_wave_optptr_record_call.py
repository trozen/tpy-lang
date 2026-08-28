"""A RECORD-returning free call at a pointer-repr `Optional[record]` slot.

The AST's optional-ptr arm is source-blind: any rvalue there hoists a
slot-typed temp and passes its address, so a free call joins the ctor and
marker-call rvalues already on that face. Corpus witness: tplib.requests'
`_connect` (`HTTPSConnection(host, hport, timeout, _ssl_context_for(v))`)."""

from .testutil import (_assert_byte_identical, _assert_rejects_at,
                       _assert_routes_byte_identical, _compile, _entry,
                       _lower_ctx_witnessed)
from ..codegen_cpp import CodeGenOptions


_BASE = ("from tpy import Int32, Own\n"
         "class Ctx:\n"
         "    k: Int32\n"
         "    def __init__(self, k: Int32) -> None:\n"
         "        self.k = k\n"
         "class Conn:\n"
         "    n: Int32\n"
         "    def __init__(self, n: Int32, c: Ctx | None) -> None:\n"
         "        self.n = n + (c.k if c is not None else 0)\n"
         "def mk(k: Int32) -> Own[Ctx]:\n"
         "    return Ctx(k)\n"
         "def take(c: Ctx | None) -> Int32:\n"
         "    return c.k if c is not None else 0\n")


def _fallback(src: str):
    compiler, modules = _compile(src)
    compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False,
                                                comment_line_numbers=False,
                                                thir_codegen=True))
    return dict(compiler._thir_fallback)


class TestRecordCallAtOptionalPtrSlot:
    CTOR_ARG = (_BASE
                + "def build(k: Int32) -> Own[Conn]:\n"
                + "    return Conn(1, mk(k))\n"
                + "def main() -> None:\n"
                + "    print(build(2).n)\n"
                + "main()\n")

    FREE_ARG = (_BASE
                + "def use(k: Int32) -> Int32:\n"
                + "    return take(mk(k))\n"
                + "def main() -> None:\n"
                + "    print(use(2))\n"
                + "main()\n")

    def test_ctor_arg_routes_witnessed(self):
        _, witnesses = _lower_ctx_witnessed(self.CTOR_ARG)
        assert witnesses.get("optptr.record_call_temp", 0) >= 1
        _assert_routes_byte_identical(self.CTOR_ARG)

    def test_free_call_arg_routes_witnessed(self):
        _, witnesses = _lower_ctx_witnessed(self.FREE_ARG)
        assert witnesses.get("optptr.record_call_temp", 0) >= 1
        _assert_routes_byte_identical(self.FREE_ARG)

    def test_ctor_rvalue_sibling_unaffected(self):
        # The pre-existing rvalue on the same face: it must keep taking the
        # ctor-shape verdict, not the call arm's pass-through.
        src = (_BASE
               + "def use(k: Int32) -> Int32:\n"
               + "    return take(Ctx(k))\n"
               + "def main() -> None:\n"
               + "    print(use(2))\n"
               + "main()\n")
        _, witnesses = _lower_ctx_witnessed(src)
        assert witnesses.get("optptr.ctor_rvalue", 0) >= 1
        assert witnesses.get("optptr.record_call_temp", 0) == 0
        _assert_routes_byte_identical(src)


class TestRecordCallAtOptionalPtrSlotBoundary:
    def test_borrow_returning_call_keeps_rejecting(self):
        # A borrow return is an lvalue: the AST takes its address with NO
        # temp, a different render -- the face must not claim it.
        src = (_BASE
               + "class Holder:\n"
               + "    c: Ctx\n"
               + "    def __init__(self) -> None:\n"
               + "        self.c = Ctx(9)\n"
               + "    def get(self) -> Ctx:\n"
               + "        return self.c\n"
               + "def peek(h: Holder) -> Ctx:\n"
               + "    return h.get()\n"
               + "def use() -> Int32:\n"
               + "    h = Holder()\n"
               + "    return take(peek(h))\n"
               + "def main() -> None:\n"
               + "    print(use())\n"
               + "main()\n")
        _assert_rejects_at(_fallback(src), "body:expr.call",
                           "call.arg_shape.optional")
        _assert_byte_identical(src)

    def test_subclass_returning_call_keeps_rejecting(self):
        # The temp's CLASS is re-derived from the arg type by the AST when
        # the pointee is inexact; admitting it here would spell the wrong
        # one.
        src = ("from tpy import Int32, Own\n"
               + "class Base:\n"
               + "    k: Int32\n"
               + "    def __init__(self, k: Int32) -> None:\n"
               + "        self.k = k\n"
               + "class Child(Base):\n"
               + "    def __init__(self, k: Int32) -> None:\n"
               + "        super().__init__(k + 1)\n"
               + "def mkc(k: Int32) -> Own[Child]:\n"
               + "    return Child(k)\n"
               + "def take(b: Base | None) -> Int32:\n"
               + "    return b.k if b is not None else 0\n"
               + "def use(k: Int32) -> Int32:\n"
               + "    return take(mkc(k))\n"
               + "def main() -> None:\n"
               + "    print(use(2))\n"
               + "main()\n")
        _assert_rejects_at(_fallback(src), "body:expr.call",
                           "call.arg_shape.optional")
        _assert_byte_identical(src)

    def test_match_guard_position_keeps_rejecting(self):
        # A guard admits calls but is never a flush point, so the temp has
        # nowhere to land.
        src = (_BASE
               + "def use(k: Int32) -> Int32:\n"
               + "    match k:\n"
               + "        case n if take(mk(n)) > 0:\n"
               + "            return 1\n"
               + "        case _:\n"
               + "            return 0\n"
               + "def main() -> None:\n"
               + "    print(use(2))\n"
               + "main()\n")
        _assert_rejects_at(_fallback(src), "body:expr.call",
                           "call.arg_shape.optional")
        _assert_byte_identical(src)
