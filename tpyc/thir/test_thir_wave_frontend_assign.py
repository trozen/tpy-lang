"""Raw name-target `TpyAssign` statements -- the shape only frontend-IR /
macro-authored ASTs produce (the parser emits `TpyVarDecl` for every
ordinary name assign). Two arms live here: the `Ptr[T]` None write and the
module-scope global-slot delegation."""

from __future__ import annotations

import pytest

from ..codegen_cpp import CodeGenOptions
from ..parse.nodes import TpyAssign, TpyIf, TpyName, TpyNoneLiteral, TpyVarDecl
from .fallback import ThirUnsupported
from .lower import _LowerCtx
from .lower.statements import _lower_stmt
from .nodes import THIRAssign, THIRLiteral, Form
from .testutil import _compile, _entry

_PTR = (
    "from tpy import Int32, Ptr\n"
    "class T:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n"
    "        self.x = x\n"
)


def _fn_ctx(src: str, fn_name: str):
    """(lower ctx, declared-map, function) for `fn_name` -- the seat a
    synthetic statement is lowered in."""
    from ..compilation_context import activate_compiler
    compiler, modules = _compile(src)
    entry = _entry(modules)
    fn = next(f for f in entry.ast.functions if f.name == fn_name)
    declared = {n: t for n, t in fn.params}
    return compiler, activate_compiler(compiler), fn, declared, entry.analyzer


class TestRawAssignPtrNone:
    def test_ptr_slot_none_write_routes(self):
        # `p = None` at a `Ptr[T]` slot: the var-decl arm's decl.ptr_none
        # row, reached through the raw-assign tail (the generic tail has no
        # bare-None render at all).
        src = _PTR + "def f(p: Ptr[T]) -> Int32:\n    return p.x\n"
        compiler, ctxmgr, fn, declared, an = _fn_ctx(src, "f")
        with ctxmgr:
            node = _lower_stmt(
                TpyAssign(target=TpyName("p"), value=TpyNoneLiteral()),
                _LowerCtx(fn, an, None), declared)
        assert isinstance(node, THIRAssign)
        assert isinstance(node.value, THIRLiteral)
        assert node.value.value is None
        # VALUE form is what renders the bare `nullptr`; the STORAGE form is
        # the value-repr Optional's `std::nullopt`.
        assert node.value.form is Form.VALUE
        assert compiler._thir_face_witnesses.get("assign.ptr_none", 0) >= 1

    def test_optional_record_slot_none_write_rejects(self):
        # BOUNDARY: a pointer-repr `Optional[T]` local is NOT a Ptr value --
        # its None write is the optional family's own reseat render, so the
        # row must not capture it.
        src = (_PTR + "def f(o: T | None) -> Int32:\n"
                      "    if o is None:\n        return 0\n"
                      "    return o.x\n")
        compiler, ctxmgr, fn, declared, an = _fn_ctx(src, "f")
        with ctxmgr:
            with pytest.raises(ThirUnsupported, match="none_literal"):
                _lower_stmt(
                    TpyAssign(target=TpyName("o"), value=TpyNoneLiteral()),
                    _LowerCtx(fn, an, None), declared)

    def test_scalar_slot_none_write_rejects(self):
        # BOUNDARY: the row keys on the Ptr VALUE slot, not on "the value is
        # None" -- a value-repr Optional[scalar] binding keeps rejecting
        # here (its whole-optional write is the value-opt target arm).
        src = ("from tpy import Int32\n"
               "def f(n: Int32 | None) -> Int32:\n"
               "    if n is None:\n        return 0\n"
               "    return n\n")
        compiler, ctxmgr, fn, declared, an = _fn_ctx(src, "f")
        with ctxmgr:
            with pytest.raises(ThirUnsupported, match="none_literal"):
                _lower_stmt(
                    TpyAssign(target=TpyName("n"), value=TpyNoneLiteral()),
                    _LowerCtx(fn, an, None), declared)


def _raw_global_write(source: str, *, in_branch: bool):
    """Compile `source`, rewrite its SECOND top-level `xs` write as a raw
    `TpyAssign` (what a frontend emits), and return (ast, thir, fallback).

    The parser never produces this node for a name target, so the shape has
    no plain-Python fixture -- the rewrite is the only way to pin it outside
    the pascal corpus cases."""
    compiler, modules = _compile(source)
    entry = _entry(modules)
    if in_branch:
        for st in entry.ast.top_level_stmts:
            if isinstance(st, TpyIf):
                for i, b in enumerate(st.then_body):
                    if isinstance(b, TpyVarDecl) and b.name == "xs":
                        st.then_body[i] = TpyAssign(
                            target=TpyName("xs"), value=b.init, loc=b.loc)
    else:
        seen = 0
        stmts = entry.ast.top_level_stmts
        for i, st in enumerate(stmts):
            if isinstance(st, TpyVarDecl) and st.name == "xs":
                seen += 1
                if seen == 2:
                    stmts[i] = TpyAssign(target=TpyName("xs"), value=st.init,
                                         loc=st.loc)

    def gen(thir: bool):
        return compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          comment_line_numbers=False,
                                          thir_codegen=thir))

    ast = gen(False)
    thir = gen(True)
    return ast, thir, dict(compiler._thir_fallback)


class TestRawAssignGlobalSlot:
    """A non-value global's write reuses the module-init `static
    __global_slot_N`. The raw-assign tail must delegate to the same driver
    the var-decl arm uses -- its own rebind-slot render would spell a
    block-scoped `__slot_N` that module scope never declares."""

    def test_global_container_write_routes_slot_reuse(self):
        ast, thir, fallback = _raw_global_write(
            "from tpy import Int32\n"
            "xs: list[Int32] = [1, 2]\n"
            "xs = [3, 4]\n"
            "print(len(xs))\n", in_branch=False)
        assert not fallback
        assert thir == ast
        assert "xs = &(__global_slot_1 = {3, 4});" in "".join(thir)

    def test_branch_global_write_rejects(self):
        # BOUNDARY: the in-branch flavors are decided per kind on the
        # var-decl side (static-keyword placement differs by branch kind)
        # and none is witnessed through a raw assign -- so the whole body
        # falls back rather than pick one.
        ast, thir, fallback = _raw_global_write(
            "from tpy import Int32\n"
            "xs: list[Int32] = [1, 2]\n"
            "if len(xs) > 1:\n"
            "    xs = [3, 4]\n"
            "print(len(xs))\n", in_branch=True)
        assert fallback == {
            "top_level:stmt.assign:assign.global_slot_branch": 1}
        assert thir == ast
