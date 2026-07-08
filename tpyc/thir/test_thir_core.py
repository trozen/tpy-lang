"""THIR value-scalar core: lowering eligibility/shape, statement shapes
(for-range/foreach/print), scalar exprs, dump, and the byte-identical emit
contract (THIR codegen == AST codegen for the slice)."""

from __future__ import annotations

import io

from ..codegen_cpp.context import CodeGenOptions
from ..compilation_context import activate_compiler
from ..parse.nodes import TpyCall, TpyExceptHandler, TpyPassStmt, TpyTry
from .dump import dump_thir
from .emit import emit_thir_body
from .lower import _is_len_native, lower_module
from .lower.functions import _shadow_bound_names
from ..typesys import TupleType
from .nodes import (
    Form, PrintForm, THIRAssign, THIRBinOp, THIRCall, THIRExprStmt,
    THIRFieldAccess, THIRForEach, THIRForRange, THIRFormConvert, THIRIf,
    THIRLiteral, THIRMethodCall, THIRName, THIRPrint, THIRReturn,
    THIRStrLiteral, THIRUnaryNot, THIRVarDecl, THIRWhile,
)
from .testutil import (
    _compile, _entry, _lower, _lower_ctx, _fn, _lower_ctor,
    _lower_ctx_witnessed, _PRELUDE, _F1_RECORDS,
)

class TestEligibility:
    def test_simple_function_is_eligible(self):
        thir = _lower(_PRELUDE + "def f(a: Int32) -> Int32:\n    b = a\n    return b\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert [p.name for p in fn.params] == ["a"]
        assert isinstance(fn.body[0], THIRVarDecl) and fn.body[0].name == "b"
        assert isinstance(fn.body[0].init, THIRName) and fn.body[0].init.name == "a"
        assert isinstance(fn.body[1], THIRReturn)

    def test_reassignment_lowers_to_assign(self):
        # The parser emits TpyVarDecl for every `name = expr`; a write to an
        # already-bound name must lower to THIRAssign, not a re-declaration.
        thir = _lower(_PRELUDE
                      + "def f(a: Int32, b: Int32) -> Int32:\n"
                      + "    x = a\n    x = b\n    return x\n")
        fn = _fn(thir, "f")
        assert isinstance(fn.body[0], THIRVarDecl)   # x = a   (first: decl)
        assert isinstance(fn.body[1], THIRAssign)    # x = b   (reassign)
        assert fn.body[1].target.name == "x"

    def test_param_reassignment_is_assign(self):
        thir = _lower(_PRELUDE + "def f(a: Int32) -> Int32:\n    a = a\n    return a\n")
        fn = _fn(thir, "f")
        assert isinstance(fn.body[0], THIRAssign)    # param already bound

    def test_void_return_is_eligible(self):
        thir = _lower(_PRELUDE + "def f(a: Int32):\n    b = a\n")
        assert _fn(thir, "f") is not None

    def test_global_reference_routes(self):
        # A read-only same-module value global seeds like a param; the read
        # renders bare (`return G;`), exactly the AST's same-module spelling.
        thir = _lower(_PRELUDE + "G: Int32 = 5\ndef f() -> Int32:\n    return G\n")
        assert _fn(thir, "f") is not None

    def test_arith_binop_is_eligible(self):
        # c = a + b + 1; return c -- nested arithmetic with a literal
        thir = _lower(_PRELUDE
                      + "def f(a: Int32, b: Int32) -> Int32:\n    c = a + b + 1\n    return c\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[0].init, THIRBinOp)
        assert fn.body[0].init.op == "+"
        assert isinstance(fn.body[0].init.left, THIRBinOp)   # (a + b) + 1

    def test_same_module_call_is_eligible(self):
        thir = _lower(_PRELUDE
                      + "def g(a: Int32) -> Int32:\n    return a + 1\n"
                      + "def f(a: Int32) -> Int32:\n    return g(a) + g(a + 1)\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[0]               # return g(a) + g(a + 1)
        assert isinstance(ret.value, THIRBinOp)
        assert isinstance(ret.value.left, THIRCall) and ret.value.left.callee == "g"

    def test_builtin_call_routes_via_resolved_symbol(self):
        # `abs` is an imported builtin: it routes with the resolved
        # @cpp_template/@native symbol pre-rendered -- never the bare name.
        thir = _lower(_PRELUDE + "def f(a: Int32) -> Int32:\n    return abs(a)\n")
        fn = _fn(thir, "f")
        assert fn is not None
        call = fn.body[0].value
        assert isinstance(call, THIRCall)
        assert (call.cpp_template is not None or call.native_name is not None
                or call.callee_cpp is not None)

    def test_scalar_aug_assign_routes(self):
        # TpyAugAssign on a scalar local -- formerly ineligible, now admitted.
        thir = _lower(_PRELUDE + "def f(a: Int32) -> Int32:\n    a += 1\n    return a\n")
        assert _fn(thir, "f") is not None

    def test_wide_literal_is_ineligible(self):
        # A literal outside [-2**31, 2**31-1] needs a suffix/cast the emitter
        # does not reproduce, even in a slot (UInt64) that can hold it.
        thir = _lower(_PRELUDE
                      + "def f(a: UInt64) -> UInt64:\n    b = a\n    b = 5000000000\n    return b\n")
        assert _fn(thir, "f") is None

    def test_if_else_is_eligible(self):
        thir = _lower(_PRELUDE
                      + "def f(a: Int32, b: Int32) -> Int32:\n"
                      + "    r = a\n"
                      + "    if a < b:\n        r = b\n    else:\n        r = a\n"
                      + "    return r\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[1], THIRIf)
        assert isinstance(fn.body[1].condition, THIRBinOp)
        assert fn.body[1].condition.op == "<"
        assert isinstance(fn.body[1].then_body[0], THIRAssign)

    def test_elif_lowers_as_nested_if(self):
        thir = _lower(_PRELUDE
                      + "def f(a: Int32) -> Int32:\n    r = a\n"
                      + "    if a < 0:\n        r = 0\n    elif a > 9:\n        r = 9\n"
                      + "    return r\n")
        fn = _fn(thir, "f")
        assert fn is not None
        outer = fn.body[1]
        assert isinstance(outer, THIRIf)
        assert len(outer.else_body) == 1 and isinstance(outer.else_body[0], THIRIf)

    def test_value_block_local_first_decl_routes(self):
        # `t` first-declared inside the branch (not read after -> the if hoists
        # nothing) is an in-place value block-local; it routes.
        thir = _lower(_PRELUDE
                      + "def f(a: Int32) -> Int32:\n    r = a\n"
                      + "    if a < 0:\n        t = 0 - a\n        r = t\n"
                      + "    return r\n")
        fn = _fn(thir, "f")
        assert fn is not None
        # The block-local decl lowers in place inside the then-body.
        assert isinstance(fn.body[1].then_body[0], THIRVarDecl)

    def test_hoisted_value_branch_decl_routes(self):
        # `x` is definitely-assigned after the if (both arms bind it) -> the AST
        # HOISTS `int32_t x;` to function scope; Slice 2 mirrors the predecl and
        # lowers the in-branch assigns as reassigns, so the body routes.
        thir = _lower(_PRELUDE
                      + "def f(c: bool) -> Int32:\n"
                      + "    if c:\n        x = 5\n    else:\n        x = 7\n"
                      + "    return x\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert fn.body[0].hoist_decls == (("x", "int32_t"),)

    def test_hoisted_nonvalue_branch_decl_is_ineligible(self):
        # A hoisted container/reference name registers pointer-local walk state
        # the slice does not reproduce -> the whole if stays AST.
        thir = _lower(_PRELUDE
                      + "def f(c: bool) -> Int32:\n"
                      + "    if c:\n        xs = [1]\n    else:\n        xs = [2]\n"
                      + "    return xs[0]\n")
        assert _fn(thir, "f") is None

    def test_nonvalue_container_block_local_is_ineligible(self):
        # A container-literal block-local is a pointer-local whose lowering
        # mutates function-scoped walk state -> stays AST.
        thir = _lower(_PRELUDE
                      + "def f(c: bool) -> None:\n"
                      + "    if c:\n        xs = [1, 2]\n        print(len(xs))\n")
        assert _fn(thir, "f") is None

    def test_truthiness_condition_is_ineligible(self):
        # `if a:` (int truthiness) is not a comparison condition.
        thir = _lower(_PRELUDE
                      + "def f(a: Int32) -> Int32:\n    r = a\n"
                      + "    if a:\n        r = 0\n    return r\n")
        assert _fn(thir, "f") is None

    def test_literal_first_decl_is_eligible(self):
        # `total = 0` resolves to the default int (not IntLiteralType).
        thir = _lower(_PRELUDE + "def f() -> Int32:\n    total = 0\n    return total\n")
        fn = _fn(thir, "f")
        assert fn is not None and fn.body[0].resolved_type.name == "Int32"

    def test_while_is_eligible(self):
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    i = 0\n"
                      + "    while i < n:\n        i = i + 1\n    return i\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[1], THIRWhile)
        assert fn.body[1].condition.op == "<"

    def test_while_else_is_ineligible(self):
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    i = 0\n"
                      + "    while i < n:\n        i = i + 1\n    else:\n        i = 0\n"
                      + "    return i\n")
        assert _fn(thir, "f") is None

    def test_while_with_break_routes(self):
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    i = 0\n"
                      + "    while i < n:\n        if i > 3:\n            break\n        i = i + 1\n"
                      + "    return i\n")
        assert _fn(thir, "f") is not None

    def test_for_with_continue_routes(self):
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    s = 0\n"
                      + "    for i in range(n):\n        if i > 3:\n            continue\n"
                      + "        s = s + i\n    return s\n")
        assert _fn(thir, "f") is not None

    def test_break_in_else_loop_is_ineligible(self):
        # A for/else loop's break is a `goto __after_else_N` -- the whole
        # body stays AST via the loop's orelse reject.
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n"
                      + "    for i in range(n):\n        if i > 3:\n            break\n"
                      + "    else:\n        return -1\n"
                      + "    return 1\n")
        assert _fn(thir, "f") is None

    def test_bigint_param_routes(self):
        # `int` is BigInt -- an eligible value scalar (::tpy::BigInt renders
        # bare like any fixed-width int).
        thir = _lower("def f(a: int) -> int:\n    b = a\n    return b\n")
        assert _fn(thir, "f") is not None

    def test_staticmethod_routes_like_free_function(self):
        # A staticmethod has no `self` receiver; its body lowers like a free
        # function's (the `static` prefix is signature-only).
        thir = _lower_ctx(
            _PRELUDE
            + "class C:\n    @staticmethod\n    def m(a: Int32) -> Int32:\n        b = a\n        return b\n")
        assert _fn(thir, "m") is not None

    def test_generic_is_ineligible(self):
        thir = _lower("def f[T](a: T) -> T:\n    b = a\n    return b\n")
        assert _fn(thir, "f") is None

    def test_walk_state_branch_copy_isolates_every_field(self):
        # branch_copy must deep-copy EVERY container field: a branch mutation
        # leaking into the parent scope would let a branch-local fact (a
        # POINTER decl, a narrowing) survive past the branch. Field-generic
        # so a sixth field added for a later cell can't be silently shared.
        from dataclasses import fields
        from .lower import _WalkState
        ws = _WalkState({"a": None})
        copy = ws.branch_copy()
        for f in fields(_WalkState):
            container = getattr(copy, f.name)
            if isinstance(container, dict):
                container["x"] = None
            else:
                container.add("x")
            assert "x" not in getattr(ws, f.name)


class TestForRange:
    def test_range_stop_eligible(self):
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    total = 0\n"
                      + "    for i in range(n):\n        total = total + i\n    return total\n")
        fn = _fn(thir, "f")
        assert fn is not None
        loop = fn.body[1]
        assert isinstance(loop, THIRForRange)
        assert loop.var == "i"
        assert loop.start is None                       # range(stop) -> implicit 0
        assert isinstance(loop.stop, THIRName) and loop.stop.name == "n"
        assert loop.stop_is_literal is False            # name bound -> hoisted
        assert isinstance(loop.body[0], THIRAssign)

    def test_range_start_stop_eligible(self):
        thir = _lower(_PRELUDE
                      + "def f(a: Int32, b: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(a, b):\n        acc = acc + i\n    return acc\n")
        loop = _fn(thir, "f").body[1]
        assert isinstance(loop, THIRForRange)
        assert isinstance(loop.start, THIRName) and loop.start.name == "a"
        assert isinstance(loop.stop, THIRName) and loop.stop.name == "b"
        assert loop.start_is_literal is False and loop.stop_is_literal is False

    def test_range_literal_bound_inlined(self):
        thir = _lower(_PRELUDE
                      + "def f() -> Int32:\n    total = 0\n"
                      + "    for i in range(10):\n        total = total + i\n    return total\n")
        loop = _fn(thir, "f").body[1]
        assert isinstance(loop, THIRForRange) and loop.stop_is_literal is True

    def test_literal_start_name_stop(self):
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(2, n):\n        acc = acc + i\n    return acc\n")
        loop = _fn(thir, "f").body[1]
        assert loop.start_is_literal is True and loop.stop_is_literal is False

    def test_nested_range_eligible(self):
        thir = _lower(_PRELUDE
                      + "def f(a: Int32, b: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(a, b):\n        for j in range(b):\n"
                      + "            acc = acc + j\n    return acc\n")
        outer = _fn(thir, "f").body[1]
        assert isinstance(outer, THIRForRange)
        assert isinstance(outer.body[0], THIRForRange)

    def test_loop_var_reassign_in_body_eligible(self):
        # The loop var is visible in the body; rebinding it lowers to THIRAssign,
        # not a re-declaration (it is declared by the C++ for-init).
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(n):\n        i = i + 1\n        acc = acc + i\n    return acc\n")
        loop = _fn(thir, "f").body[1]
        assert isinstance(loop, THIRForRange)
        assert isinstance(loop.body[0], THIRAssign) and loop.body[0].target.name == "i"

    def test_for_else_is_ineligible(self):
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(n):\n        acc = acc + i\n    else:\n        acc = 0\n"
                      + "    return acc\n")
        assert _fn(thir, "f") is None

    def test_loop_var_used_after_is_ineligible(self):
        # `i` read after the loop -> sema hoists the loop var (pre-declaration),
        # which the emitter's plain C++ for-scope binding does not reproduce.
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    last = 0\n"
                      + "    for i in range(n):\n        last = i\n    return last + i\n")
        assert _fn(thir, "f") is None

    def test_literal_pos_step_routes(self):
        # 3-arg range with a non-unit positive literal step.
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(0, n, 2):\n        acc = acc + i\n    return acc\n")
        loop = _fn(thir, "f").body[1]
        assert isinstance(loop, THIRForRange) and loop.step_kind == "literal_pos"
        assert loop.step is not None

    def test_literal_neg_step_routes(self):
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(n, 0, -3):\n        acc = acc + i\n    return acc\n")
        loop = _fn(thir, "f").body[1]
        assert isinstance(loop, THIRForRange) and loop.step_kind == "literal_neg"

    def test_unit_neg_step_routes(self):
        # A literal -1 step collapses to the descending `--` loop (no step ref).
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(n, 0, -1):\n        acc = acc + i\n    return acc\n")
        loop = _fn(thir, "f").body[1]
        assert isinstance(loop, THIRForRange) and loop.step_kind == "unit_neg"
        assert loop.step is None

    def test_unit_pos_step_routes(self):
        # A literal +1 step collapses to the ascending `++` loop.
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(0, n, 1):\n        acc = acc + i\n    return acc\n")
        loop = _fn(thir, "f").body[1]
        assert isinstance(loop, THIRForRange) and loop.step_kind == "plus_one"
        assert loop.step is None

    def test_variable_step_routes(self):
        # A bare fixed-int-name step -> the captured `__step_N` / ternary arm.
        thir = _lower(_PRELUDE
                      + "def f(n: Int32, s: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(0, n, s):\n        acc = acc + i\n    return acc\n")
        loop = _fn(thir, "f").body[1]
        assert isinstance(loop, THIRForRange) and loop.step_kind == "variable"
        assert isinstance(loop.step, THIRName) and loop.step.name == "s"

    def test_zero_literal_step_is_ineligible(self):
        # A zero literal step panics at runtime; the AST emits the Range ctor
        # path there, not this counter loop -- deferred.
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(0, n, 0):\n        acc = acc + i\n    return acc\n")
        assert _fn(thir, "f") is None

    def test_ctor_literal_step_is_ineligible(self):
        # `Int32(2)` step: the AST folds it via _extract_int_literal (which the
        # slice's _range_bound_literal_value does not), so it is deferred to
        # avoid a variable-vs-literal classification divergence.
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(0, n, Int32(2)):\n        acc = acc + i\n    return acc\n")
        assert _fn(thir, "f") is None

    def test_binop_step_is_ineligible(self):
        # An arithmetic step is deferred (net-confidence, like the bound slice).
        thir = _lower(_PRELUDE
                      + "def f(n: Int32, s: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(0, n, s + 1):\n        acc = acc + i\n    return acc\n")
        assert _fn(thir, "f") is None

    def test_bigint_counter_stepped_is_ineligible(self):
        # A BigInt counter's stepped emit differs (literal-step temp, no
        # overflow check); deferred to the AST path.
        thir = _lower(_PRELUDE
                      + "def f(n: int) -> int:\n    acc = 0\n"
                      + "    for i in range(0, n, 2):\n        acc = acc + i\n    return acc\n")
        assert _fn(thir, "f") is None

    def test_break_in_body_routes(self):
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(n):\n        if i > 3:\n            break\n        acc = acc + i\n"
                      + "    return acc\n")
        assert _fn(thir, "f") is not None

    def test_continue_in_body_routes(self):
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(n):\n        if i > 3:\n            continue\n        acc = acc + i\n"
                      + "    return acc\n")
        assert _fn(thir, "f") is not None

    def test_loop_var_shadowing_outer_is_ineligible(self):
        # A loop var name already bound in the outer scope hits the AST path's
        # was_declared handling (no fresh for-init decl), which the slice skips.
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n    i = 0\n"
                      + "    for i in range(n):\n        acc = acc + i\n    return acc\n")
        assert _fn(thir, "f") is None

    def test_binop_stop_bound_routes(self):
        # An arithmetic stop bound (`range(n + 1)`) hoists into a `__stop_N`
        # temp like a name/len bound; the binop render is target-independent.
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(n + 1):\n        acc = acc + i\n    return acc\n")
        loop = _fn(thir, "f").body[1]
        assert isinstance(loop, THIRForRange)
        assert isinstance(loop.stop, THIRBinOp) and loop.stop_is_literal is False
        assert loop.start is None

    def test_len_minus_one_stop_bound_routes(self):
        # `range(len(xs) - 1)` -- a len() call inside an arithmetic bound.
        thir = _lower(_PRELUDE
                      + "def f(xs: list[Int32]) -> Int32:\n    acc = 0\n"
                      + "    for i in range(len(xs) - 1):\n        acc = acc + xs[i]\n    return acc\n")
        loop = _fn(thir, "f").body[1]
        assert isinstance(loop, THIRForRange) and loop.stop_is_literal is False

    def test_arith_start_and_stop_bounds_route(self):
        # Both bounds arithmetic (`range(a + 1, len(xs) - 1)`) -> two hoisted temps.
        thir = _lower(_PRELUDE
                      + "def f(xs: list[Int32], a: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(a + 1, len(xs) - 1):\n        acc = acc + i\n    return acc\n")
        loop = _fn(thir, "f").body[1]
        assert isinstance(loop, THIRForRange)
        assert loop.start_is_literal is False and loop.stop_is_literal is False

    def test_call_bound_routes(self):
        # A builtin call bound (`range(abs(n))`) routes -- its own param slots
        # thread the render, target-independently.
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(abs(n)):\n        acc = acc + i\n    return acc\n")
        loop = _fn(thir, "f").body[1]
        assert isinstance(loop, THIRForRange) and loop.stop_is_literal is False

    def test_bigint_arith_bound_routes(self):
        # A runtime-BigInt counter with an arithmetic bound shares the step-1
        # emit shape -- the temp renders `::tpy::BigInt`.
        thir = _lower(_PRELUDE
                      + "def f(n: int) -> int:\n    acc = 0\n"
                      + "    for i in range(n - 1):\n        acc = acc + i\n    return acc\n")
        loop = _fn(thir, "f").body[1]
        assert isinstance(loop, THIRForRange) and loop.stop_is_literal is False


class TestIfHoistDeclEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    def _witnesses(self, src: str):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=True))
        return compiler._thir_face_witnesses

    # if/elif/else: one scalar hoist + one str hoist, definitely-assigned
    # after the chain and read past it.
    SRC = (
        _PRELUDE
        + "def f(c: Int32) -> Int32:\n"
        + "    if c == 0:\n        x = 5\n        s = \"a\"\n"
        + "    elif c == 1:\n        x = 7\n        s = \"b\"\n"
        + "    else:\n        x = 9\n        s = \"c\"\n"
        + "    print(s)\n    return x\n"
        + "def main():\n    print(f(0))\nmain()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_predecls_precede_chain(self):
        cpp = self._cpp(self.SRC, thir=True)
        body = cpp[cpp.index("f("):]
        decl = body.index("int32_t x;")
        # predecls sit before the `if (` head, and the in-branch writes are
        # bare reassigns against the slot (no re-declaration).
        assert decl < body.index("if (")
        assert "x = 5;" in body and "int32_t x = 5;" not in body

    def test_witness(self):
        assert self._witnesses(self.SRC).get("if.hoist_decl", 0) > 0


class TestForRangeArithBoundEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _PRELUDE
        + "def f(xs: list[Int32], a: Int32) -> Int32:\n    acc = 0\n"
        + "    for i in range(len(xs) - 1):\n        acc = acc + xs[i]\n"
        + "    for j in range(a + 1, len(xs) - 1):\n        acc = acc + j\n"
        + "    for k in range(abs(a)):\n        acc = acc + k\n"
        + "    return acc\n"
        + "def main():\n    print(f([1, 2, 3], 1))\nmain()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_hoisted_temps(self):
        cpp = self._cpp(self.SRC, thir=True)
        # arithmetic stop bound -> a `__stop_N` temp holding the sub_check expr
        assert "int32_t __stop_0 = (::tpy::sub_check<int32_t>(::tpy::__len__(xs), 1));" in cpp
        # arithmetic start + stop -> both temps for the second loop
        assert "__start_1 = " in cpp and "__stop_1 = " in cpp
        # call bound -> hoisted temp too
        assert "__stop_2 = " in cpp


class TestForRangeSteppedEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _PRELUDE
        + "def f(n: Int32, s: Int32) -> Int32:\n    acc = 0\n"
        + "    for i in range(0, n, 2):\n        acc = acc + i\n"        # literal_pos
        + "    for j in range(n, 0, -3):\n        acc = acc + j\n"       # literal_neg
        + "    for k in range(n, 0, -1):\n        acc = acc + k\n"       # unit_neg
        + "    for m in range(0, n, 1):\n        acc = acc + m\n"        # plus_one
        + "    for p in range(0, n, s):\n        acc = acc + p\n"        # variable
        + "    for q in range(0, 100, 5):\n        acc = acc + q\n"      # both bounds literal
        + "    return acc\n"
        + "def main():\n    print(f(10, 3))\nmain()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_step_shapes(self):
        cpp = self._cpp(self.SRC, thir=True)
        # non-unit literal step -> upfront overflow check + `i += step`
        assert "::tpy::range_check_overflow<int32_t>(0, __stop_0, 2);" in cpp
        assert "i += 2)" in cpp
        # negative non-unit literal -> descending comparison (stop 0 inlined)
        assert "j > 0; j += -3)" in cpp
        # unit steps carry no overflow check / step ref
        assert "k > 0; --k)" in cpp
        assert "m < __stop_3; ++m)" in cpp
        # variable step -> captured temp, nonzero + overflow checks, ternary
        assert "int32_t __step_4 = s;" in cpp
        assert "::tpy::range_check_step_nonzero(__step_4);" in cpp
        assert "__step_4 > 0 ? p < __stop_4 : p > __stop_4; p += __step_4)" in cpp



# --- Statement-shape axis: container iteration (for x in list/set/dict) + len() ---

# `for x in <NativeIterable>:` over a value-scalar element (the begin/end loop, a value
# loop var) routes; a record element (auto&& loop var), str/bytes-key dict, tuple-unpack,
# and generators/user-iterators ride later cells. `len(c)` -> `::tpy::__len__(c)`.
class TestForEachContainer:
    def test_list_scalar_routes(self):
        thir = _lower(
            _PRELUDE
            + "def total(items: list[Int32]) -> Int32:\n    s = 0\n"
            + "    for x in items:\n        s = s + x\n    return s\n")
        fn = _fn(thir, "total")
        assert fn is not None
        loop = fn.body[1]
        assert isinstance(loop, THIRForEach) and loop.var == "x"
        assert isinstance(loop.iterable, THIRName) and loop.iterable.name == "items"

    def test_dict_fixed_int_key_routes(self):
        thir = _lower(
            _PRELUDE
            + "def keysum(d: dict[Int32, Int32]) -> Int32:\n    s = 0\n"
            + "    for k in d:\n        s = s + k\n    return s\n")
        loop = _fn(thir, "keysum").body[1]
        assert isinstance(loop, THIRForEach) and loop.var == "k"

    def test_record_element_routes(self):
        # A `list[record]` element binds `auto&&`/`const auto&` (a borrow alias);
        # field reads are `.field`, like a record param.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(items: list[Inner]) -> Int32:\n    s = 0\n"
            + "    for p in items:\n        s = s + p.value\n    return s\n")
        loop = _fn(thir, "f").body[1]
        assert isinstance(loop, THIRForEach) and loop.var == "p"
        assert isinstance(loop.iterable, THIRName) and loop.iterable.name == "items"

    def test_record_field_mutation_routes(self):
        # Writing through the record loop var (`p.value = ...`) routes -- the alias
        # semantics match Python (the list element is mutated). Sema forbids reassigning
        # the loop var itself, so that divergent case never reaches THIR.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(items: list[Inner]) -> None:\n"
            + "    for p in items:\n        p.value = p.value + 1\n")
        loop = _fn(thir, "f").body[0]
        assert isinstance(loop, THIRForEach) and loop.var == "p"

    def test_dict_record_value_key_iter_routes(self):
        # `for k in d` over dict[int, record] yields scalar KEYS. The
        # `dict[Int32, Inner]` PARAM is admitted by the compositional gate (its
        # by-ref signature is AST-emitted and value-type-neutral), so the whole
        # function -- key loop included -- routes. A body that TOUCHED the record
        # value (`d[k].value`) would still route via the record-value subscript
        # gate; one that did not would reject there.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(d: dict[Int32, Inner]) -> Int32:\n    s = 0\n"
            + "    for k in d:\n        s = s + k\n    return s\n")
        assert _fn(thir, "f") is not None

    def test_str_keyed_dict_iteration_routes(self):
        # An owned-str dict key routes (S5): the loop var is a fresh view var,
        # here usage-resolved to a std::string_view binding.
        thir = _lower(
            _PRELUDE
            + "def f(d: dict[str, Int32]) -> Int32:\n    s = 0\n"
            + "    for k in d:\n        s = s + 1\n    return s\n")
        fn = _fn(thir, "f")
        assert fn is not None
        loop = fn.body[1]
        assert isinstance(loop, THIRForEach)
        assert loop.elem_type.to_cpp() == "std::string_view"

    def test_tuple_unpack_items_routes(self):
        thir = _lower(
            _PRELUDE
            + "def f(d: dict[Int32, Int32]) -> Int32:\n    s = 0\n"
            + "    for k, v in d.items():\n        s = s + k\n    return s\n")
        assert _fn(thir, "f") is not None

    def test_value_tuple_element_routes(self):
        # A `list[tuple[scalar, ...]]` element binds `auto&& t` (the composite
        # borrow alias); subscript reads render `std::get<i>(t)` bare.
        thir, wit = _lower_ctx_witnessed(
            _PRELUDE
            + "def f(xs: list[tuple[Int32, Int32]]) -> None:\n"
            + "    for t in xs:\n        print(t[0])\n        print(t[1])\n")
        loop = _fn(thir, "f").body[0]
        assert isinstance(loop, THIRForEach) and loop.var == "t"
        assert isinstance(loop.elem_type, TupleType)
        assert wit.get("foreach.value_tuple_elem")

    def test_items_single_target_routes(self):
        # `for kv in d.items()` (no unpack) -- the items view is an rvalue
        # capture and the loop var is the whole value tuple.
        thir = _lower(
            _PRELUDE
            + "def f(d: dict[Int32, Int32]) -> None:\n"
            + "    for kv in d.items():\n        print(kv[0])\n        print(kv[1])\n")
        loop = _fn(thir, "f").body[0]
        assert isinstance(loop, THIRForEach) and not loop.iterable_lvalue
        assert isinstance(loop.iterable, THIRMethodCall)

    def test_nested_container_param_routes(self):
        # A `list[list[Int32]]` param routes under the compositional gate (the
        # by-ref signature is AST-emitted and element-neutral, and the element
        # args are fully concrete), and the element gate binds the inner
        # `list[Int32]` loop var byte-identically. The element gate counterpart
        # over a local is below.
        thir = _lower(
            _PRELUDE
            + "def f(xs: list[list[Int32]]) -> Int32:\n    n = 0\n"
            + "    for row in xs:\n        n = n + 1\n    return n\n")
        assert _fn(thir, "f") is not None

    def test_nested_container_local_element_routes(self):
        # Compositional element gate: a `list[list[...]]` local iterated binds
        # the inner list as `auto&&` (the shared loop_var_binding non-value arm,
        # byte-identical to the AST path); the body's own reads of the loop var
        # are gated recursively. No enumerated element family is consulted.
        thir = _lower(
            _PRELUDE
            + "def f() -> Int32:\n    xs = [[1, 2], [3, 4]]\n    n = 0\n"
            + "    for row in xs:\n        n = n + 1\n    return n\n")
        loop = next(s for s in _fn(thir, "f").body if isinstance(s, THIRForEach))
        assert loop.var == "row"

    def test_local_enum_element_routes(self):
        # An enum element (a value type: `Color c = *b`, the value-copy binding
        # arm) was outside the old enumerated whitelist; the compositional gate
        # admits it since the shared loop_var_binding renders it identically.
        thir = _lower_ctx(
            "from tpy import Int32\nfrom enum import Enum\n"
            "class Color(Enum):\n    RED = 0\n    GREEN = 1\n"
            "def f() -> Int32:\n    xs = [Color.RED, Color.GREEN]\n    n = 0\n"
            "    for c in xs:\n        if c == Color.RED:\n            n = n + 1\n"
            "    return n\n")
        loop = next(s for s in _fn(thir, "f").body if isinstance(s, THIRForEach))
        assert loop.var == "c"

    def test_bytes_element_ineligible(self):
        # A bytes-view loop var stays on the AST path: its element `et` survives
        # as a PendingViewType (str is pre-resolved, bytes is not), which THIR
        # would spell before the AST's resolve_type concretizes it -- the one
        # form the compositional gate must still exclude.
        thir = _lower(
            _PRELUDE
            + "def f() -> Int32:\n    rows = [b'ab', b'cd']\n    n = 0\n"
            + "    for r in rows:\n        n = n + 1\n    return n\n")
        assert _fn(thir, "f") is None

    def test_optional_element_ineligible(self):
        # An `Optional` loop var is excluded from the compositional element gate:
        # a body that narrows it (`if item is None: continue`) then reads it needs
        # the value-repr deref (`(*item)`) the loop-var binding does not seed, so
        # THIR renders the narrowed read bare and diverges. The whole-Optional
        # read binds/prints fine, but the gate cannot see the body's narrowing, so
        # it rejects the family. The `list[Int32 | None]` PARAM is still admitted
        # (a `len(items)` body routes); only the iterating body rejects here.
        thir = _lower(
            _PRELUDE
            + "def f(items: list[Int32 | None]) -> Int32:\n    total = 0\n"
            + "    for item in items:\n        if item is None:\n            continue\n"
            + "        total = total + item\n    return total\n")
        assert _fn(thir, "f") is None

    def test_union_element_narrow_routes(self):
        # A narrowable (non-Optional) UNION loop var IS admitted: its isinstance
        # extraction reads the shared `declared` map the loop var populates, so
        # the narrowed read mirrors byte-identically (unlike the Optional deref).
        thir = _lower(
            "from tpy import Int32, Float64\n"
            + "def f(xs: list[Int32 | Float64]) -> Int32:\n    t = 0\n"
            + "    for x in xs:\n        if isinstance(x, Int32):\n            t += x\n"
            + "    return t\n")
        assert _fn(thir, "f") is not None

    def test_len_call_routes(self):
        thir = _lower(
            _PRELUDE
            + "def ln(xs: list[Int32]) -> Int32:\n    return len(xs)\n")
        call = _fn(thir, "ln").body[0].value
        assert isinstance(call, THIRCall) and call.native_name == "tpy::__len__"

    def test_range_len_routes(self):
        # `for i in range(len(xs))` -- len as a range bound; composes with the
        # container subscript read (and lights up its bounds-safe branch).
        thir = _lower(
            _PRELUDE
            + "def f(xs: list[Int32]) -> Int32:\n    n = 0\n"
            + "    for i in range(len(xs)):\n        n = n + xs[i]\n    return n\n")
        assert _fn(thir, "f") is not None

    def test_user_len_is_not_native_dispatched(self):
        # A user function named `len` has native_name None, so the emit keeps it a plain
        # call -- the len dispatch keys on the resolved symbol, not the source name.
        _, modules = _compile(
            _PRELUDE
            + "def len(x: Int32) -> Int32:\n    return x\n"
            + "def f(y: Int32) -> Int32:\n    return len(y)\n")
        entry = _entry(modules)
        f = next(fn for fn in entry.ast.functions if fn.name == "f")
        call = f.body[0].value
        assert isinstance(call, TpyCall) and not _is_len_native(call)

    def test_len_on_pointer_local_record_ineligible(self):
        # Regression: len() is gated on container type. A record with __len__ bound to a
        # reseated pointer-local emits bare `::tpy::__len__(p)`, but the AST derefs it
        # (`(*p)`); admitting it would break the byte-identical contract (a g++ error).
        src = (
            "from tpy import Int32\n"
            "class Bag:\n    data: list[Int32]\n"
            "    def __init__(self, d: list[Int32]) -> None:\n        self.data = d\n"
            "    def __len__(self) -> Int32:\n        return len(self.data)\n"
            "class Two:\n    a: Bag\n    b: Bag\n"
            "    def __init__(self, a: Bag, b: Bag) -> None:\n"
            "        self.a = a\n        self.b = b\n"
            "def pick(o: Two, flag: bool) -> Int32:\n"
            "    p = o.a\n    if flag:\n        p = o.b\n    return len(p)\n")
        assert _fn(_lower_ctx(src), "pick") is None



class TestForEachContainerEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _PRELUDE
        + "def total(items: list[Int32]) -> Int32:\n    s = 0\n"
        + "    for x in items:\n        s = s + x\n    return s\n"
        + "def count_pos(xs: list[Int32]) -> Int32:\n    n = 0\n"
        + "    for i in range(len(xs)):\n        if xs[i] > 0:\n            n = n + 1\n"
        + "    return n\n"
        + "def main():\n    print(total([1, 2, 3]))\n    print(count_pos([1, -2, 3]))\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_begin_end_loop(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "auto& __obj_0 = items;" in cpp
        assert "auto __beg_0 = __obj_0.begin();" in cpp
        assert "for (; __beg_0 != __end_0; ++__beg_0) {" in cpp
        assert "int32_t x = *__beg_0;" in cpp

    def test_emits_len_and_reaches_bounds_safe(self):
        # len -> ::tpy::__len__, hoisted into the range temp; the routed range(len)
        # loop makes the container subscript's bounds-safe branch live.
        cpp = self._cpp(self.SRC, thir=True)
        assert "::tpy::__len__(xs)" in cpp
        assert "xs[static_cast<std::size_t>(i)]" in cpp

    def test_mixed_foreach_range_counter_parity(self):
        # A for-each then a range-for in one function shares the per-function loop-index
        # counter; the numbering must stay in sync with the AST (for-each -> __obj_0,
        # the following range-for -> __stop_1).
        src = (
            _PRELUDE
            + "def f(items: list[Int32], n: Int32) -> Int32:\n    s = 0\n"
            + "    for x in items:\n        s = s + x\n"
            + "    for i in range(n):\n        s = s + i\n    return s\n"
            + "def main():\n    print(f([1, 2], 3))\nmain()\n")
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
        cpp = self._cpp(src, thir=True)
        assert "auto& __obj_0 = items;" in cpp and "__stop_1 = n;" in cpp

    def test_dump_for_each(self):
        thir = _lower(
            _PRELUDE
            + "def total(items: list[Int32]) -> Int32:\n    s = 0\n"
            + "    for x in items:\n        s = s + x\n    return s\n")
        assert "for %x in %items:" in dump_thir(thir)

    # A `list[record]` for-each: the loop var is a borrow alias (`const auto&` when
    # read-only, `auto&&` when the body mutates through it), field reads are `.field`.
    REC_SRC = (
        _F1_RECORDS
        + "def total(items: list[Inner]) -> Int32:\n    s = 0\n"
        + "    for p in items:\n        s = s + p.value\n    return s\n"
        + "def bump(items: list[Inner]) -> None:\n"
        + "    for p in items:\n        p.value = p.value + 1\n"
        + "def main():\n    xs = [Inner(1), Inner(2)]\n    bump(xs)\n    print(total(xs))\nmain()\n"
    )

    def test_record_byte_identical(self):
        assert self._cpp(self.REC_SRC, thir=True) == self._cpp(self.REC_SRC, thir=False)

    def test_record_const_loop_var_binding(self):
        # Read-only loop var -> `const auto&` (the const_loop_var thread; hardcoded False
        # would wrongly emit `auto&&` here). Field read is `.value` (dot, like a param).
        cpp = self._cpp(self.REC_SRC, thir=True)
        assert "const auto& p = *__beg_0;" in cpp
        assert "s = (::tpy::add_check<int32_t>(s, p.value));" in cpp

    def test_record_mutating_loop_var_binding(self):
        # Mutation through the loop var -> `auto&&` (a non-const alias); the write
        # `p.value = ...` goes through the reference, aliasing the list element.
        cpp = self._cpp(self.REC_SRC, thir=True)
        assert "auto&& p = *__beg_0;" in cpp

    def test_nested_record_for_each(self):
        # A `list[record]` loop nested inside another routes (both loops), and the
        # per-function loop-index counter stays in sync with the AST (__obj_0 outer,
        # __obj_1 inner). Asserting routing keeps the byte-identity check non-vacuous.
        src = (
            _F1_RECORDS
            + "def pair_sum(xs: list[Inner], ys: list[Inner]) -> Int32:\n    s = 0\n"
            + "    for a in xs:\n        for b in ys:\n"
            + "            s = s + a.value + b.value\n    return s\n"
            + "def main():\n    print(pair_sum([Inner(1)], [Inner(2)]))\nmain()\n")
        outer = _fn(_lower_ctx(src), "pair_sum").body[1]
        assert isinstance(outer, THIRForEach) and isinstance(outer.body[0], THIRForEach)
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
        cpp = self._cpp(src, thir=True)
        assert "auto& __obj_0 = xs;" in cpp and "auto& __obj_1 = ys;" in cpp

    def test_ctor_list_record_param_for_each(self):
        # Admitting `list[record]` params also broadens CONSTRUCTOR eligibility: a ctor
        # whose body iterates a `list[record]` param (the loop demotes into the ctor
        # tail) routes too. Non-vacuous: the ctor lowers (not None) + byte-identical.
        src = (
            _F1_RECORDS
            + "class Sum:\n    total: Int32\n"
            + "    def __init__(self, items: list[Inner]):\n        self.total = 0\n"
            + "        for it in items:\n            self.total = self.total + it.value\n"
            + "def main():\n    s = Sum([Inner(1), Inner(2)])\n    print(s.total)\nmain()\n")
        assert _lower_ctor(src, "Sum") is not None
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    # A value-tuple element loop over a `list[tuple[...]]` name and over a
    # `d.items()` view. Both bind the whole tuple as `auto&& t = *__beg_0;`;
    # the items view is an owning rvalue capture (`::tpy::dict_items(d)`).
    TUP_SRC = (
        _PRELUDE
        + "def pairs(xs: list[tuple[Int32, Int32]]) -> None:\n"
        + "    for t in xs:\n        print(t[0])\n        print(t[1])\n"
        + "def entries(d: dict[Int32, Int32]) -> None:\n"
        + "    for kv in d.items():\n        print(kv[0])\n        print(kv[1])\n"
        + "def main():\n"
        + "    pairs([(1, 2), (3, 4)])\n"
        + "    d: dict[Int32, Int32] = {5: 6}\n    entries(d)\nmain()\n"
    )

    def test_value_tuple_byte_identical(self):
        assert self._cpp(self.TUP_SRC, thir=True) == self._cpp(self.TUP_SRC, thir=False)

    def test_value_tuple_emits_alias_binding(self):
        cpp = self._cpp(self.TUP_SRC, thir=True)
        assert "auto& __obj_0 = xs;" in cpp
        assert "auto&& t = *__beg_0;" in cpp
        # The items view is an owning capture (rvalue), and its tuple loop var
        # binds the same `auto&&` alias.
        assert "auto __obj_0 = ::tpy::dict_items(d);" in cpp
        assert "auto&& kv = *__beg_0;" in cpp



# --- Statement-shape axis: expression statements (print + bare eligible call) ---

class TestPrintStmt:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    def test_str_literal_and_scalar_route(self):
        thir = _lower(
            _PRELUDE
            + "def f(n: Int32) -> None:\n    print(\"n =\", n)\n")
        stmt = _fn(thir, "f").body[0]
        assert isinstance(stmt, THIRPrint) and len(stmt.args) == 2
        assert isinstance(stmt.args[0].expr, THIRStrLiteral)
        assert stmt.args[0].print_form is PrintForm.RAW
        assert stmt.args[1].print_form is PrintForm.RAW

    def test_arg_forms(self):
        # bool -> BOOL, double -> FLOAT, 8-bit int -> INT8, wider int -> RAW.
        thir = _lower(
            _PRELUDE
            + "def f(ok: bool, r: float, b: UInt8, n: Int32) -> None:\n"
            + "    print(ok, r, b, n)\n")
        forms = [a.print_form for a in _fn(thir, "f").body[0].args]
        assert forms == [PrintForm.BOOL, PrintForm.FLOAT, PrintForm.INT8, PrintForm.RAW]

    def test_empty_print_routes(self):
        thir = _lower(_PRELUDE + "def f() -> None:\n    print()\n")
        stmt = _fn(thir, "f").body[0]
        assert isinstance(stmt, THIRPrint) and stmt.args == ()

    def test_bare_call_stmt_routes(self):
        # A same-module free-function call discarded for side effects (void return).
        thir = _lower(
            _PRELUDE
            + "def g(n: Int32) -> None:\n    print(n)\n"
            + "def f(n: Int32) -> None:\n    g(n)\n")
        stmt = _fn(thir, "f").body[0]
        assert isinstance(stmt, THIRExprStmt) and isinstance(stmt.expr, THIRCall)

    def test_scalar_returning_call_stmt_routes(self):
        # A discarded scalar return also routes as a bare statement.
        thir = _lower(
            _PRELUDE
            + "def g(n: Int32) -> Int32:\n    return n\n"
            + "def f(n: Int32) -> None:\n    g(n)\n")
        assert isinstance(_fn(thir, "f").body[0], THIRExprStmt)

    def test_str_var_arg_routes(self):
        # A str variable streams raw like the AST's is_any_str_type arm.
        thir = _lower(
            "from tpy import Int32\n"
            + "def f(s: str) -> None:\n    print(s)\n")
        fn = _fn(thir, "f")
        assert fn is not None
        arg = fn.body[0].args[0]
        assert arg.print_form is PrintForm.RAW
        assert isinstance(arg.expr, THIRName) and arg.expr.form is Form.BORROW

    def test_bigint_arg_routes_raw(self):
        # A BigInt print arg streams raw (operator<<), like the AST's
        # runtime-bigint arm.
        thir = _lower("def f(x: int) -> None:\n    print(x)\n")
        assert _fn(thir, "f") is not None

    def test_optval_scalar_param_plain(self):
        # `print(p)` on an un-narrowed value-repr Optional[scalar] param -> the
        # plain `::tpy::print_optional_val(p)` over the bare optional (no deref).
        for ann in ("Int32 | None", "int | None", "Char | None"):
            thir = _lower(
                "from tpy import Int32, Char\n"
                + f"def f(p: {ann}) -> None:\n    print(p)\n")
            arg = _fn(thir, "f").body[0].args[0]
            assert arg.print_form is PrintForm.OPT_VAL
            assert arg.opt_inner_cpp is None
            assert isinstance(arg.expr, THIRName) and arg.expr.name == "p"
            assert not arg.expr.deref

    def test_optval_bool_templated(self):
        thir = _lower(_PRELUDE + "def f(p: bool | None) -> None:\n    print(p)\n")
        arg = _fn(thir, "f").body[0].args[0]
        assert arg.print_form is PrintForm.OPT_VAL_BOOL
        assert arg.opt_inner_cpp == "bool"

    def test_optval_float_templated(self):
        # Both float widths take the float branch; the inner C++ spelling
        # distinguishes them (double vs float), no static_cast (the wrapper
        # converts internally).
        thir = _lower(_PRELUDE + "def f(p: float | None) -> None:\n    print(p)\n")
        arg = _fn(thir, "f").body[0].args[0]
        assert arg.print_form is PrintForm.OPT_VAL_FLOAT
        assert arg.opt_inner_cpp == "double"
        thir32 = _lower(
            "from tpy import Float32\n"
            + "def f(p: Float32 | None) -> None:\n    print(p)\n")
        arg32 = _fn(thir32, "f").body[0].args[0]
        assert arg32.print_form is PrintForm.OPT_VAL_FLOAT
        assert arg32.opt_inner_cpp == "float"

    def test_optval_str_param_plain(self):
        # A value-repr Optional[str] param (`std::optional<std::string_view>`)
        # prints via the plain form too.
        thir = _lower("def f(p: str | None) -> None:\n    print(p)\n")
        arg = _fn(thir, "f").body[0].args[0]
        assert arg.print_form is PrintForm.OPT_VAL
        assert arg.opt_inner_cpp is None

    def test_optval_field_routes(self):
        # An un-narrowed value-repr Optional field read prints via
        # `print_optional_val(this->fi)` (bare std::optional storage).
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "class C:\n"
            "    fi: Int32 | None\n"
            "    def __init__(self) -> None:\n        self.fi = None\n"
            "    def show(self) -> None:\n        print(self.fi)\n")
        arg = _fn(thir, "show").body[0].args[0]
        assert arg.print_form is PrintForm.OPT_VAL
        assert isinstance(arg.expr, THIRFieldAccess)

    def test_optval_witnessed(self):
        _, w = _lower_ctx_witnessed(
            _PRELUDE + "def f(p: Int32 | None) -> None:\n    print(p)\n")
        assert w.get("print.optval", 0) >= 1

    def test_optval_bool_emit_mirrors_ast(self):
        src = _PRELUDE + "def f(p: bool | None) -> None:\n    print(p)\n"
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
        assert "print_optional_val<::tpy::print_bool, bool>(p)" in self._cpp(src, thir=True)

    def test_optval_float_emit_mirrors_ast(self):
        src = _PRELUDE + "def f(p: float | None) -> None:\n    print(p)\n"
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
        assert "print_optional_val<::tpy::print_float, double>(p)" in self._cpp(src, thir=True)

    def test_narrowed_optval_print_defers(self):
        # A NARROWED read (`if p is not None: print(p)`) resolves to the inner
        # scalar; its AST render is the deref `(*p)`, a separate face. The body
        # must stay on the AST path (not silently route the whole-optional wrap).
        thir = _lower(
            _PRELUDE
            + "def f(p: Int32 | None) -> None:\n"
            + "    if p is not None:\n        print(p)\n")
        assert _fn(thir, "f") is None

    def test_kwargs_ineligible(self):
        # sep=/end=/file=/flush= take gen_print's richer path -> AST.
        thir = _lower(
            _PRELUDE
            + "def f(n: Int32) -> None:\n    print(n, end=\"\")\n")
        assert _fn(thir, "f") is None

    def test_container_arg_ineligible(self):
        thir = _lower(
            _PRELUDE
            + "def f(xs: list[Int32]) -> None:\n    print(xs)\n")
        assert _fn(thir, "f") is None

    def test_shadowed_print_ineligible(self):
        # A user function named `print` is not the builtin; conservatively stays AST
        # (never misrouted to the stream emit).
        thir = _lower(
            _PRELUDE
            + "def print(n: Int32) -> None:\n    return\n"
            + "def f(n: Int32) -> None:\n    print(n)\n")
        assert _fn(thir, "f") is None

    def test_byte_identical(self):
        src = (
            _PRELUDE
            + "def report(n: Int32, ok: bool, r: float, b: UInt8) -> None:\n"
            + "    print(\"n =\", n)\n    print(n, ok, r)\n    print(b)\n    print()\n"
            + "    blank()\n"
            + "def blank() -> None:\n    print(\"--\")\n"
            + "def main():\n    report(3, True, 1.5, 7)\nmain()\n")
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
        cpp = self._cpp(src, thir=True)
        assert 'std::cout << "n =" << " " << n << "\\n";' in cpp
        assert 'std::cout << n << " " << ::tpy::print_bool(ok) << " " << ::tpy::print_float(r) << "\\n";' in cpp
        assert 'std::cout << static_cast<int>(b) << "\\n";' in cpp
        assert 'std::cout << "\\n";' in cpp
        assert "blank();" in cpp

    def test_dump(self):
        thir = _lower(_PRELUDE + "def f(n: Int32) -> None:\n    print(\"x\", n)\n")
        assert "print(str('x') [raw], %n [raw])" in dump_thir(thir)

    def test_native_call_arg_ineligible(self):
        # A @native function is ::-qualified at the call site; the bare THIRCall
        # emit can't reproduce that, so a print with a native-call arg stays AST.
        thir = _lower(
            "from tpy.extern import native\nfrom tpy import Int32\n"
            + "@native\ndef ext() -> Int32: ...\n"
            + "def f() -> None:\n    print(ext())\n")
        assert _fn(thir, "f") is None

    def test_native_bare_call_ineligible(self):
        thir = _lower(
            "from tpy.extern import native\n"
            + "@native\ndef ext() -> None: ...\n"
            + "def f() -> None:\n    ext()\n")
        assert _fn(thir, "f") is None

    def test_export_c_call_ineligible(self):
        # An @export(binding="C") function has EXPORT_C linkage and emits its raw
        # extern-C symbol at the call site (not a bare name); the fi.linkage gate
        # keeps a caller of it on the AST path. Unlike a plain @native (caught by
        # the native_function/native_name check), EXPORT_C has a body and only the
        # linkage gate excludes it.
        thir = _lower(
            "from tpy.extern import export\nfrom tpy import Int32\n"
            + "@export(binding=\"C\")\ndef ext(x: Int32) -> Int32:\n    return x\n"
            + "def f(n: Int32) -> Int32:\n    return ext(n)\n")
        assert _fn(thir, "f") is None

    def test_desugar_shares_one_source_comment(self):
        # A tuple-unpack desugars to several assigns on one source line; only the
        # first carries the source comment (no_source_comment set on the rest), so
        # the emitter doesn't repeat it -- byte-identical to the AST path.
        thir = _lower(
            _PRELUDE
            + "def f(p: Int32, q: Int32) -> Int32:\n    a = p\n    b = q\n"
            + "    a, b = b, a\n    return a + b\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert sum(1 for s in fn.body if s.no_source_comment) == 3



class TestFloat:
    def test_float_param_return_local_eligible(self):
        thir = _lower("def f(a: float, b: float) -> float:\n    c = a + b\n    return c\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert fn.params[0].type.to_cpp() == "double"
        assert isinstance(fn.body[0], THIRVarDecl)
        assert fn.body[0].resolved_type.to_cpp() == "double"

    def test_float_arith_and_literal_eligible(self):
        thir = _lower("def f(a: float) -> float:\n    return a * 2.0 - 1.5\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[0].value, THIRBinOp) and fn.body[0].value.op == "-"

    def test_float_literal_local_eligible(self):
        thir = _lower("def f() -> float:\n    x = 2.5\n    return x\n")
        fn = _fn(thir, "f")
        assert fn is not None and fn.body[0].resolved_type.to_cpp() == "double"

    def test_float_comparison_condition_eligible(self):
        thir = _lower("def f(a: float, b: float) -> float:\n    r = a\n"
                      "    if a < b:\n        r = b\n    return r\n")
        assert _fn(thir, "f") is not None

    def test_float_comparison_as_value_eligible(self):
        # _binop_eligible admits comparisons for any eligible scalar incl. float.
        thir = _lower("def f(a: float, b: float) -> bool:\n    r = a < b\n    return r\n")
        assert _fn(thir, "f") is not None

    def test_float_truediv_is_ineligible(self):
        # `/` (true division) has AST op `div`, absent from _ARITH_OPS (which
        # lists `/`, a token the parser never emits), so _binop_eligible rejects
        # it -- AST path. (truediv DOES carry a `::tpy::truediv` cpp_template;
        # contrast `//`, op `//`, which is eligible. See TODO re: enabling it.)
        thir = _lower("def f(a: float, b: float) -> float:\n    return a / b\n")
        assert _fn(thir, "f") is None

    def test_float32_values_route(self):
        # Float32 params/locals/returns are eligible scalars (`float`); the
        # `f`-suffix literal render rides the float_literal_to_float32 coerce.
        thir = _lower("from tpy import Float32\n"
                      "def f(a: Float32) -> Float32:\n    b = a\n    return b\n")
        fn = _fn(thir, "f")
        assert fn is not None and fn.params[0].type.to_cpp() == "float"



class TestBool:
    def test_bool_param_return_local_eligible(self):
        thir = _lower("def f(a: bool) -> bool:\n    b = a\n    return b\n")
        fn = _fn(thir, "f")
        assert fn is not None and fn.params[0].type.to_cpp() == "bool"

    def test_comparison_as_value_eligible(self):
        # A comparison used as a value (`r = x < y`), not just an if-condition.
        thir = _lower(_PRELUDE + "def f(x: Int32, y: Int32) -> bool:\n"
                      "    r = x < y\n    return r\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[0], THIRVarDecl)
        assert fn.body[0].resolved_type.to_cpp() == "bool"
        assert isinstance(fn.body[0].init, THIRBinOp) and fn.body[0].init.op == "<"

    def test_mixed_sign_comparison_is_ineligible(self):
        # A signed-vs-unsigned comparison emits std::cmp_* (not a bare operator),
        # so it stays on the AST path -- as a value and as a condition.
        thir = _lower(_PRELUDE + "from tpy import UInt32\n"
                      "def f(a: Int32, b: UInt32) -> bool:\n    return a < b\n")
        assert _fn(thir, "f") is None

    def test_retro_widened_seed_compare_routes(self):
        # `offset = 0` retro-widens to UInt64 from the take_u64 arg slot; the
        # `offset < limit` compare is then same-sign and must NOT be excluded by
        # the mixed-sign gate (regression guard for the resolved-local-type
        # tracking -- analyzer.get_expr_type(offset) is the signed seed).
        thir = _lower("from tpy import UInt64\n"
                      "def take_u64(x: UInt64) -> UInt64:\n    return x\n"
                      "def f(limit: UInt64) -> UInt64:\n    offset = 0\n"
                      "    while offset < limit:\n        offset = take_u64(offset) + 1\n"
                      "    return offset\n")
        assert _fn(thir, "f") is not None

    def test_bool_literal_eligible(self):
        thir = _lower("def f() -> bool:\n    ok = True\n    return ok\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[0].init, THIRLiteral) and fn.body[0].init.value is True

    def test_comparison_as_call_arg_eligible(self):
        # comparison-as-value reaching a same-module call arg: g(x < y).
        thir = _lower(_PRELUDE
                      + "def g(b: bool) -> bool:\n    return b\n"
                      + "def f(x: Int32, y: Int32) -> bool:\n    return g(x < y)\n")
        assert _fn(thir, "f") is not None

    def test_bare_bool_condition_eligible(self):
        thir = _lower(_PRELUDE + "def f(x: Int32, done: bool) -> Int32:\n    r = x\n"
                      "    if done:\n        r = 0\n    return r\n")
        fn = _fn(thir, "f")
        assert fn is not None and isinstance(fn.body[1], THIRIf)
        assert isinstance(fn.body[1].condition, THIRName)
        assert fn.body[1].condition.name == "done"

    def test_bare_bool_while_condition_eligible(self):
        thir = _lower(_PRELUDE + "def f(go: bool) -> Int32:\n    r = 0\n"
                      "    while go:\n        r = r + 1\n    return r\n")
        fn = _fn(thir, "f")
        assert fn is not None and isinstance(fn.body[1], THIRWhile)
        assert isinstance(fn.body[1].condition, THIRName)
        assert fn.body[1].condition.name == "go"

    def test_bool_literal_condition_is_ineligible(self):
        # `if True:` is excluded -- the AST path may dead-branch-eliminate a
        # bool-literal condition, which a bare `if (true)` would not reproduce.
        thir = _lower("def f(a: bool) -> bool:\n    r = a\n"
                      "    if True:\n        r = a\n    return r\n")
        assert _fn(thir, "f") is None



class TestBoolOps:
    def test_logical_and_routes(self):
        # Bool-result `and` over bool operands: the bare-operator emit arm.
        thir = _lower("def f(a: bool, b: bool) -> bool:\n    return a and b\n")
        fn = _fn(thir, "f")
        assert fn is not None
        v = fn.body[0].value
        assert isinstance(v, THIRBinOp) and v.op == "&&" and v.resolved is None

    def test_logical_or_routes(self):
        # `or` alongside `and` -- a separate guard so a future edit gating one
        # operator can't silently drop the other.
        thir = _lower("def f(a: bool, b: bool) -> bool:\n    return a or b\n")
        fn = _fn(thir, "f")
        assert fn is not None and fn.body[0].value.op == "||"

    def test_not_routes(self):
        thir = _lower("def f(a: bool) -> bool:\n    return not a\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[0].value, THIRUnaryNot)

    def test_and_or_not_condition_routes(self):
        thir = _lower(_PRELUDE + "def f(a: Int32, b: Int32, flag: bool) -> Int32:\n"
                      "    if a < b and flag:\n        return 1\n"
                      "    while not flag or a == b:\n        a = a + 1\n"
                      "    return a\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[0], THIRIf) and fn.body[0].condition.op == "&&"
        assert isinstance(fn.body[1], THIRWhile) and fn.body[1].condition.op == "||"
        assert isinstance(fn.body[1].condition.left, THIRUnaryNot)

    def test_value_semantics_or_is_ineligible(self):
        # `n or 5` (non-bool result) takes _gen_logical_value's Python operand
        # semantics (temp + ternary `(n ? n : 5)`), not the bare operator.
        thir = _lower(_PRELUDE + "def f(n: Int32) -> Int32:\n    return n or 5\n")
        assert _fn(thir, "f") is None

    def test_non_bool_operand_is_ineligible(self):
        # An int operand under a bool result (`flag and n`) renders through
        # truthiness reasoning the slice does not carry -- stays on the AST path.
        thir = _lower(_PRELUDE
                      + "def f(flag: bool, n: Int32) -> bool:\n    return flag and n\n")
        assert _fn(thir, "f") is None

    def test_int_truthiness_not_is_ineligible(self):
        # `not n` (int operand) is bool-result but truthy-wraps the operand.
        thir = _lower(_PRELUDE + "def f(n: Int32) -> bool:\n    return not n\n")
        assert _fn(thir, "f") is None

    def test_unary_minus_is_ineligible(self):
        # The arithmetic unaries take the resolved_unaryop emit path.
        thir = _lower(_PRELUDE + "def f(n: Int32) -> Int32:\n    m = n\n    return -m\n")
        assert _fn(thir, "f") is None

    def test_mixed_sign_compare_operand_is_ineligible(self):
        # The mixed-sign gate applies inside a logical operand too (the pair
        # would emit std::cmp_*, not the bare operator).
        thir = _lower(_PRELUDE + "from tpy import UInt32\n"
                      "def f(a: Int32, b: UInt32, flag: bool) -> bool:\n"
                      "    return flag and a < b\n")
        assert _fn(thir, "f") is None

    def test_record_operand_compare_is_ineligible(self):
        # A record compare also reaches the rb=None bare-operator arm (a user
        # dunder has no template), but its operands need gen_expr_deref's
        # indirection -- `self` renders `(*this)`. The @total_ordering-
        # synthesized `not (self <= other)` bodies pinned this divergence.
        thir = _lower_ctx(
            _PRELUDE
            + "class C:\n"
            + "    n: Int32\n"
            + "    def __init__(self, n: Int32):\n        self.n = n\n"
            + "    def __le__(self, other: C) -> bool:\n"
            + "        return self.n <= other.n\n"
            + "    def gt(self, other: C) -> bool:\n"
            + "        return not (self <= other)\n")
        assert _fn(thir, "gt") is None



class TestBoolOpsEmit:
    def test_emit_and_or_not(self):
        thir = _lower(_PRELUDE + "def f(a: Int32, b: Int32, flag: bool) -> bool:\n"
                      "    x = flag and not (a < b)\n    return x or flag\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    bool x = (flag && (!((a < b))));\n"
            "    return (x || flag);\n")

    def test_emit_not_nested_and_double_not(self):
        thir = _lower("def f(a: bool, b: bool) -> bool:\n"
                      "    x = not (a and b)\n    return not not x\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    bool x = (!((a && b)));\n"
            "    return (!((!(x))));\n")

    def test_emit_condition_matches_value_render(self):
        # Condition position reuses the value render (`gen_truthy_expr` reduces
        # to it for the admitted bool shapes).
        thir = _lower(_PRELUDE + "def f(a: Int32, b: Int32, flag: bool) -> Int32:\n"
                      "    if a < b and flag:\n        return 1\n"
                      "    while not flag:\n        a = a + 1\n"
                      "    return a\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    if (((a < b) && flag)) {\n"
            "        return 1;\n"
            "    }\n"
            "    while ((!(flag))) {\n"
            "        a = (::tpy::add_check<int32_t>(a, 1));\n"
            "    }\n"
            "    return a;\n")

    def test_emit_bool_print_arg(self):
        thir = _lower("def f(x: bool, y: bool):\n    print(x and y, not x)\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    std::cout << ::tpy::print_bool((x && y)) << \" \" "
            "<< ::tpy::print_bool((!(x))) << \"\\n\";\n")



class TestChainedCompare:
    def test_simple_chain_routes(self):
        # Simple (name) intermediate -> the inline arm: a left-folded && of the
        # sema pairs.
        thir = _lower(_PRELUDE + "def f(a: Int32, b: Int32, c: Int32) -> bool:\n"
                      "    return a < b < c\n")
        fn = _fn(thir, "f")
        assert fn is not None
        v = fn.body[0].value
        assert isinstance(v, THIRBinOp) and v.op == "&&" and v.resolved is None
        assert v.left.op == "<" and v.right.op == "<"

    def test_four_operand_chain_routes(self):
        thir = _lower(_PRELUDE + "def f(a: Int32, b: Int32, c: Int32, d: Int32) -> bool:\n"
                      "    return a < b <= c < d\n")
        fn = _fn(thir, "f")
        assert fn is not None
        v = fn.body[0].value          # ((p0 && p1) && p2)
        assert v.op == "&&" and v.left.op == "&&" and v.right.op == "<"

    def test_complex_endpoints_route(self):
        # Endpoints may be non-simple (evaluated once); only INTERMEDIATES
        # trigger the statement-expr arm.
        thir = _lower(_PRELUDE + "def f(a: Int32, b: Int32, c: Int32) -> bool:\n"
                      "    return a + 1 < b < c + 2\n")
        assert _fn(thir, "f") is not None

    def test_complex_intermediate_is_ineligible(self):
        # A non-simple intermediate (`b + 1`) binds a `_cmp1` temp inside a GCC
        # statement expression (`({ auto&& _cmp1 = ...; ... })`) -> AST path.
        thir = _lower(_PRELUDE + "def f(a: Int32, b: Int32, c: Int32) -> bool:\n"
                      "    return a < b + 1 < c\n")
        assert _fn(thir, "f") is None

    def test_call_intermediate_is_ineligible(self):
        # A call intermediate (`len(xs)`) is non-simple -> statement-expr arm.
        thir = _lower(_PRELUDE + "def f(a: Int32, c: Int32) -> bool:\n"
                      "    xs = [1, 2, 3]\n    return a < len(xs) < c\n")
        assert _fn(thir, "f") is None

    def test_mixed_sign_pair_is_ineligible(self):
        # Each pair gets the single-comparison gates (mixed-sign -> std::cmp_*).
        thir = _lower(_PRELUDE + "from tpy import UInt32\n"
                      "def f(a: Int32, b: UInt32, c: UInt32) -> bool:\n"
                      "    return a < b < c\n")
        assert _fn(thir, "f") is None

    def test_chain_condition_routes(self):
        thir = _lower(_PRELUDE + "def f(i: Int32, n: Int32) -> Int32:\n"
                      "    if 0 <= i < n:\n        return i\n    return 0\n")
        fn = _fn(thir, "f")
        assert fn is not None and isinstance(fn.body[0], THIRIf)
        assert fn.body[0].condition.op == "&&"



class TestChainedCompareEmit:
    def test_emit_inline_chain(self):
        thir = _lower(_PRELUDE + "def f(a: Int32, b: Int32, c: Int32) -> bool:\n"
                      "    return a < b < c\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == "    return ((a < b) && (b < c));\n"

    def test_emit_four_operand_chain(self):
        thir = _lower(_PRELUDE + "def f(a: Int32, b: Int32, c: Int32, d: Int32) -> bool:\n"
                      "    return a < b < c < d\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == "    return (((a < b) && (b < c)) && (c < d));\n"

    def test_emit_chain_condition(self):
        thir = _lower(_PRELUDE + "def f(i: Int32, n: Int32) -> Int32:\n"
                      "    if 0 <= i < n:\n        return i\n    return 0\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    if (((0 <= i) && (i < n))) {\n"
            "        return i;\n"
            "    }\n"
            "    return 0;\n")



class TestDump:
    def test_dump_format(self):
        thir = _lower(_PRELUDE + "def f(a: Int32) -> Int32:\n    b = a\n    return b\n")
        assert dump_thir(thir) == (
            "fn f(a: Int32) -> Int32:\n"
            "  %b: Int32 = %a\n"
            "  return %b\n"
        )

    def test_dump_empty(self):
        # True division (AST op `div`) is outside the slice, so nothing routes.
        thir = _lower("def f(a: float) -> float:\n    return a / a\n")
        assert "(no THIR-eligible functions)" in dump_thir(thir)

    def test_dump_for_range(self):
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    total = 0\n"
                      + "    for i in range(n):\n        total = total + i\n    return total\n")
        assert dump_thir(thir) == (
            "fn f(n: Int32) -> Int32:\n"
            "  %total: Int32 = lit(0)\n"
            "  for %i in range(0, %n):\n"
            "    %total = binop(%total, +, %i)\n"
            "  return %total\n"
        )

    def test_dump_storage_none(self):
        # An F2c None return surfaces the STORAGE form tag on the None literal
        # (the tag selects std::nullopt vs nullptr at emit).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def nothing() -> Own[Inner] | None:\n    return None\n")
        assert "return lit(None) [storage]" in dump_thir(thir)



class TestEmit:
    def test_emit_body_no_comments(self):
        thir = _lower(_PRELUDE + "def f(a: Int32) -> Int32:\n    b = a\n    return b\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == "    int32_t b = a;\n    return b;\n"

    def test_emit_binop_overflow_checked(self):
        thir = _lower(_PRELUDE + "def f(a: Int32, b: Int32) -> Int32:\n    return a + b + 1\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    return (::tpy::add_check<int32_t>"
            "((::tpy::add_check<int32_t>(a, b)), 1));\n")

    def test_emit_binop_div_floor_when_divisor_nonzero(self):
        thir = _lower(_PRELUDE + "def f(a: Int32) -> Int32:\n    return a // 2\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == "    return (::tpy::div_floor<int32_t>(a, 2));\n"

    def test_emit_binop_div_check_with_runtime_divisor(self):
        # A non-literal divisor is not proven non-zero -> checked div helper.
        thir = _lower(_PRELUDE + "def f(a: Int32, b: Int32) -> Int32:\n    return a // b\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == "    return (::tpy::div_check<int32_t>(a, b));\n"

    def test_emit_call_with_args(self):
        thir = _lower(_PRELUDE
                      + "def g(a: Int32, b: Int32) -> Int32:\n    return a + b\n"
                      + "def f(a: Int32) -> Int32:\n    return g(a, a + 1)\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == "    return g(a, (::tpy::add_check<int32_t>(a, 1)));\n"

    def test_emit_while(self):
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    i = 0\n"
                      + "    while i < n:\n        i = i + 1\n    return i\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    int32_t i = 0;\n"
            "    while ((i < n)) {\n"
            "        i = (::tpy::add_check<int32_t>(i, 1));\n"
            "    }\n"
            "    return i;\n"
        )

    def test_emit_range_stop(self):
        # range(name): bound captured once into __stop_0, then C-style for.
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    total = 0\n"
                      + "    for i in range(n):\n        total = total + i\n    return total\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    int32_t total = 0;\n"
            "    int32_t __stop_0 = n;\n"
            "    for (int32_t i = 0; i < __stop_0; ++i) {\n"
            "        total = (::tpy::add_check<int32_t>(total, i));\n"
            "    }\n"
            "    return total;\n"
        )

    def test_emit_range_literal_inlined(self):
        thir = _lower(_PRELUDE
                      + "def f() -> Int32:\n    total = 0\n"
                      + "    for i in range(10):\n        total = total + i\n    return total\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    int32_t total = 0;\n"
            "    for (int32_t i = 0; i < 10; ++i) {\n"
            "        total = (::tpy::add_check<int32_t>(total, i));\n"
            "    }\n"
            "    return total;\n"
        )

    def test_emit_comparison_as_value(self):
        thir = _lower(_PRELUDE + "def f(x: Int32, y: Int32) -> bool:\n"
                      "    r = x < y\n    return r\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    bool r = (x < y);\n"
            "    return r;\n"
        )

    def test_emit_comparison_in_return(self):
        thir = _lower(_PRELUDE + "def f(x: Int32, y: Int32) -> bool:\n    return x < y\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == "    return (x < y);\n"

    def test_emit_derived_comparison(self):
        # `!=` has no resolved_binop -> the bare C++ operator branch of _emit_binop.
        thir = _lower(_PRELUDE + "def f(x: Int32, y: Int32) -> bool:\n    return x != y\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == "    return (x != y);\n"

    def test_emit_bare_bool_condition(self):
        thir = _lower(_PRELUDE + "def f(x: Int32, done: bool) -> Int32:\n    r = x\n"
                      "    if done:\n        r = 0\n    return r\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    int32_t r = x;\n"
            "    if (done) {\n"
            "        r = 0;\n"
            "    }\n"
            "    return r;\n"
        )

    def test_emit_bool_literal(self):
        thir = _lower("def f() -> bool:\n    ok = True\n    return ok\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    bool ok = true;\n"
            "    return ok;\n"
        )

    def test_emit_bare_bool_while(self):
        thir = _lower(_PRELUDE + "def f(go: bool) -> Int32:\n    r = 0\n"
                      "    while go:\n        r = r + 1\n    return r\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    int32_t r = 0;\n"
            "    while (go) {\n"
            "        r = (::tpy::add_check<int32_t>(r, 1));\n"
            "    }\n"
            "    return r;\n"
        )

    def test_emit_float_mul_literal(self):
        # Float arithmetic flows through the same templated binop path as int;
        # the float literal renders as repr(value) in a double slot.
        thir = _lower("def f(a: float) -> float:\n    return a * 2.0\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == "    return ((a) * (2.0));\n"

    def test_emit_float_floordiv(self):
        thir = _lower("def f(a: float, b: float) -> float:\n    return a // b\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == "    return (::tpy::floordiv(a, b));\n"

    def test_emit_range_literal_start_name_stop(self):
        # range(literal, name): literal start inlined (no __start_N), name stop
        # hoisted to __stop_0 -- the start_is_literal=True / stop_is_literal=False path.
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(2, n):\n        acc = acc + i\n    return acc\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    int32_t acc = 0;\n"
            "    int32_t __stop_0 = n;\n"
            "    for (int32_t i = 2; i < __stop_0; ++i) {\n"
            "        acc = (::tpy::add_check<int32_t>(acc, i));\n"
            "    }\n"
            "    return acc;\n"
        )

    def test_emit_nested_range_counter_parity(self):
        # The hidden-temp index must reproduce ctx.iter_counter: the outer loop
        # takes index 0 (__start_0/__stop_0) BEFORE its body, so the nested loop
        # takes index 1 (__stop_1) -- pre-order, matching _gen_range_counter_loop.
        thir = _lower(_PRELUDE
                      + "def f(a: Int32, b: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(a, b):\n        for j in range(b):\n"
                      + "            acc = acc + j\n    return acc\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    int32_t acc = 0;\n"
            "    int32_t __start_0 = a;\n"
            "    int32_t __stop_0 = b;\n"
            "    for (int32_t i = __start_0; i < __stop_0; ++i) {\n"
            "        int32_t __stop_1 = b;\n"
            "        for (int32_t j = 0; j < __stop_1; ++j) {\n"
            "            acc = (::tpy::add_check<int32_t>(acc, j));\n"
            "        }\n"
            "    }\n"
            "    return acc;\n"
        )

    def test_emit_if_else(self):
        thir = _lower(_PRELUDE
                      + "def f(a: Int32, b: Int32) -> Int32:\n"
                      + "    r = a\n"
                      + "    if a < b:\n        r = b\n    else:\n        r = a\n"
                      + "    return r\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    int32_t r = a;\n"
            "    if ((a < b)) {\n"
            "        r = b;\n"
            "    } else {\n"
            "        r = a;\n"
            "    }\n"
            "    return r;\n"
        )



class TestByteIdentical:
    """The load-bearing contract: THIR codegen == AST codegen for the slice,
    over a module that mixes routed (eligible) and AST-path (ineligible) funcs."""

    SRC = (
        _PRELUDE
        + "def passthru(a: Int32) -> Int32:\n    b = a\n    return b\n"
        + "def widen(p: UInt8) -> UInt8:\n    q = p\n    return q\n"
        + "def calc(a: Int32, b: Int32) -> Int32:\n    c = a * b + 1\n    return c // 2\n"
        + "def combo(a: Int32) -> Int32:\n    return calc(a, passthru(a)) + passthru(a + 1)\n"
        + "def clamp(x: Int32, lo: Int32, hi: Int32) -> Int32:\n"
        + "    r = x\n    if x < lo:\n        r = lo\n    elif x > hi:\n        r = hi\n    return r\n"
        + "def sum_to(n: Int32) -> Int32:\n    total = 0\n    i = 0\n"
        + "    while i < n:\n        total = total + i\n        i = i + 1\n    return total\n"
        + "def triangle(n: Int32) -> Int32:\n    total = 0\n"
        + "    for i in range(n):\n        total = total + i\n    return total\n"
        + "def matrix_sum(r: Int32, c: Int32) -> Int32:\n    acc = 0\n"
        + "    for i in range(r):\n        for j in range(0, c):\n            acc = acc + i\n    return acc\n"
        + "def scale(x: float, k: float) -> float:\n    r = x * k - 1.5\n    return r\n"
        + "def is_lt(a: Int32, b: Int32) -> bool:\n    return a < b\n"
        + "def check(a: Int32, b: Int32, flag: bool) -> bool:\n    ok = a < b\n"
        + "    if flag:\n        ok = a == b\n    return ok\n"
        + "def main():\n    print(clamp(sum_to(combo(7)), 0, 50) + triangle(5) + matrix_sum(3, 4))\n"
        + "    print(scale(2.0, 3.0))\n    print(is_lt(1, 2))\n    print(check(1, 2, True))\n\nmain()\n"
    )

    def test_entry_cpp_byte_identical(self):
        compiler, modules = _compile(self.SRC)
        entry = _entry(modules)
        ast = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=True))
        thir = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=True, thir_codegen=True))
        assert thir == ast


class TestBranchBlockLocals:
    """Slice 1: a VALUE local first-declared inside an if/elif/else/for/while
    body (the construct hoisting nothing) lowers in place, byte-identically to
    the AST path. Hoisted and non-value block-locals stay AST."""

    def _identical(self, src: str, ctx: bool = False):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        opts_ast = CodeGenOptions(emit_source_comments=True)
        opts_thir = CodeGenOptions(emit_source_comments=True, thir_codegen=True)
        if ctx:
            with activate_compiler(compiler):
                ast = compiler.generate_code_to_strings(entry, options=opts_ast)
                thir = compiler.generate_code_to_strings(entry, options=opts_thir)
        else:
            ast = compiler.generate_code_to_strings(entry, options=opts_ast)
            thir = compiler.generate_code_to_strings(entry, options=opts_thir)
        return ast, thir

    def _routes(self, src: str, name: str = "f", ctx: bool = False) -> bool:
        if ctx:
            compiler, modules = _compile(src)
            entry = _entry(modules)
            with activate_compiler(compiler):
                thir = lower_module(entry.ast, entry.analyzer)
            return any(fn.name == name for fn in thir.functions)
        return _fn(_lower(src), name) is not None

    def test_if_scalar_block_local_routes_identical(self):
        src = (_PRELUDE
               + "def f(c: bool) -> None:\n"
               + "    if c:\n        x = 5\n        print(x)\n    print(1)\n"
               + "f(True)\n")
        assert self._routes(src)
        ast, thir = self._identical(src)
        assert thir == ast

    def test_if_char_block_local_routes_identical(self):
        src = ("from tpy import Char\n"
               + "def f(c: bool) -> None:\n"
               + "    if c:\n        ch: Char = 'x'\n        print(ch)\n"
               + "f(True)\n")
        assert self._routes(src)
        ast, thir = self._identical(src)
        assert thir == ast

    def test_if_owned_str_block_local_routes_identical(self):
        src = ("def f(c: bool) -> None:\n"
               + "    if c:\n        name = 'hi'\n        print(name)\n"
               + "f(True)\n")
        assert self._routes(src)
        ast, thir = self._identical(src)
        assert thir == ast

    def test_if_enum_block_local_routes_identical(self):
        # Enums resolve through the active compiler -> ctx lowering.
        src = ("from enum import Enum\n"
               + "class Color(Enum):\n    RED = 0\n    GREEN = 1\n"
               + "def f(c: bool) -> Color:\n"
               + "    if c:\n        col = Color.RED\n        return col\n"
               + "    return Color.GREEN\n"
               + "print(int(f(True).value))\n")
        assert self._routes(src, ctx=True)
        ast, thir = self._identical(src, ctx=True)
        assert thir == ast

    def test_else_block_local_routes_identical(self):
        src = (_PRELUDE
               + "def f(c: bool) -> Int32:\n"
               + "    if c:\n        a = 1\n        return a\n"
               + "    else:\n        b = 2\n        return b\n"
               + "print(int(f(False)))\n")
        assert self._routes(src)
        ast, thir = self._identical(src)
        assert thir == ast

    def test_elif_block_local_routes_identical(self):
        src = (_PRELUDE
               + "def f(c: Int32) -> Int32:\n"
               + "    if c == 0:\n        a = 1\n        return a\n"
               + "    elif c == 1:\n        b = 2\n        return b\n"
               + "    else:\n        d = 3\n        return d\n"
               + "print(int(f(1)))\n")
        assert self._routes(src)
        ast, thir = self._identical(src)
        assert thir == ast

    def test_for_block_local_routes_identical(self):
        src = (_PRELUDE
               + "def f(n: Int32) -> Int32:\n    total = 0\n"
               + "    for i in range(n):\n        y = i * 2\n        total = total + y\n"
               + "    return total\n"
               + "print(int(f(5)))\n")
        assert self._routes(src)
        ast, thir = self._identical(src)
        assert thir == ast

    def test_while_block_local_routes_identical(self):
        src = (_PRELUDE
               + "def f(n: Int32) -> Int32:\n    i = 0\n"
               + "    while i < n:\n        z = i + 1\n        i = z\n"
               + "    return i\n"
               + "print(int(f(5)))\n")
        assert self._routes(src)
        ast, thir = self._identical(src)
        assert thir == ast

    def test_narrowing_if_block_local_routes_identical(self):
        # A block-local inside an isinstance-narrowed branch routes for free.
        src = ("from tpy import Int32\n"
               + "class A:\n    x: Int32\n    def __init__(self, x: Int32):\n        self.x = x\n"
               + "class B:\n    y: Int32\n    def __init__(self, y: Int32):\n        self.y = y\n"
               + "def f(v: A | B) -> Int32:\n"
               + "    if isinstance(v, A):\n        t = v.x + 1\n        return t\n"
               + "    return 0\n"
               + "print(int(f(A(3))))\n")
        assert self._routes(src, ctx=True)
        ast, thir = self._identical(src, ctx=True)
        assert thir == ast

    def test_nested_if_in_for_block_local_routes(self):
        src = (_PRELUDE
               + "def f(n: Int32) -> Int32:\n    t = 0\n"
               + "    for i in range(n):\n        if i > 2:\n            q = i * 3\n            t = t + q\n"
               + "    return t\n")
        assert self._routes(src)

    def test_nonhoisted_try_body_block_local_stays_ast(self):
        # A try-body first-decl used ONLY inside the try (handler falls through,
        # no else/finally) is NOT hoisted and declares inside the C++ try scope
        # -- deferred (try bodies never enable value block-locals).
        src = (_PRELUDE
               + "def f(n: Int32) -> None:\n"
               + "    try:\n        w = n + 1\n        print(w)\n"
               + "    except Exception:\n        print(0)\n")
        assert not self._routes(src)


class TestDelStmt:
    def test_del_scalar_local_routes_as_noop(self):
        # Trivially-destructible target: the AST emits no code, THIR a NoOp.
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    x = n + 1\n"
                      + "    y = x * 2\n    del x\n    return y\n")
        assert _fn(thir, "f") is not None

    def test_del_multi_scalar_routes(self):
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    a = n + 1\n    b = n + 2\n"
                      + "    del a, b\n    return n\n")
        assert _fn(thir, "f") is not None

    def test_del_owned_str_is_ineligible(self):
        # An owned-str local takes the move-sink face
        # (`{ auto __del_sink = std::move(t); }`) -- gated out.
        thir = _lower(_PRELUDE
                      + "def f(s: str) -> Int32:\n    t = s + \"x\"\n"
                      + "    del t\n    return 1\n")
        assert _fn(thir, "f") is None

    def test_del_item_routes(self):
        thir = _lower(_PRELUDE
                      + "def f(d: dict[Int32, Int32], k: Int32) -> Int32:\n"
                      + "    del d[k]\n    return len(d)\n"
                      + "def g(xs: list[Int32]) -> Int32:\n"
                      + "    del xs[0]\n    return len(xs)\n")
        assert _fn(thir, "f") is not None
        assert _fn(thir, "g") is not None

    def test_del_item_multi_target_is_ineligible(self):
        # Multi-target del shares one source comment across N emitted lines --
        # a shape one THIR statement cannot carry.
        thir = _lower(_PRELUDE
                      + "def f(d: dict[Int32, Int32]) -> Int32:\n"
                      + "    del d[1], d[2]\n    return len(d)\n")
        assert _fn(thir, "f") is None

    SRC = (
        _PRELUDE
        + "def drop(d: dict[str, Int32], k: str) -> Int32:\n"
        + "    del d[k]\n    del d[\"gone\"]\n    return len(d)\n"
        + "def scalars(n: Int32) -> Int32:\n    x = n + 1\n    del x\n    return n\n"
        + "def main():\n    d = {\"a\": 1, \"b\": 2, \"gone\": 3}\n"
        + "    print(drop(d, \"a\"))\n    print(scalars(5))\nmain()\n"
    )

    def test_byte_identical_with_comments(self):
        # The del source comment must land exactly where the AST puts it --
        # before the __delitem__ line, and alone for the no-code del-var.
        compiler, modules = _compile(self.SRC)
        entry = _entry(modules)
        ast = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=True))
        thir = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=True, thir_codegen=True))
        assert thir == ast

    def test_routes_and_emits_delitem(self):
        compiler, modules = _compile(self.SRC)
        entry = _entry(modules)
        assert _fn(lower_module(entry.ast, entry.analyzer), "drop") is not None
        assert _fn(lower_module(entry.ast, entry.analyzer), "scalars") is not None
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert "::tpy::__delitem__(d, k);" in cpp
        assert '::tpy::__delitem__(d, "gone");' in cpp


class TestForTails:
    SRC = (
        _PRELUDE
        + "def sum_values(d: dict[Int32, Int32]) -> Int32:\n    s = 0\n"
        + "    for v in d.values():\n        s = s + v\n    return s\n"
        + "def sum_keys(d: dict[Int32, Int32]) -> Int32:\n    s = 0\n"
        + "    for k in d.keys():\n        s = s + k\n    return s\n"
        + "def sum_items(d: dict[Int32, Int32]) -> Int32:\n    s = 0\n"
        + "    for k, v in d.items():\n        s = s + k + v\n    return s\n"
        + "def sum_pairs(ps: list[tuple[Int32, Int32]]) -> Int32:\n    s = 0\n"
        + "    for a, b in ps:\n        s = s + a * b\n    return s\n"
        + "def discard_snd(ps: list[tuple[Int32, Int32]]) -> Int32:\n    s = 0\n"
        + "    for a, _ in ps:\n        s = s + a\n    return s\n"
        + "def reused_target(ps: list[tuple[Int32, Int32]]) -> Int32:\n"
        + "    a = 100\n    s = 0\n"
        + "    for a, b in ps:\n        s = s + a + b\n    return s + a\n"
        + "def main():\n    d = {1: 10, 2: 20}\n"
        + "    print(sum_values(d))\n    print(sum_keys(d))\n    print(sum_items(d))\n"
        + "    ps = [(1, 2), (3, 4)]\n"
        + "    print(sum_pairs(ps))\n    print(discard_snd(ps))\n"
        + "    print(reused_target(ps))\nmain()\n"
    )

    def test_dict_view_iteration_routes(self):
        thir = _lower(self.SRC)
        assert _fn(thir, "sum_values") is not None
        assert _fn(thir, "sum_keys") is not None

    def test_tuple_unpack_loops_route(self):
        thir = _lower(self.SRC)
        assert _fn(thir, "sum_items") is not None
        assert _fn(thir, "sum_pairs") is not None
        assert _fn(thir, "discard_snd") is not None

    def test_two_unpack_loops_share_the_per_function_counter(self):
        # Two unpack loops in one function: __tup_1 then __tup_2 (the counter
        # is per-function and continuous across statements, NOT per-loop).
        src = (
            _PRELUDE
            + "def two(ps: list[tuple[Int32, Int32]], qs: list[tuple[Int32, Int32]]) -> Int32:\n"
            + "    s = 0\n"
            + "    for a, b in ps:\n        s = s + a * b\n"
            + "    for c, d in qs:\n        s = s - c + d\n    return s\n"
            + "def main():\n    ps = [(1, 2)]\n    print(two(ps, ps))\nmain()\n")
        assert _fn(_lower(src), "two") is not None
        compiler, modules = _compile(src)
        entry = _entry(modules)
        ast = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=True))
        thir = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=True, thir_codegen=True))
        assert thir == ast
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert "__tup_1" in cpp and "__tup_2" in cpp

    def test_dict_view_on_optional_receiver_is_ineligible(self):
        # `d.values()` on an unproven Optional dict carries the runtime-check
        # marker (needs_optional_runtime_check) -- the bare view render would
        # skip the deref check, so the iterable gate must reject it.
        thir = _lower(
            _PRELUDE
            + "def f(d: dict[Int32, Int32] | None) -> Int32:\n    s = 0\n"
            + "    for v in d.values():\n        s = s + v\n    return s\n")
        assert _fn(thir, "f") is None

    def test_reused_unpack_target_is_ineligible(self):
        # A target shadowing an outer local takes the AST's was_declared
        # assign path -- out of the slice.
        thir = _lower(self.SRC)
        assert _fn(thir, "reused_target") is None

    def test_standalone_unpack_stmt_routes(self):
        # `a, b = t` outside a for-loop head reuses the same THIRTupleUnpack head
        # (const-ref bind + per-target scalar decls) -- the value-scalar-tuple
        # source arm. Full coverage lives in test_thir_tuples.py.
        thir = _lower(_PRELUDE
                      + "def f(t: tuple[Int32, Int32]) -> Int32:\n"
                      + "    a, b = t\n    return a + b\n")
        assert _fn(thir, "f") is not None

    def test_byte_identical_with_comments(self):
        compiler, modules = _compile(self.SRC)
        entry = _entry(modules)
        ast = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=True))
        thir = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=True, thir_codegen=True))
        assert thir == ast

    def test_emit_shapes(self):
        compiler, modules = _compile(self.SRC)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert "auto __obj_0 = ::tpy::dict_values(d);" in cpp
        assert "auto __obj_0 = ::tpy::dict_items(d);" in cpp
        assert "const auto& __tup_1 = __for_tup_0;" in cpp
        assert "int32_t k = std::get<0>(__tup_1);" in cpp

    def test_dump(self):
        thir = _lower(self.SRC)
        assert "a, b = %__for_tup_" in dump_thir(thir)
        assert "a, _ = %__for_tup_" in dump_thir(thir)


class TestGlobalStmt:
    SRC = (
        _PRELUDE
        + "counter: Int32 = 0\nflag: bool = False\nbig: int = 0\n"
        + "def bump() -> Int32:\n    global counter\n"
        + "    counter = counter + 1\n    return counter\n"
        + "def bump_aug() -> None:\n    global counter\n    counter += 2\n"
        + "def set_flag() -> None:\n    global flag\n    flag = True\n"
        + "def big_write() -> None:\n    global big\n    big = big + 1\n"
        + "def read_no_decl() -> Int32:\n    return counter + 1\n"
        + "def main():\n    print(bump())\n    bump_aug()\n    set_flag()\n"
        + "    big_write()\n    print(read_no_decl())\nmain()\n"
    )

    def test_scalar_global_writes_route(self):
        thir = _lower(self.SRC)
        assert _fn(thir, "bump") is not None
        assert _fn(thir, "bump_aug") is not None
        assert _fn(thir, "set_flag") is not None

    def test_bigint_global_routes(self):
        # `int` globals are BigInt -- an eligible scalar since the BigInt
        # value-binding cell, so the `global` declaration seeds and the write
        # renders the same bare `big = ...;` (the class byte-identical test
        # covers the render).
        thir = _lower(self.SRC)
        assert _fn(thir, "big_write") is not None

    def test_bare_global_read_without_decl_routes(self):
        # Read-only module-global access (no `global` statement): value-family
        # globals a body never assigns seed read-only, so the bare read
        # renders like a local of the same resolved type.
        thir = _lower(self.SRC)
        assert _fn(thir, "read_no_decl") is not None

    def test_byte_identical_with_comments(self):
        compiler, modules = _compile(self.SRC)
        entry = _entry(modules)
        ast = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=True))
        thir = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=True, thir_codegen=True))
        assert thir == ast

    def test_write_emits_bare_assign(self):
        compiler, modules = _compile(self.SRC)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert "flag = true;" in cpp
        assert "counter = (::tpy::add_check<int32_t>(counter, 1));" in cpp


class TestBreakContinueEmit:
    SRC = (
        _PRELUDE
        + "def first_gt(xs: list[Int32], lim: Int32) -> Int32:\n    r = -1\n"
        + "    for x in xs:\n        if x > lim:\n            r = x\n            break\n"
        + "    return r\n"
        + "def sum_odd(n: Int32) -> Int32:\n    s = 0\n    i = 0\n"
        + "    while i < n:\n        i = i + 1\n        if i % 2 == 0:\n            continue\n"
        + "        s = s + i\n    return s\n"
        + "def main():\n    print(first_gt([1, 5, 9], 4))\n    print(sum_odd(7))\nmain()\n"
    )

    def test_routes(self):
        thir = _lower(self.SRC)
        assert _fn(thir, "first_gt") is not None
        assert _fn(thir, "sum_odd") is not None

    def test_byte_identical_with_comments(self):
        # Comments on: the `// break` / `// continue` source lines ride the
        # generic loc mechanism and must match the AST's placement.
        compiler, modules = _compile(self.SRC)
        entry = _entry(modules)
        ast = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=True))
        thir = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=True, thir_codegen=True))
        assert thir == ast

    def test_emits_bare_forms(self):
        compiler, modules = _compile(self.SRC)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert "break;" in cpp and "continue;" in cpp

    def test_dump(self):
        thir = _lower(self.SRC)
        d = dump_thir(thir)
        assert "break" in d and "continue" in d


class TestTriviaBodies:
    """Docstring / `pass` trivia in function bodies (the M3c-trivia arm made
    body-wide): pass-only and docstring-only bodies route; `pass` keeps its
    source comment, a docstring emits neither comment nor code."""

    def _cpp(self, src: str, thir: bool, comments: bool = False):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=comments,
                                          thir_codegen=thir))
        return cpp

    SRC = (
        _PRELUDE
        + 'def noop() -> None:\n    pass\n'
        + 'def documented() -> None:\n    """doc"""\n'
        + 'def mid(n: Int32) -> Int32:\n    """doc"""\n    return n\n'
        + 'def branch_pass(n: Int32) -> Int32:\n'
        + '    if n > 0:\n        pass\n    return n\n'
        + 'def main():\n    noop()\n    documented()\n    print(mid(1))\n'
        + '    print(branch_pass(2))\nmain()\n'
    )

    def test_trivia_bodies_route(self):
        thir = _lower(self.SRC)
        names = {f.name for f in thir.functions}
        assert {"noop", "documented", "mid", "branch_pass"} <= names

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_byte_identical_with_comments(self):
        # `pass` keeps its `// pass` source line; a docstring suppresses both
        # comment and code -- parity must hold with comments on.
        assert (self._cpp(self.SRC, thir=True, comments=True)
                == self._cpp(self.SRC, thir=False, comments=True))

    def test_trivia_face_witnessed(self):
        _, witnessed = _lower_ctx_witnessed(self.SRC)
        assert witnessed.get("stmt.trivia", 0) >= 4


class TestBoolFieldCondition:
    """Bool-field truthiness conditions (`if self.open:` / `while g.open:`):
    a bool value's truthiness render is its value render, so the admitted
    field-read emit carries the condition unchanged. Mirrors the name arm's
    bool-only scope pin (int-truthiness fields stay AST)."""

    SRC = (
        "from tpy import Int32\n"
        "class Gate:\n"
        "    open: bool\n"
        "    count: Int32\n"
        "    def __init__(self):\n"
        "        self.open = True\n"
        "        self.count = 3\n"
        "    def tick(self) -> Int32:\n"
        "        if self.open:\n"
        "            self.count += 1\n"
        "        return self.count\n"
        "    def drain(self) -> Int32:\n"
        "        total = 0\n"
        "        while self.open:\n"
        "            total += 1\n"
        "            self.open = False\n"
        "        return total\n"
        "def main():\n"
        "    g = Gate()\n"
        "    t = g.tick()\n"
        "    print(t)\n"
        "    print(g.drain())\n"
        "main()\n"
    )

    def test_routes_and_face_witnessed(self):
        thir, witnessed = _lower_ctx_witnessed(self.SRC)
        names = {f.name for f in thir.functions}
        assert {"tick", "drain"} <= names
        assert witnessed.get("cond.bool_field", 0) >= 2

    def test_byte_identical(self):
        compiler, modules = _compile(self.SRC)
        entry = _entry(modules)
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert thir_out == ast_out

    def test_nonbool_field_condition_stays_ast(self):
        src = (
            "from tpy import Int32\n"
            "class Tally:\n"
            "    n: Int32\n"
            "    def __init__(self):\n"
            "        self.n = 2\n"
            "    def spin(self) -> Int32:\n"
            "        if self.n:\n"
            "            return 1\n"
            "        return 0\n"
            "def main():\n"
            "    print(Tally().spin())\n"
            "main()\n"
        )
        thir, witnessed = _lower_ctx_witnessed(src)
        assert _fn(thir, "spin") is None
        assert witnessed.get("cond.bool_field", 0) == 0


class TestBoolMethodCondition:
    """Bool-method-call truthiness conditions (`if g.is_open():` /
    `while g.is_open():` / assert): a bool value's truthiness render is its
    value render, so the method call's value-position admission carries the
    condition unchanged. Bool only, mirroring the name/field arms' scope pin
    (int/str/Optional results stay AST)."""

    SRC = (
        "from tpy import Int32\n"
        "class Gate:\n"
        "    open: bool\n"
        "    count: Int32\n"
        "    def __init__(self):\n"
        "        self.open = True\n"
        "        self.count = 3\n"
        "    def is_open(self) -> bool:\n"
        "        return self.open\n"
        "    def shut(self):\n"
        "        self.open = False\n"
        "def tick(g: Gate, b: bool) -> Int32:\n"
        "    n = 0\n"
        "    if g.is_open():\n"
        "        n += 1\n"
        "    if not g.is_open():\n"
        "        n += 2\n"
        "    if g.is_open() and b:\n"
        "        n += 4\n"
        "    return n\n"
        "def drain(g: Gate) -> Int32:\n"
        "    total = 0\n"
        "    while g.is_open():\n"
        "        total += 1\n"
        "        g.shut()\n"
        "    return total\n"
        "def check(g: Gate):\n"
        "    assert g.is_open()\n"
        "def main():\n"
        "    g = Gate()\n"
        "    check(g)\n"
        "    print(tick(g, True))\n"
        "    print(drain(g))\n"
        "main()\n"
    )

    def test_routes_and_face_witnessed(self):
        thir, witnessed = _lower_ctx_witnessed(self.SRC)
        names = {f.name for f in thir.functions}
        assert {"tick", "drain", "check"} <= names
        # Direct if/while/assert conditions witness the face; the `not` /
        # `and` positions route through the pre-existing operand arms.
        assert witnessed.get("cond.bool_method", 0) >= 3

    def test_byte_identical(self):
        compiler, modules = _compile(self.SRC)
        entry = _entry(modules)
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert thir_out == ast_out

    def test_nonbool_method_condition_stays_ast(self):
        # An Int32 result also renders bare on the AST path, but the slice
        # pins bool like the name/field arms.
        src = (
            "from tpy import Int32\n"
            "class Tally:\n"
            "    n: Int32\n"
            "    def __init__(self):\n"
            "        self.n = 2\n"
            "    def size(self) -> Int32:\n"
            "        return self.n\n"
            "def spin(t: Tally) -> Int32:\n"
            "    if t.size():\n"
            "        return 1\n"
            "    return 0\n"
            "def main():\n"
            "    print(spin(Tally()))\n"
            "main()\n"
        )
        thir, witnessed = _lower_ctx_witnessed(src)
        assert _fn(thir, "spin") is None
        assert witnessed.get("cond.bool_method", 0) == 0

    def test_str_method_condition_stays_ast(self):
        # A str result takes the `.empty()` truthiness wrap on the AST path.
        src = (
            "class Box:\n"
            "    s: str\n"
            "    def __init__(self):\n"
            "        self.s = \"x\"\n"
            "    def name(self) -> str:\n"
            "        return self.s\n"
            "def probe(b: Box) -> bool:\n"
            "    if b.name():\n"
            "        return True\n"
            "    return False\n"
            "def main():\n"
            "    print(probe(Box()))\n"
            "main()\n"
        )
        thir, witnessed = _lower_ctx_witnessed(src)
        assert _fn(thir, "probe") is None
        assert witnessed.get("cond.bool_method", 0) == 0


class TestImportedCallee:
    """Cross-module free-function calls (`from helper import f; f(x)`):
    the gate admits attribute-table-imported plain callees and lowering
    stamps THIRCall.callee_cpp with the pre-rendered absolute spelling --
    imported_free_callee_cpp, the ONE qualification decision shared with
    the AST emit. Natives / extern-C / implicit builtins stay AST."""

    HELPER = (
        "from tpy import Int32\n"
        "def triple(n: Int32) -> Int32:\n"
        "    return n * 3\n"
        "def shout(n: Int32) -> None:\n"
        "    print(n)\n"
    )
    SRC = (
        "from tpy import Int32\n"
        "from helper import triple, shout\n"
        "def use(n: Int32) -> Int32:\n"
        "    m = triple(n)\n"
        "    shout(m)\n"
        "    return triple(m)\n"
        "def main():\n"
        "    print(use(2))\n"
        "main()\n"
    )

    def _compiled(self, tmp_path):
        (tmp_path / "helper.py").write_text(self.HELPER)
        return _compile(self.SRC, extra_lib_dirs=[tmp_path])

    def test_routes_with_prerendered_spelling(self, tmp_path):
        compiler, modules = self._compiled(tmp_path)
        entry = _entry(modules)
        with activate_compiler(compiler):
            thir = lower_module(entry.ast, entry.analyzer)
            witnessed = dict(compiler._thir_face_witnesses)
        fn = _fn(thir, "use")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl.init, THIRCall)
        assert decl.init.callee_cpp == "::tpyapp::helper::triple"
        assert witnessed.get("call.imported", 0) >= 3

    def test_byte_identical(self, tmp_path):
        compiler, modules = self._compiled(tmp_path)
        entry = _entry(modules)
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert thir_out == ast_out
        assert "::tpyapp::helper::triple(" in thir_out[1]

    def test_same_module_call_stays_bare(self):
        thir = _lower(_PRELUDE
                      + "def one() -> Int32:\n    return 1\n"
                      + "def two() -> Int32:\n    return one() + 1\n")
        fn = _fn(thir, "two")
        assert fn is not None
        call = fn.body[0].value.left
        assert isinstance(call, THIRCall)
        assert call.callee_cpp is None

    NATIVE_SRC = (
        "from tpy import Float64\n"
        "from math import sqrt\n"
        "def f(x: Float64) -> Float64:\n"
        "    return sqrt(x)\n"
        "def main():\n"
        "    print(f(4.0))\n"
        "main()\n"
    )

    def test_native_free_callee_routes(self):
        # A C++ @native stdlib free function (`math.sqrt`): the resolved
        # symbol rides THIRCall.native_name -- the gen_call_from_fi arm the
        # len hardcode uses. Exact-arm assertions: a classification flip to
        # template/imported must fail here, not slide through an OR.
        thir, witnessed = _lower_ctx_witnessed(self.NATIVE_SRC)
        fn = _fn(thir, "f")
        assert fn is not None
        call = fn.body[0].value
        assert isinstance(call, THIRCall)
        assert call.native_name == "std::sqrt"
        assert call.cpp_template is None and call.callee_cpp is None
        assert witnessed.get("call.native_free", 0) >= 1
        assert witnessed.get("call.template_free", 0) == 0

    def test_native_free_byte_identical(self):
        compiler, modules = _compile(self.NATIVE_SRC)
        entry = _entry(modules)
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert thir_out == ast_out
        assert "::std::sqrt(" in thir_out[1]

    TEMPLATE_SRC = (
        "from tpy import Char, Int32\n"
        "def f(c: Char) -> Int32:\n"
        "    return ord(c)\n"
        "def main():\n"
        "    print(f('A'))\n"
        "main()\n"
    )

    def test_template_free_callee_routes(self):
        # A positional-only @cpp_template free function (`ord(c)` on a
        # runtime Char -- the single-char-LITERAL constant fold is the
        # rejected shape): the substituted template rides
        # THIRCall.cpp_template, the scalar-ctor expansion arm.
        thir, witnessed = _lower_ctx_witnessed(self.TEMPLATE_SRC)
        fn = _fn(thir, "f")
        assert fn is not None
        call = fn.body[0].value
        assert isinstance(call, THIRCall)
        assert call.cpp_template is not None and "{0}" in call.cpp_template
        assert call.native_name is None and call.callee_cpp is None
        assert witnessed.get("call.template_free", 0) >= 1

    def test_template_free_byte_identical(self):
        compiler, modules = _compile(self.TEMPLATE_SRC)
        entry = _entry(modules)
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert thir_out == ast_out

    def test_imported_literal_overload_stays_ast(self, tmp_path):
        # A cross-module call resolving to a literal-specialized overload:
        # the mangled-name reject (`call.literal_overload`) must fire BEFORE
        # the imported arm -- routing it through the plain qualified
        # spelling would drop the `__lit_N` mangling. AST keeps the shape;
        # both paths stay byte-identical.
        (tmp_path / "helper.py").write_text(
            "from typing import Literal, overload\n"
            "from tpy import Int32\n"
            "@overload\n"
            "def get_field(name: Literal[\"age\"]) -> Int32: ...\n"
            "@overload\n"
            "def get_field(name: Literal[\"name\"]) -> str: ...\n"
            "@overload\n"
            "def get_field(name: str) -> Int32 | str: ...\n"
            "def get_field(name: str) -> Int32 | str:\n"
            "    if name == \"age\":\n"
            "        return 42\n"
            "    return \"hello\"\n"
        )
        src = (
            "from tpy import Int32\n"
            "from helper import get_field\n"
            "def use() -> Int32:\n"
            "    return get_field(\"age\")\n"
            "def main():\n"
            "    print(use())\n"
            "main()\n"
        )
        compiler, modules = _compile(src, extra_lib_dirs=[tmp_path])
        entry = _entry(modules)
        with activate_compiler(compiler):
            thir = lower_module(entry.ast, entry.analyzer)
        assert _fn(thir, "use") is None
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert thir_out == ast_out


class TestGlobalReadonlySeed:
    """Read-only same-module value-global seeding: scalar / str / StrView /
    Final / Ptr[T] globals a body never assigns read bare, exactly like a
    local of the same resolved type. Names the body assigns or shadow-binds
    are excluded (a seeded one would misroute its first local decl as a bare
    global reassign); non-value / Optional / imported globals stay AST."""

    SRC = (
        "from tpy import Int32, StrView\n"
        "from typing import Final\n"
        "GI: Int32 = 10\n"
        "GB = True\n"
        "GS = \"own\"\n"
        "GV: StrView = \"view\"\n"
        "GBIG = 7\n"
        "GC: Final[Int32] = 99\n"
        "GL = [1, 2]\n"
        "GO: Int32 | None = 5\n"
        "def read_scalar() -> Int32:\n"
        "    if GI > 5:\n"
        "        return GI\n"
        "    return 0\n"
        "def read_bool() -> bool:\n"
        "    return GB\n"
        "def read_str() -> str:\n"
        "    return GS\n"
        "def read_view_owned_sink() -> str:\n"
        "    return GV\n"
        "def read_final() -> Int32:\n"
        "    return GC\n"
        "def shadow_only() -> Int32:\n"
        "    GI = 1\n"
        "    return GI\n"
        "def read_then_shadow() -> Int32:\n"
        "    y = GI\n"
        "    GI = 5\n"
        "    return y + GI\n"
        "def aug_without_global() -> Int32:\n"
        "    GI += 1\n"
        "    return GI\n"
        "def read_container() -> Int32:\n"
        "    return GL[0]\n"
        "def read_optional() -> Int32:\n"
        "    if GO is not None:\n"
        "        return GO\n"
        "    return 0\n"
        "def main():\n"
        "    print(read_scalar(), read_bool(), read_str(), read_final())\n"
        "    print(read_view_owned_sink(), shadow_only(), read_then_shadow())\n"
        "    print(aug_without_global(), read_container(), read_optional())\n"
        "main()\n"
    )

    def test_value_family_reads_route(self):
        thir, witnessed = _lower_ctx_witnessed(self.SRC)
        for name in ("read_scalar", "read_bool", "read_str",
                     "read_view_owned_sink", "read_final"):
            assert _fn(thir, name) is not None, name
        assert witnessed.get("name.global_seeded", 0) >= 5

    def test_reads_render_bare(self):
        fn = _fn(_lower_ctx(self.SRC), "read_scalar")
        ret = fn.body[0].then_body[0]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRName) and ret.value.name == "GI"

    def test_view_global_is_borrow_form(self):
        # A StrView global at an owned-str return takes the view->owned copy
        # (`std::string(GV)`), driven by the BORROW form tag -- the byte-
        # identical test pins the render; this pins the tag.
        fn = _fn(_lower_ctx(self.SRC), "read_view_owned_sink")
        assert fn is not None

    def test_assigning_bodies_do_not_seed(self):
        thir = _lower_ctx(self.SRC)
        # A pure local shadow routes as a FRESH decl (not a bare global
        # reassign) -- the name was excluded from seeding.
        fn = _fn(thir, "shadow_only")
        assert fn is not None
        assert isinstance(fn.body[0], THIRVarDecl)
        # A read BEFORE the shadowing assignment resolves to the global (a
        # sema-accepted CPython-parity gap); the excluded name makes the read
        # reject -> whole body stays AST.
        assert _fn(thir, "read_then_shadow") is None
        # TPy accepts an aug-assign to a global without `global` (mutates the
        # global); the exclusion keeps the body on the AST path.
        assert _fn(thir, "aug_without_global") is None

    def test_nonvalue_and_optional_globals_reject(self):
        thir = _lower_ctx(self.SRC)
        # Containers are pointer slots ((*GL) reads) -- not seeded.
        assert _fn(thir, "read_container") is None
        # Optional-value globals must NOT seed: sema narrows the read, and the
        # AST renders the narrowed global bare (a known AST miscompile) while
        # THIR's local-style narrowing would extract -- a divergence.
        assert _fn(thir, "read_optional") is None

    def test_byte_identical(self):
        compiler, modules = _compile(self.SRC)
        entry = _entry(modules)
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert thir_out == ast_out

    def test_except_as_shadow_excluded(self):
        # `_shadow_bound_names` excludes an except-`as` binder name from
        # seeding: reads of the like-named global in that body stay AST.
        src = (
            "from tpy import Int32\n"
            "GE: Int32 = 3\n"
            "def f() -> Int32:\n"
            "    x = GE\n"
            "    return x\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        t = TpyTry(try_body=[TpyPassStmt()],
                   handlers=[TpyExceptHandler(exception_type="ValueError",
                                              binding="GE", body=[TpyPassStmt()])],
                   else_body=[], finally_body=[])
        assert _shadow_bound_names([t]) == {"GE"}


class TestGlobalPtrSeed:
    """Ptr[T] globals: `global`-declared writes (param / None sources) and
    read-only returns route; `x = None` at a Ptr binding renders `nullptr`."""

    SRC = (
        "from tpy import Int32, Ptr, take_ptr\n"
        "class Node:\n"
        "    n: Int32\n"
        "    def __init__(self) -> None:\n"
        "        self.n = 5\n"
        "head: Ptr[Node] = None\n"
        "def set_head(p: Ptr[Node]) -> None:\n"
        "    global head\n"
        "    head = p\n"
        "def clear_head() -> None:\n"
        "    global head\n"
        "    head = None\n"
        "def get_head() -> Ptr[Node]:\n"
        "    return head\n"
        "def main() -> None:\n"
        "    node = Node()\n"
        "    set_head(take_ptr(node))\n"
        "    print(get_head() is not None)\n"
        "    clear_head()\n"
        "main()\n"
    )

    def test_ptr_global_bodies_route(self):
        thir, witnessed = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "set_head") is not None
        assert _fn(thir, "clear_head") is not None
        assert _fn(thir, "get_head") is not None
        assert witnessed.get("decl.ptr_none", 0) >= 1

    def test_none_write_lowers_to_nullptr_literal(self):
        fn = _fn(_lower_ctx(self.SRC), "clear_head")
        assign = fn.body[1]  # body[0] is the `global head` no-op
        assert isinstance(assign, THIRAssign)
        assert isinstance(assign.value, THIRLiteral)
        assert assign.value.value is None
        assert assign.value.form is not Form.STORAGE  # emits nullptr

    def test_byte_identical(self):
        compiler, modules = _compile(self.SRC)
        entry = _entry(modules)
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert thir_out == ast_out
        assert "head = nullptr;" in thir_out[1]


class TestGlobalCtorAndImports:
    def test_ctor_body_global_read_demotes_to_body(self):
        # A leading `self.f = GLOBAL` is a bare-name RHS the AST DEMOTES to
        # the body (`blocked_by_bare_name`); THIR mirrors the demote
        # (`_ast_demotes_init`), so the ctor routes with an empty MIL and the
        # body assign `this->n = LIMIT;` -- a hoist would emit `: n(LIMIT)`.
        src = (
            "from tpy import Int32\n"
            "LIMIT: Int32 = 9\n"
            "class Box2:\n"
            "    n: Int32\n"
            "    def __init__(self) -> None:\n"
            "        self.n = LIMIT\n"
            "b = Box2()\n"
            "print(b.n)\n"
        )
        ctor = _lower_ctor(src, "Box2")
        assert ctor is not None
        assert ctor.mil_inits == ()
        assert len(ctor.body) == 1
        compiler, modules = _compile(src)
        entry = _entry(modules)
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert thir_out == ast_out
        assert "this->n = LIMIT;" in thir_out[0]

    def test_ctor_nonleading_global_read_routes(self):
        # After the leading-MIL chain, a body statement reading a seeded
        # global routes through the shared statement machinery.
        src = (
            "from tpy import Int32\n"
            "SCALE: Int32 = 4\n"
            "class Acc:\n"
            "    total: Int32\n"
            "    def __init__(self, base: Int32) -> None:\n"
            "        self.total = base\n"
            "        self.total = self.total * SCALE\n"
            "a = Acc(3)\n"
            "print(a.total)\n"
        )
        ctor = _lower_ctor(src, "Acc")
        assert ctor is not None

    def test_imported_global_read_routes(self, tmp_path):
        # Cross-module imported-variable reads qualify (`::tpyapp::cfg::width`):
        # the read seeds like a same-module value global, the pre-rendered
        # spelling riding THIRName.cpp.
        (tmp_path / "cfg.py").write_text(
            "from tpy import Int32\nwidth: Int32 = 100\n")
        src = (
            "from tpy import Int32\n"
            "from cfg import width\n"
            "def read_w() -> Int32:\n"
            "    return width\n"
            "def main():\n"
            "    print(read_w())\n"
            "main()\n"
        )
        compiler, modules = _compile(src, extra_lib_dirs=[tmp_path])
        entry = _entry(modules)
        with activate_compiler(compiler):
            thir = lower_module(entry.ast, entry.analyzer)
        fn = _fn(thir, "read_w")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret.value, THIRName)
        assert ret.value.cpp == "::tpyapp::cfg::width"
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert thir_out == ast_out
        assert "::tpyapp::cfg::width" in thir_out[1]


class TestGlobalSpelledSeed:
    """Native-linkage and imported value globals seed READ-ONLY like the
    same-module ones, the fixed spelling riding THIRName.cpp (rendered
    verbatim by emit): `::symbol` via qualify_native_name for a module's own
    @native globals, `::tpyapp::mod::g` / the registered native_cpp_name via
    imported_variable_cpp -- the ONE decision shared with the AST render --
    for imports (re-exports resolve to the defining module). The same-module
    seeding exclusions apply identically (a shadowing assignment keeps the
    name a plain local); non-value imported globals stay pointer-slot AST, and a
    leading `self.f = IMPORTED` keeps the ctor on the AST path (the
    bare-name MIL demote)."""

    NATIVE_SRC = (
        "from typing import Final\n"
        "from tpy import Int32\n"
        "from tpy.extern import native_global\n"
        "COUNT: Int32 = native_global(\"g_count\")\n"
        "LIMIT: Final[Int32] = native_global(\"tpy_limit\", binding=\"C\")\n"
        "def read_count() -> Int32:\n"
        "    return COUNT + 1\n"
        "def read_limit() -> Int32:\n"
        "    return LIMIT\n"
        "def shadow() -> Int32:\n"
        "    COUNT = 3\n"
        "    return COUNT\n"
        "def main() -> None:\n"
        "    print(read_count(), read_limit(), shadow())\n"
        "main()\n"
    )

    def test_native_reads_route_with_spelling(self):
        thir, witnessed = _lower_ctx_witnessed(self.NATIVE_SRC)
        fn = _fn(thir, "read_count")
        assert fn is not None
        read = fn.body[0].value.left
        assert isinstance(read, THIRName) and read.cpp == "::g_count"
        lim = _fn(thir, "read_limit").body[0].value
        assert isinstance(lim, THIRName) and lim.cpp == "::tpy_limit"
        assert witnessed.get("name.global_native", 0) >= 2

    def test_native_shadow_excluded(self):
        # An assigned name is a plain local for the whole body (Python
        # scoping): fresh decl, bare read, no native spelling.
        fn = _fn(_lower_ctx(self.NATIVE_SRC), "shadow")
        assert fn is not None
        assert isinstance(fn.body[0], THIRVarDecl)
        ret = fn.body[1]
        assert isinstance(ret.value, THIRName) and ret.value.cpp is None

    def test_native_byte_identical(self):
        compiler, modules = _compile(self.NATIVE_SRC)
        entry = _entry(modules)
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert thir_out == ast_out
        assert "::g_count" in thir_out[1] and "::tpy_limit" in thir_out[1]

    HELPER = (
        "from typing import Final\n"
        "from tpy import Int32, StrView\n"
        "G: Int32 = 5\n"
        "NAME: StrView = \"hello\"\n"
        "BIG: Final[Int32] = 99\n"
        "items: list[Int32] = [1, 2]\n"
    )
    SRC = (
        "from tpy import Int32\n"
        "from helper import G, NAME, BIG, items\n"
        "def read_g() -> Int32:\n"
        "    return G + 1\n"
        "def read_final() -> Int32:\n"
        "    return BIG\n"
        "def name_owned() -> str:\n"
        "    return NAME\n"
        "def read_items() -> Int32:\n"
        "    return items[0]\n"
        "def shadow() -> Int32:\n"
        "    G = 7\n"
        "    return G\n"
        "class C:\n"
        "    x: Int32\n"
        "    def __init__(self) -> None:\n"
        "        self.x = G\n"
        "    def m(self) -> Int32:\n"
        "        return G * 2\n"
        "def main() -> None:\n"
        "    c = C()\n"
        "    print(read_g(), read_final(), name_owned())\n"
        "    print(read_items(), shadow(), c.m())\n"
        "main()\n"
    )

    def _lowered(self, tmp_path):
        (tmp_path / "helper.py").write_text(self.HELPER)
        return _lower_ctx_witnessed(self.SRC, extra_lib_dirs=[tmp_path])

    def test_imported_reads_route_with_spelling(self, tmp_path):
        thir, witnessed = self._lowered(tmp_path)
        fn = _fn(thir, "read_g")
        assert fn is not None
        read = fn.body[0].value.left
        assert isinstance(read, THIRName)
        assert read.cpp == "::tpyapp::helper::G"
        fin = _fn(thir, "read_final").body[0].value
        assert fin.cpp == "::tpyapp::helper::BIG"
        # Methods seed the same way (ctor seeding exists too, but the MIL
        # demote keeps this fixture's ctor on the AST path -- below).
        assert _fn(thir, "m") is not None
        assert witnessed.get("name.global_imported", 0) >= 3

    def test_imported_view_global_owned_sink_copies(self, tmp_path):
        # A StrView imported global keeps the BORROW form tag, so the owned
        # -str return takes the view->owned copy AROUND the spelling
        # (`std::string(::tpyapp::helper::NAME)`).
        thir, _ = self._lowered(tmp_path)
        assert _fn(thir, "name_owned") is not None

    def test_nonvalue_and_shadowed_imported(self, tmp_path):
        thir, _ = self._lowered(tmp_path)
        # A list global is a pointer slot ((*::tpyapp::helper::items) reads)
        # -- not in the value family, never seeded.
        assert _fn(thir, "read_items") is None
        # The shadowing assignment excludes the name: fresh local decl.
        fn = _fn(thir, "shadow")
        assert fn is not None
        assert isinstance(fn.body[0], THIRVarDecl)

    def test_imported_ctor_mil_demotes_to_body(self, tmp_path):
        # `self.x = G` leading a ctor: the AST demotes the bare-name RHS to a
        # body assign (`this->x = ::tpyapp::helper::G;`) -- THIR mirrors the
        # demote (`_ast_demotes_init`), so the ctor routes with an empty MIL
        # and the assign in the body.
        (tmp_path / "helper.py").write_text(self.HELPER)
        ctor = _lower_ctor(self.SRC, "C", extra_lib_dirs=[tmp_path])
        assert ctor is not None
        assert ctor.mil_inits == ()
        assert len(ctor.body) == 1

    def test_imported_byte_identical(self, tmp_path):
        (tmp_path / "helper.py").write_text(self.HELPER)
        compiler, modules = _compile(self.SRC, extra_lib_dirs=[tmp_path])
        entry = _entry(modules)
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert thir_out == ast_out
        assert "std::string(::tpyapp::helper::NAME)" in thir_out[1]
        assert "this->x = ::tpyapp::helper::G;" in thir_out[0]

    def test_reexported_global_spells_definer(self, tmp_path):
        # A re-exported global resolves through the chain to the module that
        # actually emits the symbol (resolve_definer inside the shared
        # helper): `from reexp import G` spells `::tpyapp::helper::G`.
        (tmp_path / "helper.py").write_text(
            "from tpy import Int32\nG: Int32 = 5\n")
        (tmp_path / "reexp.py").write_text("from helper import G\n")
        src = (
            "from tpy import Int32\n"
            "from reexp import G\n"
            "def read_g() -> Int32:\n"
            "    return G + 3\n"
            "def main() -> None:\n"
            "    print(read_g())\n"
            "main()\n"
        )
        thir, witnessed = _lower_ctx_witnessed(src, extra_lib_dirs=[tmp_path])
        fn = _fn(thir, "read_g")
        assert fn is not None
        read = fn.body[0].value.left
        assert read.cpp == "::tpyapp::helper::G"
        assert witnessed.get("name.global_imported", 0) >= 1

    def test_imported_native_global_spells_symbol(self, tmp_path):
        # Importing another module's @native global rides the SAME imported
        # arm; the spelling is the registered native_cpp_name (`::g_count`),
        # not the module-qualified qname.
        (tmp_path / "natmod.py").write_text(
            "from tpy import Int32\n"
            "from tpy.extern import native_global\n"
            "COUNT: Int32 = native_global(\"g_count\")\n")
        src = (
            "from tpy import Int32\n"
            "from natmod import COUNT\n"
            "def read_count() -> Int32:\n"
            "    return COUNT * 2\n"
            "def main() -> None:\n"
            "    print(read_count())\n"
            "main()\n"
        )
        thir, witnessed = _lower_ctx_witnessed(src, extra_lib_dirs=[tmp_path])
        fn = _fn(thir, "read_count")
        assert fn is not None
        read = fn.body[0].value.left
        assert read.cpp == "::g_count"
        assert witnessed.get("name.global_imported", 0) >= 1


class TestRecordFieldWrite:
    # `recv.field = <record rvalue>` off an F1-record method body: a ctor
    # (`Inner(n)`) or a by-value call (`mk(n)`) rvalue copies bare into the
    # field (the field_write.record_rvalue rung). A bare record NAME source
    # routes too since the ctor-body cell -- the bare copy / std::move rows
    # (field_write.record_name; routed units in test_thir_methods'
    # TestFieldWriteFamilies). (_lower_ctx: non-value records need the compiler.)
    SRC = (
        "from tpy import Int32, Own\n"
        "class Inner:\n"
        "    value: Int32\n"
        "    def __init__(self, value: Int32):\n        self.value = value\n"
        "def mk(n: Int32) -> Own[Inner]:\n    return Inner(n)\n"
        "class Box:\n"
        "    inner: Inner\n"
        "    def __init__(self, first: Own[Inner]):\n        self.inner = first\n"
        "    def set_ctor(self, n: Int32):\n        self.inner = Inner(n)\n"
        "    def set_call(self, n: Int32):\n        self.inner = mk(n)\n"
        "    def set_name(self, other: Inner):\n        self.inner = other\n"
    )

    def test_ctor_rvalue_field_write_routes(self):
        fn = _fn(_lower_ctx(self.SRC), "set_ctor")
        assert fn is not None
        assert isinstance(fn.body[0], THIRAssign)

    def test_call_rvalue_field_write_routes(self):
        fn = _fn(_lower_ctx(self.SRC), "set_call")
        assert fn is not None
        assert isinstance(fn.body[0], THIRAssign)

    def test_record_name_source_routes_bare(self):
        fn = _fn(_lower_ctx(self.SRC), "set_name")
        assert fn is not None
        st = fn.body[0]
        assert isinstance(st, THIRAssign)
        assert not isinstance(st.value, THIRFormConvert)  # bare copy, no move
