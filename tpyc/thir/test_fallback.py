"""Unit tests for the per-body AST-fallback tally (fallback.py): the
helpers record on the active compiler and no-op without one, the
signature gate notes first-reject reasons through a real lowering
attempt, and classify_stmt tags landmark constructs."""

from __future__ import annotations

from ..compilation_context import _current_compiler, activate_compiler
from .fallback import (
    begin_attempt,
    begin_stmt,
    classify_stmt,
    fold_attempt,
    note,
    note_detail,
    stmt_reject_reason,
)
from .lower import iter_module_callables, lower_function
from .lower.predicates import _type_family_tag
from .testutil import _compile, _entry


def test_note_and_fold_record_on_active_compiler():
    compiler, _ = _compile("def f() -> None:\n    pass\n")
    with activate_compiler(compiler):
        begin_attempt()
        assert note("sig.async") is False
        # Set-if-empty: the first recorded reason wins the attempt.
        assert note("stmt.for_each") is False
        fold_attempt("body")
        begin_attempt()
        fold_attempt("ctor")  # no reason recorded -> unclassified
    assert compiler._thir_fallback == {
        "body:sig.async": 1,
        "ctor:unclassified": 1,
    }


def test_noop_without_active_compiler():
    # Outside a compilation the helpers record nowhere; note still returns
    # False for gate positions. The autouse fixture activates a stub
    # compiler, so clear the ContextVar explicitly.
    token = _current_compiler.set(None)
    try:
        begin_attempt()
        assert note("sig.async") is False
        fold_attempt("body")
    finally:
        _current_compiler.reset(token)


_SRC = (
    "from tpy import Array, Int32\n"
    "async def af() -> None:\n"
    "    pass\n"
    "def comp(src: Array[Int32, 3]) -> Int32:\n"
    "    xs = [v + 1 for v in src]\n"  # Array-SOURCE arm: outside the slice
    "    return len(xs)\n"
    "def ok(n: Int32) -> Int32:\n"
    "    return n + 1\n"
)


def test_end_to_end_first_reject_reasons():
    compiler, modules = _compile(_SRC)
    entry = _entry(modules)
    routed = []
    with activate_compiler(compiler):
        for func, self_type in iter_module_callables(entry.ast, entry.analyzer):
            begin_attempt()
            fn = lower_function(func, entry.analyzer, self_type=self_type)
            if fn is None:
                fold_attempt("body")
            else:
                routed.append(fn.name)
    fb = compiler._thir_fallback
    assert fb.get("body:sig.async") == 1
    # The comprehension local is the first-rejecting statement; the landmark
    # scan names the frontier, not the host statement shape.
    assert fb.get("body:expr.list_comp") == 1
    assert "ok" in routed


def test_detail_composes_into_stmt_tag():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "def g(n: Int32) -> None:\n"
        "    print(n)\n"
    )
    entry = _entry(modules)
    g = entry.ast.functions[0]
    stmt = g.body[0]
    with activate_compiler(compiler):
        begin_attempt()
        begin_stmt()
        assert note_detail("call.imported_symbol") is False
        # Set-if-empty: a later detail loses to the first.
        assert note_detail("call.arg_shape") is False
        assert stmt_reject_reason(stmt) == "stmt.expr_stmt:call.imported_symbol"
        # A fresh statement clears the slot: bare shape again.
        begin_stmt()
        assert stmt_reject_reason(stmt) == "stmt.expr_stmt"
        # A landmark tag stands alone -- no detail suffix.
        note_detail("call.imported_symbol")
        comp_stmt = _compile(
            "from tpy import Int32\n"
            "def h(n: Int32) -> None:\n"
            "    d = {i: i for i in range(n)}\n"
        )[1]
    entry2 = _entry(comp_stmt)
    h = entry2.ast.functions[0]
    with activate_compiler(compiler):
        assert stmt_reject_reason(h.body[0]) == "expr.dict_comp"


def test_detail_noop_without_active_compiler():
    token = _current_compiler.set(None)
    try:
        begin_stmt()
        assert note_detail("call.linkage") is False
    finally:
        _current_compiler.reset(token)


def test_classify_stmt_tags():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "def g(n: Int32) -> None:\n"
        "    d = {i: i for i in range(n)}\n"
        "    print(n)\n"
    )
    entry = _entry(modules)
    g = entry.ast.functions[0]
    assert classify_stmt(g.body[0]) == "expr.dict_comp"
    assert classify_stmt(g.body[1]) == "stmt.expr_stmt"


def test_type_family_tag_on_signature_types():
    # The shared drilldown family chain, pinned per family so a tag-chain
    # regression (wrong label, broken Own recursion) fails loudly.
    compiler, modules = _compile(
        "from tpy import Int32, Own, Ptr\n"
        "def f(a: Int32, b: Int32 | None, c: list[Int32],\n"
        "      d: tuple[Int32, str], e: Ptr[Int32], g: str,\n"
        "      i: Own[list[Int32]]) -> None:\n"
        "    pass\n"
    )
    entry = _entry(modules)
    an = entry.analyzer
    func = next(fn for fn in entry.ast.functions if fn.name == "f")
    tags = {name: _type_family_tag(pt, an) for name, pt in func.params}
    assert tags == {
        "a": "scalar", "b": "optional", "c": "container", "d": "tuple",
        "e": "ptr", "g": "str", "i": "own_container",
    }
    assert _type_family_tag(None, an) == "untyped"
