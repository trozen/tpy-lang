"""THIR value-scalar slice: lowering eligibility/shape, dump, and the
byte-identical emit contract (THIR codegen == AST codegen for the slice)."""

from __future__ import annotations

import io

from .. import get_lib_dir
from ..codegen_cpp.context import CodeGenOptions
from ..compiler import Compiler
from .dump import dump_thir
from .emit import emit_thir_body
from .lower import lower_module
from .nodes import (
    THIRAssign, THIRBinOp, THIRCall, THIRForRange, THIRIf, THIRLiteral, THIRName,
    THIRReturn, THIRVarDecl, THIRWhile,
)

_STDLIB_DIRS = [get_lib_dir() / "tpy"]


def _compile(source: str):
    compiler = Compiler.from_source(source, lib_dirs=_STDLIB_DIRS)
    return compiler, compiler.compile()


def _entry(modules):
    return [m for m in modules if m.is_entry_point][0]


def _lower(source: str):
    compiler, modules = _compile(source)
    entry = _entry(modules)
    return lower_module(entry.ast, entry.analyzer)


def _fn(thir, name):
    return next((f for f in thir.functions if f.name == name), None)


_PRELUDE = "from tpy import Int32, UInt8, UInt64\n"


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

    def test_global_reference_is_ineligible(self):
        # A name resolving to a module global needs a qualified C++ symbol the
        # slice does not yet materialize -> stays on the AST path.
        thir = _lower(_PRELUDE + "G: Int32 = 5\ndef f() -> Int32:\n    return G\n")
        assert _fn(thir, "f") is None

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

    def test_builtin_call_is_ineligible(self):
        # `abs` is an imported builtin -> qualified/special emit, not bare.
        thir = _lower(_PRELUDE + "def f(a: Int32) -> Int32:\n    return abs(a)\n")
        assert _fn(thir, "f") is None

    def test_aug_assign_is_ineligible(self):
        # `a += 1` (TpyAugAssign) is not in the supported statement set.
        thir = _lower(_PRELUDE + "def f(a: Int32) -> Int32:\n    a += 1\n    return a\n")
        assert _fn(thir, "f") is None

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

    def test_branch_local_first_decl_is_ineligible(self):
        # `t` is first-declared inside the branch -> needs scope machinery.
        thir = _lower(_PRELUDE
                      + "def f(a: Int32) -> Int32:\n    r = a\n"
                      + "    if a < 0:\n        t = 0 - a\n        r = t\n"
                      + "    return r\n")
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

    def test_while_with_break_is_ineligible(self):
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    i = 0\n"
                      + "    while i < n:\n        if i > 3:\n            break\n        i = i + 1\n"
                      + "    return i\n")
        assert _fn(thir, "f") is None

    def test_non_fixed_int_param_is_ineligible(self):
        # `int` is BigInt, not a fixed-width scalar -> outside the slice.
        thir = _lower("def f(a: int) -> int:\n    b = a\n    return b\n")
        assert _fn(thir, "f") is None

    def test_method_is_ineligible(self):
        thir = _lower(_PRELUDE
                      + "class C:\n    def m(self, a: Int32) -> Int32:\n        b = a\n        return b\n")
        assert _fn(thir, "m") is None

    def test_generic_is_ineligible(self):
        thir = _lower("def f[T](a: T) -> T:\n    b = a\n    return b\n")
        assert _fn(thir, "f") is None


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

    def test_stepped_range_is_ineligible(self):
        # 3-arg range -> step handling (overflow checks etc.) outside the slice.
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(0, n, 2):\n        acc = acc + i\n    return acc\n")
        assert _fn(thir, "f") is None

    def test_break_in_body_is_ineligible(self):
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(n):\n        if i > 3:\n            break\n        acc = acc + i\n"
                      + "    return acc\n")
        assert _fn(thir, "f") is None

    def test_continue_in_body_is_ineligible(self):
        # TpyContinue is not in the supported statement set (default-reject) --
        # an explicit guard so a future _stmt_eligible arm can't silently route it.
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(n):\n        if i > 3:\n            continue\n        acc = acc + i\n"
                      + "    return acc\n")
        assert _fn(thir, "f") is None

    def test_loop_var_shadowing_outer_is_ineligible(self):
        # A loop var name already bound in the outer scope hits the AST path's
        # was_declared handling (no fresh for-init decl), which the slice skips.
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n    i = 0\n"
                      + "    for i in range(n):\n        acc = acc + i\n    return acc\n")
        assert _fn(thir, "f") is None

    def test_binop_bound_is_ineligible(self):
        # A non-literal, non-name bound (binop) is deferred: gen_range_args'
        # _gen_expr_deref(arg, ptype) rendering is not yet net-confirmed vs _emit_expr.
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(n + 1):\n        acc = acc + i\n    return acc\n")
        assert _fn(thir, "f") is None


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

    def test_float32_is_ineligible(self):
        # Float32 literals need a `f` suffix the slice does not emit, so the
        # whole Float32 family stays on the AST path.
        thir = _lower("from tpy import Float32\n"
                      "def f(a: Float32) -> Float32:\n    b = a\n    return b\n")
        assert _fn(thir, "f") is None


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

    def test_logical_and_is_ineligible(self):
        # `and`/`or` (TpyBinOp &&/||) use a narrowing + short-circuit-slot emit
        # path (`_gen_logical_value`) the slice does not reproduce.
        thir = _lower("def f(a: bool, b: bool) -> bool:\n    return a and b\n")
        assert _fn(thir, "f") is None

    def test_logical_or_is_ineligible(self):
        # `or` (TpyBinOp ||) shares the `and` exclusion; a separate guard so a
        # future edit admitting one operator can't silently route the other.
        thir = _lower("def f(a: bool, b: bool) -> bool:\n    return a or b\n")
        assert _fn(thir, "f") is None

    def test_not_is_ineligible(self):
        # `not` (TpyUnaryOp) emits via the resolved_unaryop path the slice has no
        # node for.
        thir = _lower("def f(a: bool) -> bool:\n    return not a\n")
        assert _fn(thir, "f") is None

    def test_bool_literal_condition_is_ineligible(self):
        # `if True:` is excluded -- the AST path may dead-branch-eliminate a
        # bool-literal condition, which a bare `if (true)` would not reproduce.
        thir = _lower("def f(a: bool) -> bool:\n    r = a\n"
                      "    if True:\n        r = a\n    return r\n")
        assert _fn(thir, "f") is None


class TestDump:
    def test_dump_format(self):
        thir = _lower(_PRELUDE + "def f(a: Int32) -> Int32:\n    b = a\n    return b\n")
        assert dump_thir(thir) == (
            "fn f(a: Int32) -> Int32:\n"
            "  %b: Int32 = %a\n"
            "  return %b\n"
        )

    def test_dump_empty(self):
        # `int` is BigInt -- not an eligible scalar, so nothing routes.
        thir = _lower("def f(a: int) -> int:\n    return a\n")
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
