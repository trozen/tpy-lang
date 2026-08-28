"""Plain F1-record FIELD write whose TARGET auto-derefs through a user
`__deref__` wrapper (`r.field = v` on `r: Rc[T]`).

The deref chain is a target-position render decided by the receiver, so it
composes with the record-write value rows (bare copy / `std::move` at a
movable name's last use / a ctor rvalue) unchanged. Corpus witness:
`tpy.channel`'s `_Recv.__poll__` (`this->_state.__deref__()._recv_waker =
waker;`)."""

from .testutil import (_assert_byte_identical, _assert_rejects_at,
                       _assert_routes_byte_identical, _compile, _entry,
                       _lower_ctx_witnessed)
from ..codegen_cpp import CodeGenOptions


_SRC = ("from tpy import Int32, Own\n"
        "from tplib import Box\n"
        "class Inner:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "class Holder:\n"
        "    ins: Inner\n"
        "    def __init__(self) -> None:\n"
        "        self.ins = Inner(1)\n")


def _fallback(src: str):
    compiler, modules = _compile(src)
    compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False,
                                                comment_line_numbers=False,
                                                thir_codegen=True))
    return dict(compiler._thir_fallback)


class TestDerefRecordFieldWrite:
    NAME = (_SRC
            + "def put(b: Box[Holder], v: Inner) -> None:\n"
            + "    b.ins = v\n"
            + "def main() -> None:\n"
            + "    b = Box(Holder())\n"
            + "    put(b, Inner(2))\n"
            + "main()\n")

    def test_name_source_routes_witnessed(self):
        thir, w = _lower_ctx_witnessed(self.NAME)
        assert w.get("field_write.record_user_deref", 0) >= 1
        assert w.get("field_write.record_name", 0) >= 1
        _assert_routes_byte_identical(self.NAME)

    def test_move_verdict_survives_the_chain(self):
        # An `Own` source at its last use still renders `std::move` through
        # the deref chain -- admission alone would drop it byte-visibly.
        src = (_SRC
               + "def put(b: Box[Holder], v: Own[Inner]) -> None:\n"
               + "    b.ins = v\n"
               + "def main() -> None:\n"
               + "    b = Box(Holder())\n"
               + "    put(b, Inner(2))\n"
               + "main()\n")
        out = _assert_routes_byte_identical(src)
        assert "b.__deref__().ins = std::move(v);" in "".join(out)

    def test_ctor_rvalue_source_routes(self):
        src = (_SRC
               + "def put(b: Box[Holder]) -> None:\n"
               + "    b.ins = Inner(9)\n"
               + "def main() -> None:\n"
               + "    b = Box(Holder())\n"
               + "    put(b)\n"
               + "main()\n")
        out = _assert_routes_byte_identical(src)
        assert "b.__deref__().ins = Inner(9);" in "".join(out)

    def test_self_field_wrapper_receiver_routes(self):
        # The corpus shape: the wrapper sits in a FIELD, not a param
        # (`this->w.__deref__().ins = v;`).
        src = (_SRC
               + "class Wrap:\n"
               + "    w: Box[Holder]\n"
               + "    def __init__(self, w: Own[Box[Holder]]) -> None:\n"
               + "        self.w = w\n"
               + "    def put(self, v: Inner) -> None:\n"
               + "        self.w.ins = v\n"
               + "def main() -> None:\n"
               + "    x = Wrap(Box(Holder()))\n"
               + "    x.put(Inner(3))\n"
               + "main()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert w.get("field_write.record_user_deref", 0) >= 1
        _assert_routes_byte_identical(src)


class TestDerefRecordFieldWriteBoundary:
    def test_optional_record_slot_keeps_rejecting(self):
        # BOUNDARY: a pointer-repr `Optional[record]` slot at a deref target
        # needs the `ptr_to_optional` lift, which the plain-slot widening
        # does not carry -- it must stay on the AST path.
        src = ("from tpy import Int32, Own\n"
               "from tplib import Box\n"
               "class Inner:\n"
               "    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        self.n = n\n"
               "class Holder:\n"
               "    opt: Inner | None\n"
               "    def __init__(self) -> None:\n"
               "        self.opt = None\n"
               "def put(b: Box[Holder], v: Inner) -> None:\n"
               "    b.opt = v\n"
               "def main() -> None:\n"
               "    b = Box(Holder())\n"
               "    put(b, Inner(2))\n"
               "main()\n")
        _assert_rejects_at(_fallback(src), "body:stmt.assign",
                           "assign.field_write_shape")
        _assert_byte_identical(src)
