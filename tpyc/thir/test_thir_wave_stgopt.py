"""The storage-optional comp/genexpr unpack mirror wave.

`lc.storage_opt_locals` mirrors codegen's `storage_form_optional_locals`
for comp/genexpr unpack targets binding a ptr-repr Optional[F1-record]
tuple element (`auto& p = std::get<0>(__tup_N);` -- storage form, not
pointer-accessed). Reads render the bare storage optional; a `T*` arg
slot lifts via `::tpy::optional_to_ptr(p)`. The genexpr grew unpack
heads (per-target binds off `auto& __tup_N = *__beg++;` in the
make_generator lambda).
"""

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _compile,
    _entry,
)


def _gen(source):
    compiler, modules = _compile(source)
    entry = _entry(modules)
    outs = {}
    for flag in (False, True):
        outs[flag] = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          comment_line_numbers=False,
                                          thir_codegen=flag))
    return compiler, outs[False], outs[True]


_PRE = (
    "from tpy import Int32\n"
    "class P:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n"
    "        self.x = x\n"
    "def borrow(p: P | None) -> Int32:\n"
    "    if p is None:\n"
    "        return Int32(-1)\n"
    "    return p.x\n"
)


class TestCompAndGenexprUnpackOptional:
    # The witness shape: comp unpack binds the storage optional element by
    # reference and the consumer lifts at the `P*` slot; the genexpr twin
    # binds the same lines inside the make_generator lambda.
    SRC = (
        _PRE +
        "def main() -> None:\n"
        "    items: list[tuple[P | None, Int32]] = [\n"
        "        (P(Int32(1)), Int32(10)),\n"
        "        (None, Int32(20)),\n"
        "    ]\n"
        "    results = [borrow(p) for p, n in items]\n"
        "    for r in results:\n"
        "        print(r)\n"
        "    total = sum(n for p, n in items)\n"
        "    print(total)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        compiler, ast, thir = _gen(self.SRC)
        assert thir == ast
        assert not dict(compiler._thir_fallback), dict(compiler._thir_fallback)
        assert "auto& p = std::get<0>(__tup_1);" in thir[1]
        assert "borrow(::tpy::optional_to_ptr(p))" in thir[1]
        assert "auto& __tup_2 = *__beg++;" in thir[1]
        w = compiler._thir_face_witnesses
        assert w.get("name.storage_opt_whole", 0) >= 1
        assert w.get("optptr.storage_name_lift", 0) >= 1
        assert w.get("genexpr.unpack", 0) >= 1


class TestForStatementHeadRoutesOptPtr:
    # The for-STATEMENT head over Optional-element tuples now routes: the
    # opt_ptr target binds `T* p = std::get<0>(__tup_N);` off the lifted
    # head (the standalone unpack's bind at the for head).
    SRC = (
        _PRE +
        "def main() -> None:\n"
        "    items: list[tuple[P | None, Int32]] = [(P(Int32(1)), Int32(10))]\n"
        "    total = 0\n"
        "    for p, n in items:\n"
        "        total = total + n\n"
        "    print(total)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        compiler, ast, thir = _gen(self.SRC)
        assert thir == ast
        fb = dict(compiler._thir_fallback)
        assert not any(k.startswith("body:") for k in fb), fb
        w = compiler._thir_face_witnesses
        assert w.get("stmt.tuple_unpack.opt_ptr_target", 0) >= 1


class TestNarrowedTargetReadDefers:
    # BOUNDARY: a NARROWED occurrence of the registered target (a deref
    # render) is a later rung -- the name row rejects it and the body
    # defers whole.
    SRC = (
        _PRE +
        "def main() -> None:\n"
        "    items: list[tuple[P | None, Int32]] = [(P(Int32(1)), Int32(10))]\n"
        "    vals = [(p.x if p is not None else n) for p, n in items]\n"
        "    print(vals[0])\n"
        "main()\n"
    )

    def test_defers_byte_identical(self):
        compiler, ast, thir = _gen(self.SRC)
        assert thir == ast
        fb = dict(compiler._thir_fallback)
        assert fb.get("body:expr.list_comp") == 1, fb


class TestMovedGenexprUnpackDefers:
    # BOUNDARY: a moved container-LITERAL source with an unpack head is
    # unwitnessed and keeps the named reject (the unpack route admits
    # lvalue sources only).
    SRC = (
        "from tpy import Int32\n"
        "class P:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "def main() -> None:\n"
        "    total = sum(n for p, n in [(P(Int32(1)), Int32(10))])\n"
        "    print(total)\n"
        "main()\n"
    )

    def test_defers_byte_identical(self):
        compiler, ast, thir = _gen(self.SRC)
        assert thir == ast
        fb = dict(compiler._thir_fallback)
        assert fb.get("body:genexpr.unpack") == 1, fb


class TestFilteredGenexprUnpackDefers:
    # BOUNDARY: a filtered genexpr (unpack or not) keeps the filter reject.
    SRC = (
        _PRE +
        "def main() -> None:\n"
        "    items: list[tuple[P | None, Int32]] = [(P(Int32(1)), Int32(10))]\n"
        "    total = sum(n for p, n in items if n > 0)\n"
        "    print(total)\n"
        "main()\n"
    )

    def test_defers_byte_identical(self):
        compiler, ast, thir = _gen(self.SRC)
        assert thir == ast
        fb = dict(compiler._thir_fallback)
        assert fb, fb
