"""An `Own[T]` ctor param at a NON-last use: the warned MIL COPY.

At its last use an own param MOVES into the field; at a non-last use sema
warns (`copies ... field` / `may copy ... field`) and the AST's MIL emits the
bare `field(param)` a plain param gets. Three families share that one fact:
an F1-record field, a generic `T` field, and `print(<Own[T] name>)`.
"""
from __future__ import annotations

HDR = "from tpy import Int32, Own\n\n\nclass Inner:\n    value: Int32\n\n\n"

REC_COPY = HDR + (
    "class H:\n"
    "    inner: Inner\n"
    "    def __init__(self, inner: Own[Inner]):\n"
    "        self.inner = inner\n"
    "        print(inner.value)\n"
    "def main() -> None:\n"
    "    i = Inner()\n"
    "    i.value = 3\n"
    "    h = H(i)\n"
    "    print(h.inner.value)\n"
    "main()\n")

REC_MOVE = HDR + (
    "class H:\n"
    "    inner: Inner\n"
    "    def __init__(self, inner: Own[Inner]):\n"
    "        self.inner = inner\n"
    "def main() -> None:\n"
    "    i = Inner()\n"
    "    i.value = 3\n"
    "    h = H(i)\n"
    "    print(h.inner.value)\n"
    "main()\n")

TPARAM_COPY = HDR + (
    "class G[T]:\n"
    "    item: T\n"
    "    def __init__(self, item: Own[T]):\n"
    "        self.item = item\n"
    "        print(item)\n"
    "def main() -> None:\n"
    "    g = G[Int32](7)\n"
    "    print(g.item)\n"
    "main()\n")

PRINT_METHOD = HDR + (
    "class G[T]:\n"
    "    item: T\n"
    "    def __init__(self, item: Own[T]):\n"
    "        self.item = item\n"
    "    def show(self, other: Own[T]) -> None:\n"
    "        print(other)\n"
    "def main() -> None:\n"
    "    g = G[Int32](7)\n"
    "    g.show(9)\n"
    "main()\n")

OPTREC_COPY = HDR + (
    "class H:\n"
    "    inner: Inner | None\n"
    "    def __init__(self, inner: Own[Inner]):\n"
    "        self.inner = inner\n"
    "        print(inner.value)\n"
    "def main() -> None:\n"
    "    i = Inner()\n"
    "    h = H(i)\n"
    "    print(h.inner is None)\n"
    "main()\n")

PRINT_OWN_CONTAINER = (
    "from tpy import Int32, Own\n"
    "def show(xs: Own[list[Int32]]) -> None:\n"
    "    print(xs)\n"
    "def main() -> None:\n"
    "    show([1, 2])\n"
    "main()\n")


class TestOwnRecordParamMilCopy:

    def test_own_record_param_copy_routes_byte_identical(self):
        from .testutil import _assert_routes_byte_identical
        hpp, _cpp = _assert_routes_byte_identical(REC_COPY)
        # The warned copy must stay a COPY -- no std::move in the MIL.
        assert "H::H(Inner&& inner) : inner(inner) {" in hpp

    def test_own_record_param_copy_routes_the_ctor_and_witnesses(self):
        # `lower_module` holds no constructors, so the routing claim has to be
        # read off the seeded codegen ctx.
        from .testutil import _thir_ctx_witnessed
        ctx, wit, fb = _thir_ctx_witnessed(REC_COPY)
        assert not fb, fb
        assert any(c.record_name == "H" for c in ctx.thir_constructors.values())
        assert wit.get("mil.own_param_copy", 0) == 1

    def test_last_use_twin_still_moves(self):
        # BOUNDARY: the same param at its LAST use keeps the M3b-move arm --
        # the copy row must not swallow it.
        from .testutil import _assert_routes_byte_identical
        hpp, _cpp = _assert_routes_byte_identical(REC_MOVE)
        assert "H::H(Inner&& inner) : inner(std::move(inner)) {}" in hpp

    def test_optional_record_field_twin_still_rejects(self):
        # BOUNDARY (residue): the row keys on `own.wrapped == ftype`, and for
        # an `Inner | None` field the field type is the Optional while the
        # payload is `Inner` -- conservative, kept rejecting deliberately.
        from .testutil import _thir_ctx
        _ctx, fb = _thir_ctx(OPTREC_COPY)
        assert any(k.endswith("ctor.mil_field.optional.name") for k in fb), fb


class TestOwnTypeParamMilCopy:

    def test_own_tparam_param_copy_routes_byte_identical(self):
        from .testutil import _assert_routes_byte_identical
        hpp, _cpp = _assert_routes_byte_identical(TPARAM_COPY)
        assert "G(::tpy::own_param_t<T> item) : item(item) {" in hpp


class TestPrintOwnTypeParam:

    def test_print_own_tparam_routes_with_value_generic_form(self):
        from .nodes import PrintForm, THIRPrint
        from .testutil import _assert_routes_byte_identical, _thir_ctx
        _hpp, _cpp = _assert_routes_byte_identical(PRINT_METHOD)
        ctx, fb = _thir_ctx(PRINT_METHOD)
        assert not fb, fb
        show = next(f for f in ctx.thir_functions.values()
                    if f.name == "show")
        stmt = show.body[0]
        assert isinstance(stmt, THIRPrint)
        assert stmt.args[0].print_form is PrintForm.VALUE_GENERIC

    def test_print_own_container_still_rejects(self):
        # BOUNDARY: the peel is type-param-only -- `Own[list[...]]` keeps its
        # own reject rather than silently taking ValuePrinter.
        from .testutil import _thir_ctx
        _ctx, fb = _thir_ctx(PRINT_OWN_CONTAINER)
        assert any(k.endswith("print.arg.container_name") for k in fb), fb
