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
from ..codegen_cpp.forms import LocalBinding
from .nodes import (
    Form, THIRAssign, THIRBinOp, THIRCall, THIRFieldAccess, THIRForRange,
    THIRFormConvert, THIRIf, THIRLiteral, THIRName, THIRReturn, THIRSelf,
    THIRVarDecl, THIRWhile,
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


def _lower_ctx(source: str):
    """Lower inside the compiler context -- required once non-value records are
    involved: `NominalType.is_user_record` / `.to_cpp()` resolve through the
    active Compiler (the registry / native-name maps), unlike the value-scalar
    types `_lower` covers."""
    from ..compilation_context import activate_compiler
    compiler, modules = _compile(source)
    entry = _entry(modules)
    with activate_compiler(compiler):
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

    def test_staticmethod_is_ineligible(self):
        # The method frontier (M1) admits instance methods only; a staticmethod
        # has no `self` receiver and takes a different emit path.
        thir = _lower_ctx(
            _PRELUDE
            + "class C:\n    @staticmethod\n    def m(a: Int32) -> Int32:\n        b = a\n        return b\n")
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


# --- F1 form rung: single-assignment non-value record locals + field reads ---

_F1_RECORDS = (
    "from tpy import Int32, Own, readonly\n"
    "class Leaf:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32):\n        self.n = n\n"
    "class Inner:\n"
    "    value: Int32\n"
    "    opt: Leaf | None\n"
    "    def __init__(self, value: Int32):\n        self.value = value\n        self.opt = None\n"
    "class Box:\n"
    "    inner: Inner\n"
    "    opt: Inner | None\n"
    "    n: Int32\n"
    "    def __init__(self, inner: Own[Inner]):\n"
    "        self.inner = inner\n        self.opt = None\n        self.n = 0\n"
)


class TestF1Eligibility:
    def test_ref_alias_local_eligible(self):
        # x = b.inner -- a plain record field read binds a single-assignment T&
        # alias (REF_ALIAS); x.value is a scalar field read off it.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box) -> Int32:\n    x = b.inner\n    return x.value\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.REF_ALIAS
        assert decl.form is Form.BORROW
        assert decl.cpp_type == "Inner"
        assert isinstance(decl.init, THIRFieldAccess)
        assert decl.init.field_cpp == "inner" and not decl.init.is_arrow
        assert decl.init.form is Form.STORAGE
        # the scalar field read off the REF_ALIAS local
        read = fn.body[1].value
        assert isinstance(read, THIRFieldAccess) and read.field_cpp == "value"
        assert read.form is Form.VALUE

    def test_optional_to_ptr_local_eligible(self):
        # p = b.opt -- a storage-form Optional[record] field read lifts to a
        # borrow T* via optional_to_ptr (OPTIONAL_TO_PTR); const because b is a
        # const-ref param.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box) -> Int32:\n    p = b.opt\n    return 0\n")
        decl = _fn(thir, "f").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR
        assert decl.form is Form.BORROW and decl.is_const
        assert decl.cpp_type == "Inner"
        assert isinstance(decl.init, THIRFormConvert)
        assert decl.init.form is Form.BORROW
        assert isinstance(decl.init.value, THIRFieldAccess)
        assert decl.init.value.form is Form.STORAGE

    def test_scalar_field_read_off_param_eligible(self):
        # The working, common F1 pattern: scalar field reads off a record param
        # (value form, no borrow local). `return p.x + p.y` and `a = p.x`.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box) -> Int32:\n    a = b.n\n    return a\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        # a scalar local; the field read is a plain value-form field access
        assert decl.cpp_local_representation is None
        assert isinstance(decl.init, THIRFieldAccess)
        assert decl.init.form is Form.VALUE and decl.init.field_cpp == "n"

    def test_method_routes_via_self_receiver(self):
        # The method frontier (M1): a method's `self.field` reads route the same
        # as a record param's. `x = self.b` binds a REF_ALIAS off the `this`
        # receiver; `x.n` is a scalar read off it.
        thir = _lower_ctx(
            _F1_RECORDS
            + "class Wrap:\n    b: Box\n"
            + "    def __init__(self, b: Own[Box]):\n        self.b = b\n"
            + "    def get(self) -> Int32:\n        x = self.b\n        return x.n\n")
        fn = _fn(thir, "get")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.REF_ALIAS
        # the field source reads `self->b` (the `this` pointer renders `->`)
        assert isinstance(decl.init, THIRFieldAccess)
        assert isinstance(decl.init.receiver, THIRSelf) and decl.init.is_arrow

    def test_reassigned_nonvalue_local_routes_as_pointer(self):
        # F2: a reassigned plain-record local with lvalue field sources is a
        # reseatable `T*` pointer-local (POINTER), no longer AST-only.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box, c: Box) -> Int32:\n"
            + "    x = b.inner\n    x = c.inner\n    return x.value\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.POINTER
        assert decl.form is Form.BORROW and decl.cpp_type == "Inner"
        assert isinstance(decl.init, THIRFormConvert)  # &(b.inner)
        assert decl.init.form is Form.BORROW
        assert isinstance(decl.init.value, THIRFieldAccess)
        assert decl.init.value.form is Form.STORAGE

    def test_call_passing_record_arg_is_ineligible(self):
        # Passing an Own[record] / record param positionally crosses an ownership
        # boundary (an Own param auto-moves at last use: `consume(std::move(p))`),
        # which the bare-name THIRCall emit does not reproduce -- so the caller
        # stays on the AST path even though the callee is a plain free function.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def consume(p: Own[Inner]) -> Int32:\n        return p.value\n"
            + "def forward(b: Box) -> Int32:\n        return consume(b.inner)\n")
        # consume itself (Own[record] param + scalar field read) is eligible;
        # forward (passes a record arg) is not.
        assert _fn(thir, "consume") is not None
        assert _fn(thir, "forward") is None

    def test_method_call_on_record_is_ineligible(self):
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box) -> Int32:\n    x = b.inner\n    return x.value + b.n\n")
        # b.n is a scalar field read (fine); but a method call would not be. Use
        # one with a method call to confirm rejection.
        thir2 = _lower_ctx(
            _F1_RECORDS
            + "class Counter:\n    k: Int32\n"
            + "    def __init__(self):\n        self.k = 0\n"
            + "    def bump(self) -> Int32:\n        self.k = self.k + 1\n        return self.k\n"
            + "def g(c: Counter) -> Int32:\n    return c.bump()\n")
        assert _fn(thir2, "g") is None
        # The pure field-read function is eligible.
        assert _fn(thir, "f") is not None


class TestF1Emit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F1_RECORDS
        + "def read_ref(b: Box) -> Int32:\n    x = b.inner\n    return x.value\n"
        + "def read_opt(b: Box) -> Int32:\n    p = b.opt\n    return 0\n"
        + "def scalar(b: Box) -> Int32:\n    a = b.n\n    return a + b.n\n"
        + "def chain(b: Box) -> Int32:\n    x = b.inner\n    y = x.value\n    return y\n"
        # const path: a readonly receiver makes the REF_ALIAS a `const Inner&`,
        # and the chained Optional read off that const local a `const Inner*`
        # (exercises both _f1_is_const branches: the ReadonlyType read and the
        # const-propagation through a const F1 local).
        + "def ro_chain(b: readonly[Box]) -> Int32:\n"
        + "    x = b.inner\n    p = x.opt\n    return x.value\n"
        + "def main():\n"
        + "    box = Box(Inner(3))\n"
        + "    print(read_ref(box) + read_opt(box) + scalar(box) + chain(box) + ro_chain(box))\n"
        + "main()\n"
    )

    def test_f1_byte_identical(self):
        # The load-bearing F1 contract: every routed form (REF_ALIAS,
        # OPTIONAL_TO_PTR, scalar field reads) emits identically to the AST path.
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_ref_alias_emits_reference(self):
        # T& alias of the field storage (non-const here: the field is a plain
        # record off the param -- sema does not mark the read readonly).
        cpp = self._cpp(self.SRC, thir=True)
        assert "Inner& x = b.inner;" in cpp
        assert "return x.value;" in cpp

    def test_optional_to_ptr_emits_lift(self):
        # const because the receiver param is const (an F1 body cannot mutate it).
        cpp = self._cpp(self.SRC, thir=True)
        assert "const Inner* p = ::tpy::optional_to_ptr(b.opt);" in cpp

    def test_scalar_field_read_emits(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "int32_t a = b.n;" in cpp        # scalar field read into a local
        assert cpp.count("b.n") >= 2            # the read + the return operand

    def test_const_borrow_local_forms(self):
        # A readonly receiver yields a `const Inner&` REF_ALIAS, and the chained
        # Optional read off that const local a `const Inner*` -- guards the two
        # _f1_is_const const paths (the byte-identical assertion above already
        # pins them to the AST path; these check the const spelling explicitly).
        cpp = self._cpp(self.SRC, thir=True)
        assert "const Inner& x = b.inner;" in cpp
        assert "const Leaf* p = ::tpy::optional_to_ptr(x.opt);" in cpp


# --- F2 form rung: reassigned/rebound pointer-locals (lvalue reseat) ---


class TestF2PointerLocal:
    def test_rvalue_reseat_is_ineligible(self):
        # Reseating from an rvalue (a constructor) needs the `__slot_N` rebind
        # machinery -- deferred past F2's lvalue-reseat slice -> AST path.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box, which: Int32) -> Int32:\n"
            + "    x = b.inner\n    if which < 0:\n        x = Inner(9)\n    return x.value\n")
        assert _fn(thir, "f") is None

    def test_name_alias_reseat_is_ineligible(self):
        # Reseating from a name (not a field source) is the deferred name-alias
        # case -> AST path.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box, c: Box, which: Int32) -> Int32:\n"
            + "    x = b.inner\n    y = c.inner\n"
            + "    if which < 0:\n        x = y\n    return x.value\n")
        assert _fn(thir, "f") is None

    def test_reseat_lowers_to_assign_with_convert(self):
        # The reseat is a THIRAssign whose value is the `&(...)` storage->borrow
        # convert; the read off the pointer-local is an arrow field access.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box, c: Box, which: Int32) -> Int32:\n"
            + "    x = b.inner\n    if which < 0:\n        x = c.inner\n    return x.value\n")
        fn = _fn(thir, "f")
        assert fn is not None
        reseat = fn.body[1].then_body[0]
        assert isinstance(reseat, THIRAssign) and reseat.target.name == "x"
        assert isinstance(reseat.value, THIRFormConvert)
        assert reseat.value.form is Form.BORROW
        assert isinstance(reseat.value.value, THIRFieldAccess)  # c.inner
        ret = fn.body[2]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRFieldAccess) and ret.value.is_arrow


class TestF2Emit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F1_RECORDS
        + "def reseat(b: Box, c: Box, which: Int32) -> Int32:\n"
        + "    x = b.inner\n    if which < 0:\n        x = c.inner\n    return x.value\n"
        + "def main():\n"
        + "    box = Box(Inner(3))\n    print(reseat(box, box, -1))\n"
        + "main()\n"
    )

    def test_f2_byte_identical(self):
        # The load-bearing F2a contract: the pointer-local init / reseat / arrow
        # read emit identically to the AST path.
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_pointer_local_init_and_reseat(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "Inner* x = &(b.inner);" in cpp
        assert "x = &(c.inner);" in cpp

    def test_arrow_read_off_pointer_local(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "return x->value;" in cpp

    def test_const_pointer_local(self):
        # A readonly receiver makes the reseatable pointer-local a `const Inner*`
        # (exercises _f1_is_const for POINTER), reseated and read identically.
        src = (
            _F1_RECORDS
            + "def f(b: readonly[Box], c: readonly[Box], which: Int32) -> Int32:\n"
            + "    x = b.inner\n    if which < 0:\n        x = c.inner\n    return x.value\n"
            + "def main():\n    box = Box(Inner(1))\n    print(f(box, box, -1))\nmain()\n")
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
        cpp = self._cpp(src, thir=True)
        assert "const Inner* x = &(b.inner);" in cpp
        assert "return x->value;" in cpp


# --- F2b form rung: Optional borrow->storage write (ptr_to_optional) ---


class TestF2bWrite:
    def test_optional_field_write_routes(self):
        # p = src.opt (OPTIONAL_TO_PTR borrow) ; dst.opt = p lowers to a field-target
        # THIRAssign whose value is the borrow->storage convert (ptr_to_optional).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def move_opt(src: Box, dst: Box):\n    p = src.opt\n    dst.opt = p\n")
        fn = _fn(thir, "move_opt")
        assert fn is not None
        write = fn.body[1]
        assert isinstance(write, THIRAssign)
        assert isinstance(write.target, THIRFieldAccess) and write.target.field_cpp == "opt"
        assert isinstance(write.value, THIRFormConvert) and write.value.form is Form.STORAGE
        assert write.value.value.form is Form.BORROW  # the `p` borrow being lifted

    def test_copy_acknowledged_value_is_ineligible(self):
        # `copy(p)` (the explicit acknowledgment) is a call -- deferred to the AST path.
        thir = _lower_ctx(
            _F1_RECORDS
            + "from tpy import copy\n"
            + "def move_opt(src: Box, dst: Box):\n    p = src.opt\n    dst.opt = copy(p)\n")
        assert _fn(thir, "move_opt") is None

    def test_scalar_field_write_routes_as_plain_assign(self):
        # A scalar (non-optional) field write is not the F2b borrow->storage shape;
        # it routes via the scalar-field-write cell as a plain value assign.
        thir = _lower_ctx(
            _F1_RECORDS + "def setn(dst: Box):\n    dst.n = 5\n")
        st = _fn(thir, "setn").body[0]
        assert isinstance(st, THIRAssign) and isinstance(st.target, THIRFieldAccess)
        assert not isinstance(st.value, THIRFormConvert)

    def test_ref_alias_value_is_ineligible(self):
        # A `T&` REF_ALIAS value is not a `T*` pointer source (the AST path emits it
        # differently), so an optional-field write from it stays on the AST path.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(src: Box, dst: Box):\n    x = src.inner\n    dst.opt = x\n")
        assert _fn(thir, "f") is None


class TestF2bEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F1_RECORDS
        + "def move_opt(src: Box, dst: Box):\n    p = src.opt\n    dst.opt = p\n"
        + "def main():\n    a = Box(Inner(1))\n    b = Box(Inner(2))\n    move_opt(a, b)\nmain()\n"
    )

    def test_f2b_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_ptr_to_optional(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "dst.opt = ::tpy::ptr_to_optional(p);" in cpp

    def test_written_receiver_is_non_const(self):
        # Mutation enters the slice: the written receiver is a non-const `Box&`,
        # the read-only one a `const Box&` (pure const_borrow_params sema read).
        cpp = self._cpp(self.SRC, thir=True)
        assert "void move_opt(const Box& src, Box& dst)" in cpp


class TestF2PointerReceiver:
    """F2 paths where a POINTER local is itself the field-access receiver, so the
    field renders `x->field`: an Optional READ source (`optional_to_ptr(x->opt)`)
    and an Optional WRITE target (`x->opt = ptr_to_optional(leaf)`). Both are
    admitted by the F2a/F2b gates (a POINTER local is an F1-record receiver) and
    must stay byte-identical to the AST path; neither the scaffold nor the two
    corpus cases exercised them before."""

    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F1_RECORDS
        + "def read_opt(b: Box, c: Box, which: Int32) -> Int32:\n"
        + "    x = b.inner\n    if which < 0:\n        x = c.inner\n    q = x.opt\n    return 0\n"
        + "def write_opt(b: Box, c: Box, src: Inner, which: Int32):\n"
        + "    x = b.inner\n    if which < 0:\n        x = c.inner\n    leaf = src.opt\n    x.opt = leaf\n"
        + "def main():\n"
        + "    bx = Box(Inner(1))\n    cx = Box(Inner(2))\n    s = Inner(3)\n"
        + "    print(read_opt(bx, cx, -1))\n    write_opt(bx, cx, s, 1)\nmain()\n"
    )

    def test_both_route(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "read_opt") is not None
        assert _fn(thir, "write_opt") is not None

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_optional_source_off_pointer_local(self):
        # `q = x.opt` off a POINTER local -> the storage read uses `x->opt`.
        cpp = self._cpp(self.SRC, thir=True)
        assert "::tpy::optional_to_ptr(x->opt)" in cpp

    def test_optional_write_off_pointer_local(self):
        # `x.opt = leaf` off a POINTER local -> the write target uses `x->opt`.
        cpp = self._cpp(self.SRC, thir=True)
        assert "x->opt = ::tpy::ptr_to_optional(leaf);" in cpp

    def test_reassigned_optional_local_is_ineligible(self):
        # A reassigned OPTIONAL_TO_PTR (optional pointer-local) needs the rebind-
        # slot machinery, so it stays on the AST path (the optional branch of
        # classify_local_binding returns OTHER for a reassigned name).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box, c: Box, which: Int32) -> Int32:\n"
            + "    p = b.opt\n    if which < 0:\n        p = c.opt\n    return 0\n")
        assert _fn(thir, "f") is None


# --- F2c form rung: storage-form Optional[record] return + None write ---


class TestF2cReturn:
    def test_borrow_return_routes(self):
        # A storage-form `Own[Inner] | None` return lifts a borrow `T*` via
        # ptr_to_optional (copy): the return value is a borrow->storage convert.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def find(b: Box) -> Own[Inner] | None:\n    p = b.opt\n    return p\n")
        fn = _fn(thir, "find")
        assert fn is not None
        ret = fn.body[1]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRFormConvert) and ret.value.form is Form.STORAGE
        assert ret.value.value.form is Form.BORROW  # the `p` borrow being lifted

    def test_none_return_routes(self):
        # `return None` into a storage-form Optional lowers to a STORAGE-form None
        # literal (-> std::nullopt), not a borrow convert.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def nothing(b: Box) -> Own[Inner] | None:\n    return None\n")
        fn = _fn(thir, "nothing")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRLiteral) and ret.value.value is None
        assert ret.value.form is Form.STORAGE

    def test_pointer_repr_return_is_ineligible(self):
        # `Inner | None` is pointer-repr (the function returns a borrow `Inner*`),
        # a different direction than the storage `Own[Inner] | None` slot -> AST path.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box) -> Inner | None:\n    p = b.opt\n    return p\n")
        assert _fn(thir, "f") is None

    def test_rvalue_return_is_ineligible(self):
        # A non-borrow, non-None source (here an rvalue ctor) into the storage
        # return slot is the direct-construction branch -- deferred to the AST path.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box) -> Own[Inner] | None:\n    return Inner(5)\n")
        assert _fn(thir, "f") is None

    def test_pointer_local_borrow_return_routes(self):
        # The borrow-return source via the POINTER (not OPTIONAL_TO_PTR) branch of
        # `_is_borrow_ptr_local`: a reseatable `T*` returned into Own[Inner]|None
        # copies (a POINTER is a non-owning borrow -> move=False).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box, c: Box, which: Int32) -> Own[Inner] | None:\n"
            + "    x = b.inner\n    if which < 0:\n        x = c.inner\n    return x\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[2]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRFormConvert)
        assert ret.value.form is Form.STORAGE and ret.value.move is False


class TestF2cNoneWrite:
    def test_none_field_write_routes(self):
        # `b.opt = None` lowers to a field-target THIRAssign whose value is a
        # STORAGE-form None literal (-> std::nullopt), no form convert.
        thir = _lower_ctx(
            _F1_RECORDS + "def clear(b: Box):\n    b.opt = None\n")
        fn = _fn(thir, "clear")
        assert fn is not None
        write = fn.body[0]
        assert isinstance(write, THIRAssign)
        assert isinstance(write.target, THIRFieldAccess) and write.target.field_cpp == "opt"
        assert isinstance(write.value, THIRLiteral) and write.value.value is None
        assert write.value.form is Form.STORAGE


class TestF2cEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F1_RECORDS
        + "def find(b: Box) -> Own[Inner] | None:\n    p = b.opt\n    return p\n"
        + "def nothing(b: Box) -> Own[Inner] | None:\n    return None\n"
        + "def clear(b: Box):\n    b.opt = None\n"
        + "def main():\n"
        + "    bx = Box(Inner(3))\n    clear(bx)\n    a = find(bx)\n    c = nothing(bx)\n    print(0)\n"
        + "main()\n"
    )

    def test_f2c_byte_identical(self):
        # The load-bearing F2c contract: the borrow-return lift, the None return,
        # and the None field write all emit identically to the AST path.
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_borrow_return_emits_ptr_to_optional(self):
        assert "return ::tpy::ptr_to_optional(p);" in self._cpp(self.SRC, thir=True)

    def test_none_return_emits_nullopt(self):
        assert "return std::nullopt;" in self._cpp(self.SRC, thir=True)

    def test_none_write_emits_nullopt(self):
        assert "b.opt = std::nullopt;" in self._cpp(self.SRC, thir=True)

    def test_none_write_via_pointer_receiver(self):
        # `x.opt = None` off a POINTER local receiver -> `x->opt = std::nullopt;`
        # (the None write through the arrow-receiver path), byte-identical.
        src = (
            _F1_RECORDS
            + "def clearp(b: Box, c: Box, which: Int32):\n"
            + "    x = b.inner\n    if which < 0:\n        x = c.inner\n    x.opt = None\n"
            + "def main():\n    bx = Box(Inner(1))\n    clearp(bx, bx, 1)\nmain()\n"
        )
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
        assert "x->opt = std::nullopt;" in self._cpp(src, thir=True)


# --- F2d form rung: rvalue rebind-slot pointer-locals (the __slot_N machinery) ---


class TestF2dRebindSlot:
    def test_rvalue_reassigned_routes(self):
        # An rvalue-reassigned plain-record local is a rebind-slot pointer-local:
        # the decl is a REBIND_SLOT whose init is the rvalue ctor (no convert),
        # the reseat a plain THIRAssign of the ctor, and reads are arrow accesses.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def reb() -> Int32:\n"
            + "    p = Inner(1)\n    a = p.value\n    p = Inner(2)\n    return p.value + a\n")
        fn = _fn(thir, "reb")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.REBIND_SLOT
        assert decl.form is Form.BORROW and decl.cpp_type == "Inner"
        assert isinstance(decl.init, THIRCall) and decl.init.callee == "Inner"
        read = fn.body[1].init  # a = p.value -> arrow read off the pointer-local
        assert isinstance(read, THIRFieldAccess) and read.is_arrow
        reseat = fn.body[2]
        assert isinstance(reseat, THIRAssign) and reseat.target.name == "p"
        assert isinstance(reseat.value, THIRCall) and reseat.value.callee == "Inner"

    def test_single_assignment_rvalue_is_ineligible(self):
        # No reassignment -> a plain value local (`Inner p = Inner(1);`), not a
        # rebind-slot pointer-local -> stays on the AST path.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f() -> Int32:\n    p = Inner(1)\n    return p.value\n")
        assert _fn(thir, "f") is None

    def test_kwarg_ctor_normalizes_and_routes(self):
        # sema rewrites a single-param ctor kwarg to a positional arg before
        # lowering (`Inner(value=1)` -> `Inner(1)`), so it still routes as a
        # rebind-slot rvalue source. (The `init.kwargs` guard only fires for a
        # ctor sema leaves un-normalized.)
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f() -> Int32:\n"
            + "    p = Inner(value=1)\n    a = p.value\n    p = Inner(value=2)\n    return a + p.value\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert decl.cpp_local_representation is LocalBinding.REBIND_SLOT
        assert isinstance(decl.init, THIRCall) and len(decl.init.args) == 1

    def test_record_arg_ctor_source_is_ineligible(self):
        # A rebind-slot ctor whose arg is a non-scalar (a record value-local) needs
        # the AST's arg deref / auto-move, which the bare THIRCall arg emit does not
        # reproduce -- so the arg gate (mirroring _call_eligible) rejects it -> AST
        # path. (Box's ctor takes Own[Inner]; `a` is a record value-local.)
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f() -> Int32:\n"
            + "    a = Inner(0)\n    p = Box(a)\n    p = Box(a)\n    return p.n\n")
        assert _fn(thir, "f") is None

    def test_function_call_rebind_source_routes(self):
        # A by-value record-returning FREE FUNCTION (not a ctor) is also a valid
        # rebind-slot rvalue source; it emits as the bare `make_inner()`.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def make_inner() -> Own[Inner]:\n    return Inner(9)\n"
            + "def f() -> Int32:\n"
            + "    p = make_inner()\n    p = make_inner()\n    return p.value\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert decl.cpp_local_representation is LocalBinding.REBIND_SLOT
        assert isinstance(decl.init, THIRCall) and decl.init.callee == "make_inner"

    def test_conditional_reseat_routes(self):
        # A REBIND_SLOT reseat inside an `if`-body (the in-branch reseat path).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(x: Int32) -> Int32:\n"
            + "    p = Inner(1)\n    if x < 0:\n        p = Inner(2)\n    return p.value\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert fn.body[0].cpp_local_representation is LocalBinding.REBIND_SLOT
        reseat = fn.body[1].then_body[0]  # the in-branch reseat
        assert isinstance(reseat, THIRAssign) and isinstance(reseat.value, THIRCall)

    def test_lvalue_reseat_of_rebind_slot_is_ineligible(self):
        # A REBIND_SLOT local (rvalue first decl) reseated with an lvalue field
        # source is the deferred mixed case -- `_is_record_rvalue_source` needs a
        # ctor/call, so it stays on the AST path (mirror of the POINTER+rvalue case).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box, x: Int32) -> Int32:\n"
            + "    p = Inner(1)\n    if x < 0:\n        p = b.inner\n    return p.value\n")
        assert _fn(thir, "f") is None


class TestF2dEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F1_RECORDS
        + "def reb() -> Int32:\n"
        + "    p = Inner(1)\n    a = p.value\n    p = Inner(2)\n    return p.value + a\n"
        # two rebind-slot locals -> __slot_1.._slot_4, exercising slot numbering.
        + "def two() -> Int32:\n"
        + "    p = Inner(1)\n    q = Inner(2)\n    p = Inner(3)\n    q = Inner(4)\n"
        + "    return p.value + q.value\n"
        + "def main():\n    print(reb() + two())\nmain()\n"
    )

    def test_f2d_byte_identical(self):
        # The load-bearing F2d contract: the two-slot init, the optional rebind
        # slot, the pointer reseat, and the arrow reads emit identically to the
        # AST path -- including slot numbering across two rebind-slot locals.
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_two_slot_init_and_reseat(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "Inner __slot_1 = Inner(1);" in cpp
        assert "std::optional<Inner> __slot_2;" in cpp
        assert "Inner* p = &__slot_1;" in cpp
        assert "p = &*(__slot_2 = Inner(2));" in cpp

    def test_arrow_reads_off_rebind_local(self):
        assert "int32_t a = p->value;" in self._cpp(self.SRC, thir=True)

    def test_slot_numbering_across_two_locals(self):
        # The second rebind-slot local numbers after the first (init then rebind):
        # p -> __slot_1/__slot_2, q -> __slot_3/__slot_4.
        cpp = self._cpp(self.SRC, thir=True)
        assert "Inner __slot_3 = Inner(2);" in cpp
        assert "std::optional<Inner> __slot_4;" in cpp
        assert "Inner* q = &__slot_3;" in cpp
        assert "q = &*(__slot_4 = Inner(4));" in cpp

    def test_function_call_source_byte_identical(self):
        # A by-value record-returning function as the rvalue source emits the bare
        # call into the two-slot form, byte-identical to the AST path.
        src = (
            _F1_RECORDS
            + "def make_inner() -> Own[Inner]:\n    return Inner(9)\n"
            + "def f() -> Int32:\n"
            + "    p = make_inner()\n    a = p.value\n    p = make_inner()\n    return p.value + a\n"
            + "def main():\n    print(f())\nmain()\n"
        )
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
        assert "Inner __slot_1 = make_inner();" in self._cpp(src, thir=True)

    def test_conditional_reseat_byte_identical(self):
        # A rebind-slot reseat inside an `if`-body emits identically to the AST path
        # (the slot is allocated at the top-level decl, reused in the branch).
        src = (
            _F1_RECORDS
            + "def f(x: Int32) -> Int32:\n"
            + "    p = Inner(1)\n    if x < 0:\n        p = Inner(2)\n    return p.value\n"
            + "def main():\n    print(f(-1))\nmain()\n"
        )
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)


# --- F2e form rung: the _move write + return variants (owned source) ---


class TestF2eMove:
    def test_write_move_routes(self):
        # An owned rebind-slot local written into an optional field at last use
        # lifts borrow->storage with move=True (-> ptr_to_optional_move).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def store(dst: Box):\n    p = Inner(1)\n    p = Inner(2)\n    dst.opt = p\n")
        fn = _fn(thir, "store")
        assert fn is not None
        write = fn.body[2]
        assert isinstance(write, THIRAssign) and isinstance(write.value, THIRFormConvert)
        assert write.value.form is Form.STORAGE and write.value.move is True

    def test_write_copy_when_not_last_use(self):
        # The same rebind-slot read again after the write is not a last use -> copy.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def keep(dst: Box) -> Int32:\n    p = Inner(1)\n    p = Inner(2)\n"
            + "    dst.opt = p\n    return p.value\n")
        write = _fn(thir, "keep").body[2]
        assert isinstance(write.value, THIRFormConvert) and write.value.move is False

    def test_return_move_routes(self):
        thir = _lower_ctx(
            _F1_RECORDS
            + "def make(flag: Int32) -> Own[Inner] | None:\n"
            + "    p = Inner(1)\n    p = Inner(2)\n    return p\n")
        ret = _fn(thir, "make").body[2]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRFormConvert)
        assert ret.value.form is Form.STORAGE and ret.value.move is True

    def test_nonowning_borrow_write_stays_copy(self):
        # Regression: an OPTIONAL_TO_PTR (non-owning) source is never movable, so
        # the optional-field write stays a copy (move=False) -- F2b unchanged.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def move_opt(src: Box, dst: Box):\n    p = src.opt\n    dst.opt = p\n")
        write = _fn(thir, "move_opt").body[1]
        assert isinstance(write.value, THIRFormConvert) and write.value.move is False


class TestF2eEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F1_RECORDS
        + "def store(dst: Box):\n    p = Inner(1)\n    p = Inner(2)\n    dst.opt = p\n"
        + "def keep(dst: Box) -> Int32:\n"
        + "    p = Inner(1)\n    p = Inner(2)\n    dst.opt = p\n    return p.value\n"
        + "def make(flag: Int32) -> Own[Inner] | None:\n"
        + "    p = Inner(1)\n    p = Inner(2)\n    return p\n"
        + "def main():\n    d = Box(Inner(0))\n    store(d)\n    print(keep(d))\n    r = make(0)\n"
        + "main()\n"
    )

    def test_f2e_byte_identical(self):
        # The load-bearing F2e contract: the move write, the copy write (not last
        # use), and the move return all emit identically to the AST path.
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_write_move_emits_move_helper(self):
        assert "dst.opt = ::tpy::ptr_to_optional_move(p);" in self._cpp(self.SRC, thir=True)

    def test_write_copy_emits_copy_helper(self):
        assert "dst.opt = ::tpy::ptr_to_optional(p);" in self._cpp(self.SRC, thir=True)

    def test_return_move_emits_move_helper(self):
        assert "return ::tpy::ptr_to_optional_move(p);" in self._cpp(self.SRC, thir=True)


# --- M1 method frontier: instance methods with a `self` (`this`) receiver ---

# Methods over the F1 records. `get_n` is a scalar field read (auto-readonly,
# const self); `head` a REF_ALIAS off self; `peek` an OPTIONAL_TO_PTR off a
# readonly self (-> `const Inner*`); `reset` a non-readonly method writing
# `self.opt = None` (mutates self -> non-const `this`).
_M1_METHODS = (
    _F1_RECORDS
    + "    def get_n(self) -> Int32:\n        return self.n\n"
    + "    def head(self) -> Int32:\n        x = self.inner\n        return x.value\n"
    + "    def peek(self) -> Int32:\n        p = self.opt\n        return 0\n"
    + "    def reset(self):\n        self.opt = None\n"
)


class TestMethodFrontier:
    def test_scalar_field_read_off_self(self):
        thir = _lower_ctx(_M1_METHODS)
        fn = _fn(thir, "get_n")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRFieldAccess)
        assert isinstance(ret.value.receiver, THIRSelf)
        assert ret.value.is_arrow and ret.value.form is Form.VALUE

    def test_ref_alias_off_self(self):
        decl = _fn(_lower_ctx(_M1_METHODS), "head").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.REF_ALIAS
        assert isinstance(decl.init.receiver, THIRSelf) and decl.init.is_arrow

    def test_optional_to_ptr_off_readonly_self_is_const(self):
        # A readonly method's `self` is const, so the borrow lifts to `const T*`.
        decl = _fn(_lower_ctx(_M1_METHODS), "peek").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR
        assert decl.form is Form.BORROW and decl.is_const

    def test_nonreadonly_method_routes(self):
        # `reset` writes `self.opt = None` -> self is non-readonly (non-const
        # `this`); routes via the F2c storage-form None write.
        assert _fn(_lower_ctx(_M1_METHODS), "reset") is not None

    def test_staticmethod_excluded(self):
        thir = _lower_ctx(
            _F1_RECORDS
            + "    @staticmethod\n    def smethod(a: Int32) -> Int32:\n        return a\n")
        assert _fn(thir, "smethod") is None

    def test_record_param_method_excluded(self):
        # A record param on a method is the M2 part of the frontier; M1 restricts
        # method params to value scalars.
        thir = _lower_ctx(
            _F1_RECORDS
            + "    def with_box(self, other: Box) -> Int32:\n        return other.n\n")
        assert _fn(thir, "with_box") is None

    def test_generic_record_method_excluded(self):
        # A generic record's `self` is templated -> outside the F1-record slice.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "class Wrap[T]:\n    n: Int32\n"
            "    def __init__(self, n: Int32):\n        self.n = n\n"
            "    def get(self) -> Int32:\n        return self.n\n")
        assert _fn(thir, "get") is None

    def test_constructor_excluded(self):
        # The ctor body is emitted via the member-init-list driver, not gen_body;
        # iter_module_callables skips record.init_method (the M3 ctor frontier).
        thir = _lower_ctx(_M1_METHODS)
        assert _fn(thir, "__init__") is None

    def test_property_getter_excluded(self):
        thir = _lower_ctx(
            _F1_RECORDS
            + "    @property\n    def doubled(self) -> Int32:\n        return self.n\n")
        assert _fn(thir, "doubled") is None

    def test_optional_to_ptr_off_mutable_self_is_nonconst(self):
        # A non-readonly method (writes self.opt) reads self.opt off a non-const
        # `this`, so the borrow lifts to a mutable `Inner*`, not `const Inner*`.
        thir = _lower_ctx(
            _F1_RECORDS
            + "    def churn(self):\n        p = self.opt\n        self.opt = None\n")
        decl = _fn(thir, "churn").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR
        assert decl.form is Form.BORROW and not decl.is_const


class TestMethodFrontierEmit:
    def _emit(self, src: str, thir: bool):
        # Instance methods emit inline in the struct (the .hpp), so the contract
        # is checked over header + source, not just the .cpp.
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _M1_METHODS
        + "def main():\n    b = Box(Inner(0))\n    print(b.get_n() + b.head())\n"
        + "    print(b.peek())\n    b.reset()\n"
        + "main()\n"
    )

    def test_methods_byte_identical(self):
        # The load-bearing contract for the frontier: method bodies emit
        # identically from THIR and the AST path.
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_self_renders_as_this_arrow(self):
        # The `self` receiver renders as the C++ `this` pointer with `->`.
        assert "return this->n;" in self._emit(self.SRC, thir=True)

    def test_readonly_self_optional_read_is_const(self):
        assert "const Inner* p = ::tpy::optional_to_ptr(this->opt);" in self._emit(self.SRC, thir=True)

    def test_nonreadonly_self_none_write(self):
        assert "this->opt = std::nullopt;" in self._emit(self.SRC, thir=True)


# --- Scalar field writes: `recv.field = <scalar>` off an F1-record receiver ---

_SCALAR_WRITE = (
    "from tpy import Int32\n"
    "class Counter:\n    count: Int32\n    other: Int32\n"
    "    def __init__(self, count: Int32):\n        self.count = count\n        self.other = 0\n"
    "    def reset(self):\n        self.count = 0\n"
    "    def copy_field(self):\n        self.count = self.other\n"
    # off a record param in a free function (non-self receiver)
    "def bump(c: Counter, n: Int32):\n    c.count = n\n    c.other = c.count + 1\n"
)


class TestScalarFieldWrite:
    def test_write_off_self_routes_as_plain_assign(self):
        # `self.count = 0` lowers to a plain THIRAssign (value form), NOT the F2b
        # borrow->storage path -- the field is a scalar, so no THIRFormConvert.
        fn = _fn(_lower_ctx(_SCALAR_WRITE), "reset")
        assert fn is not None
        st = fn.body[0]
        assert isinstance(st, THIRAssign)
        assert isinstance(st.target, THIRFieldAccess) and st.target.is_arrow
        assert not isinstance(st.value, THIRFormConvert)
        assert st.value.form is Form.VALUE

    def test_field_to_field_scalar_copy(self):
        st = _fn(_lower_ctx(_SCALAR_WRITE), "copy_field").body[0]
        assert isinstance(st, THIRAssign)
        assert isinstance(st.value, THIRFieldAccess) and st.value.form is Form.VALUE

    def test_write_off_record_param_routes(self):
        # A scalar field write off a record param in a free function (non-self).
        assert _fn(_lower_ctx(_SCALAR_WRITE), "bump") is not None

    def test_property_setter_target_excluded(self):
        # A field write that is really a @property setter takes a method-call
        # emit path, not a plain field assign -> stays on the AST path.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "class C:\n    _n: Int32\n"
            "    def __init__(self):\n        self._n = 0\n"
            "    @property\n    def n(self) -> Int32:\n        return self._n\n"
            "    @n.setter\n    def n(self, v: Int32):\n        self._n = v\n"
            "    def use(self):\n        self.n = 5\n")
        assert _fn(thir, "use") is None

    def test_write_off_pointer_local_routes_with_arrow(self):
        # Receiver is an F2 reseatable `T*` pointer-local -- the third
        # `_field_receiver_ok` receiver kind, rendering `->` (distinct from self's
        # `this->` and a record param's `.`).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def via_ptr(b: Box, c: Box):\n    x = b.inner\n    x = c.inner\n    x.value = 5\n")
        fn = _fn(thir, "via_ptr")
        assert fn is not None
        st = fn.body[-1]
        assert isinstance(st, THIRAssign)
        assert isinstance(st.target, THIRFieldAccess) and st.target.is_arrow

    def test_mixed_scalar_and_optional_write_body(self):
        # Both write forms in one body exercise the `or`-dispatch in
        # `_stmt_eligible` -- neither blocks the other's eligibility.
        thir = _lower_ctx(
            _F1_RECORDS + "def mixed(b: Box):\n    b.n = 7\n    b.opt = None\n")
        assert _fn(thir, "mixed") is not None


class TestScalarFieldWriteEmit:
    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _SCALAR_WRITE
        + "def main():\n    c = Counter(3)\n    c.reset()\n    c.copy_field()\n    bump(c, 7)\n"
        + "    print(c.count + c.other)\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_self_scalar_write_emits_arrow_assign(self):
        assert "this->count = 0;" in self._emit(self.SRC, thir=True)

    def test_param_scalar_write_emits_dot_assign(self):
        assert "c.count = n;" in self._emit(self.SRC, thir=True)

    def test_pointer_local_scalar_write_emits_arrow(self):
        src = (
            _F1_RECORDS
            + "def via_ptr(b: Box, c: Box):\n    x = b.inner\n    x = c.inner\n    x.value = 5\n"
            + "def main():\n    b = Box(Inner(1))\n    via_ptr(b, b)\n    print(b.inner.value)\n"
            + "main()\n")
        assert "x->value = 5;" in self._emit(src, thir=True)
        assert self._emit(src, thir=True) == self._emit(src, thir=False)
