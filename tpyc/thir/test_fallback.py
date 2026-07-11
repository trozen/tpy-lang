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
from .lower import (
    iter_module_callables,
    iter_module_constructors,
    lower_constructor,
    lower_function,
)
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


def test_function_lowering_reject_falls_back_without_scope_residue():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "def rejected(s: str) -> Int32:\n"
        "    t = s + 'x'\n"
        "    del t\n"
        "    return 1\n"
        "def clean(n: Int32) -> Int32:\n"
        "    return n + 1\n"
    )
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
    assert compiler._thir_fallback.get(
        "body:stmt.del_var:nontrivial") == 1
    assert "clean" in routed


def test_constructor_lowering_reject_falls_back():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "class R:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32):\n"
        "        self.n = n\n"
        "        s = 'x' + 'y'\n"
        "        del s\n"
    )
    entry = _entry(modules)
    with activate_compiler(compiler):
        record, init, self_type = next(
            iter_module_constructors(entry.ast, entry.analyzer))
        begin_attempt()
        ctor = lower_constructor(
            record, init, entry.analyzer, self_type=self_type)
        if ctor is None:
            fold_attempt("ctor")
    assert ctor is None
    assert compiler._thir_fallback.get(
        "ctor:stmt.del_var:nontrivial") == 1


def test_base_init_arg_lowering_reject_falls_back():
    # A base-init arg is admitted by TYPE (`_base_init_arg_ok`), so a
    # structurally-unhandled scalar shape reaches `_lower_expr` and only rejects
    # there -- it must land on the ctor's fallback boundary, not escape as a crash.
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "class Base:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32):\n"
        "        self.x = x\n"
        "class Derived(Base):\n"
        "    y: Int32\n"
        "    def __init__(self, n: Int32):\n"
        "        super().__init__(m := n)\n"
        "        self.y = n\n"
    )
    entry = _entry(modules)
    with activate_compiler(compiler):
        ctors = {rec.name: (rec, init, st) for rec, init, st
                 in iter_module_constructors(entry.ast, entry.analyzer)}
        record, init, self_type = ctors["Derived"]
        begin_attempt()
        ctor = lower_constructor(
            record, init, entry.analyzer, self_type=self_type)
        if ctor is None:
            fold_attempt("ctor")
    assert ctor is None
    assert compiler._thir_fallback.get("ctor:expr.named_expr") == 1


def test_global_lowering_reject_falls_back_at_sync_boundary():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "message = 'before'\n"
        "def rejected(n: Int32) -> Int32:\n"
        "    global message\n"
        "    return n\n"
        "def clean(n: Int32) -> Int32:\n"
        "    return n + 1\n"
    )
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
    assert compiler._thir_fallback.get(
        "body:stmt.global:global.unseeded") == 1
    assert "clean" in routed


def test_global_lowering_reject_falls_back_at_constructor_boundary():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "message = 'before'\n"
        "class R:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32):\n"
        "        global message\n"
        "        self.n = n\n"
    )
    entry = _entry(modules)
    with activate_compiler(compiler):
        record, init, self_type = next(
            iter_module_constructors(entry.ast, entry.analyzer))
        begin_attempt()
        ctor = lower_constructor(
            record, init, entry.analyzer, self_type=self_type)
        if ctor is None:
            fold_attempt("ctor")
    assert ctor is None
    assert compiler._thir_fallback.get(
        "ctor:stmt.global:global.unseeded") == 1


def test_raise_lowering_reject_falls_back_at_sync_boundary():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "err = ValueError('bad')\n"
        "def rejected(n: Int32) -> Int32:\n"
        "    raise err\n"
        "def clean(n: Int32) -> Int32:\n"
        "    return n + 1\n"
    )
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
    assert compiler._thir_fallback.get("body:stmt.raise") == 1
    assert "clean" in routed


def test_numeric_literal_lowering_rejects_have_precise_reasons():
    compiler, modules = _compile(
        "from tpy import Float64, Int64\n"
        "def take_int(n: Int64) -> Int64:\n"
        "    return n\n"
        "def take_float(n: Float64) -> Float64:\n"
        "    return n\n"
        "def wide() -> Int64:\n"
        "    return take_int(2147483648)\n"
        "def nonfinite() -> Float64:\n"
        "    return take_float(1e400)\n"
    )
    entry = _entry(modules)
    with activate_compiler(compiler):
        for func, self_type in iter_module_callables(entry.ast, entry.analyzer):
            begin_attempt()
            fn = lower_function(func, entry.analyzer, self_type=self_type)
            if fn is None:
                fold_attempt("body")
    assert compiler._thir_fallback.get("body:expr.int_literal.range") == 1
    assert compiler._thir_fallback.get(
        "body:expr.float_literal.nonfinite") == 1


def test_unhandled_expression_rejects_from_lowering_tail():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "def rejected(n: Int32) -> Int32:\n"
        "    x = (y := n)\n"
        "    return x\n"
        "def clean(n: Int32) -> Int32:\n"
        "    return n + 1\n"
    )
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
    assert compiler._thir_fallback.get("body:expr.named_expr") == 1
    assert "clean" in routed


def test_while_lowering_reject_falls_back_at_sync_boundary():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "def rejected(n: Int32) -> Int32:\n"
        "    while True:\n"
        "        n = n + 1\n"
        "    return n\n"
        "def clean(n: Int32) -> Int32:\n"
        "    return n + 1\n"
    )
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
    assert compiler._thir_fallback.get("body:stmt.while") == 1
    assert "clean" in routed


def test_while_lowering_reject_falls_back_at_constructor_boundary():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "class R:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32):\n"
        "        while True:\n"
        "            n = n + 1\n"
        "        self.n = n\n"
    )
    entry = _entry(modules)
    with activate_compiler(compiler):
        record, init, self_type = next(
            iter_module_constructors(entry.ast, entry.analyzer))
        begin_attempt()
        ctor = lower_constructor(
            record, init, entry.analyzer, self_type=self_type)
        if ctor is None:
            fold_attempt("ctor")
    assert ctor is None
    assert compiler._thir_fallback.get("ctor:stmt.while") == 1


def test_for_lowering_reject_falls_back_at_sync_boundary():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "def rejected(n: Int32) -> Int32:\n"
        "    for i in range(n):\n"
        "        n = n + i\n"
        "    else:\n"
        "        n = n + 1\n"
        "    return n\n"
        "def clean(n: Int32) -> Int32:\n"
        "    return n + 1\n"
    )
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
    assert compiler._thir_fallback.get("body:stmt.for_each") == 1
    assert "clean" in routed


def test_tuple_unpack_lowering_reject_falls_back_at_sync_boundary():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "def rejected(pairs: list[tuple[Int32, Int32]]) -> Int32:\n"
        "    a, b = pairs[0]\n"
        "    return a + b\n"
        "def clean(n: Int32) -> Int32:\n"
        "    return n + 1\n"
    )
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
    assert compiler._thir_fallback.get("body:stmt.tuple_unpack") == 1
    assert "clean" in routed


def test_expr_stmt_lowering_reject_falls_back_at_sync_boundary():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "def rejected(n: Int32) -> Int32:\n"
        "    n + 1\n"
        "    return n\n"
        "def clean(n: Int32) -> Int32:\n"
        "    return n + 1\n"
    )
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
    assert compiler._thir_fallback.get(
        "body:stmt.expr_stmt:expr_stmt.bin_op") == 1
    assert "clean" in routed


def test_aug_assign_lowering_reject_falls_back_at_sync_boundary():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "def rejected(s: str) -> Int32:\n"
        "    s += 'x'\n"
        "    return Int32(len(s))\n"
        "def clean(n: Int32) -> Int32:\n"
        "    return n + 1\n"
    )
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
    assert compiler._thir_fallback.get("body:stmt.aug_assign") == 1
    assert "clean" in routed


def test_assert_lowering_reject_falls_back_at_sync_boundary():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "def rejected(n: Int32) -> Int32:\n"
        "    assert True\n"
        "    return n\n"
        "def clean(n: Int32) -> Int32:\n"
        "    return n + 1\n"
    )
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
    assert compiler._thir_fallback.get("body:stmt.assert") == 1
    assert "clean" in routed


def test_if_lowering_reject_falls_back_at_sync_boundary():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "def rejected(n: Int32) -> Int32:\n"
        "    if True:\n"
        "        n = n + 1\n"
        "    return n\n"
        "def clean(n: Int32) -> Int32:\n"
        "    return n + 1\n"
    )
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
    assert compiler._thir_fallback.get("body:stmt.if:cond.bool_literal") == 1
    assert "clean" in routed


def test_assign_lowering_reject_falls_back_at_sync_boundary():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "class R:\n"
        "    xs: list[Int32]\n"
        "    def __init__(self, xs: list[Int32]):\n"
        "        self.xs = xs\n"
        "def rejected(r: R, xs: list[Int32]) -> None:\n"
        "    r.xs = xs\n"
    )
    entry = _entry(modules)
    with activate_compiler(compiler):
        func, self_type = next(iter_module_callables(entry.ast, entry.analyzer))
        begin_attempt()
        fn = lower_function(func, entry.analyzer, self_type=self_type)
        if fn is None:
            fold_attempt("body")
    assert fn is None
    assert compiler._thir_fallback == {
        "body:stmt.assign:assign.field_write_shape": 1,
    }


def test_return_lowering_reject_falls_back_at_sync_boundary():
    compiler, modules = _compile(
        "def rejected(s: str) -> str | None:\n"
        "    return s\n"
    )
    entry = _entry(modules)
    with activate_compiler(compiler):
        func, self_type = next(iter_module_callables(entry.ast, entry.analyzer))
        begin_attempt()
        fn = lower_function(func, entry.analyzer, self_type=self_type)
        if fn is None:
            fold_attempt("body")
    assert fn is None
    assert compiler._thir_fallback == {
        "body:stmt.return:return.opt_view_source": 1,
    }


def test_sync_lowering_reports_first_reject_in_source_order():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "def rejected(n: Int32) -> Int32:\n"
        "    assert True\n"
        "    x = (y := n)\n"
        "    return x\n"
    )
    entry = _entry(modules)
    with activate_compiler(compiler):
        func, self_type = next(iter_module_callables(entry.ast, entry.analyzer))
        begin_attempt()
        fn = lower_function(func, entry.analyzer, self_type=self_type)
        if fn is None:
            fold_attempt("body")
    assert fn is None
    assert compiler._thir_fallback == {"body:stmt.assert": 1}


def test_constructor_lowering_reports_first_reject_in_source_order():
    compiler, modules = _compile(
        "from tpy import Int32\n"
        "class R:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32):\n"
        "        assert True\n"
        "        x = (y := n)\n"
        "        self.n = x\n"
    )
    entry = _entry(modules)
    with activate_compiler(compiler):
        record, init, self_type = next(
            iter_module_constructors(entry.ast, entry.analyzer))
        begin_attempt()
        ctor = lower_constructor(
            record, init, entry.analyzer, self_type=self_type)
        if ctor is None:
            fold_attempt("ctor")
    assert ctor is None
    assert compiler._thir_fallback == {"ctor:stmt.assert": 1}


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
        assert stmt_reject_reason(
            stmt, "name.global_read") == "stmt.expr_stmt:name.global_read"
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
