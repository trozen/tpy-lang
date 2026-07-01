"""THIR value-scalar slice: lowering eligibility/shape, dump, and the
byte-identical emit contract (THIR codegen == AST codegen for the slice)."""

from __future__ import annotations

import io

from .. import get_lib_dir
from ..codegen_cpp.context import CodeGenOptions
from ..compiler import Compiler
from .dump import dump_thir
from .emit import emit_thir_body, emit_thir_constructor_tail
from .lower import lower_module
from ..codegen_cpp.forms import LocalBinding
from .nodes import (
    Form, THIRAssign, THIRBinOp, THIRCall, THIRFieldAccess, THIRForRange,
    THIRFormConvert, THIRIf, THIRLiteral, THIRName, THIRReturn, THIRSelf,
    THIRSubscript, THIRVarDecl, THIRWhile,
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


def _lower_ctor(source: str, record_name: str):
    """Lower one record's constructor to its THIRConstructor (or None if outside
    the M3 slice). Within the compiler context -- records resolve through the live
    registry / native-name maps, like `_lower_ctx`."""
    from ..compilation_context import activate_compiler
    from .lower import iter_module_constructors, lower_constructor
    compiler, modules = _compile(source)
    entry = _entry(modules)
    with activate_compiler(compiler):
        for rec, init, self_type in iter_module_constructors(entry.ast, entry.analyzer):
            if rec.name == record_name:
                return lower_constructor(rec, init, entry.analyzer,
                                         self_type=self_type)
    return None


def _ctor_tail(ctor) -> str:
    buf = io.StringIO()
    emit_thir_constructor_tail(buf, ctor)
    return buf.getvalue()


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

    def test_mixed_mutation_free_function_keys_on_param_index(self):
        # A free function mutates b but only reads a.opt; the const verdict must
        # key on a's param index (0), not b's (1) -- exercises the index-based
        # _param_is_const(record_name=None) lookup that a single-param fn never does.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def mix(a: Box, b: Box) -> Int32:\n"
            + "    b.n = 1\n    p = a.opt\n    return 0\n")
        decl = next(s for s in _fn(thir, "mix").body
                    if isinstance(s, THIRVarDecl)
                    and s.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR)
        assert decl.form is Form.BORROW and decl.is_const

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


# --- F3 form rung: storage->borrow tuple read (tuple_to_pointer) ---

# A record with a pointer-repr tuple field: storage form `std::tuple<int32_t,
# Leaf>`, borrow form `std::tuple<int32_t, Leaf*>`.
_F3_RECORDS = (
    "from tpy import Int32, readonly\n"
    "class Leaf:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32):\n        self.n = n\n"
    "class Holder:\n"
    "    pair: tuple[Int32, Leaf]\n"
    "    def __init__(self, b: Leaf):\n        self.pair = (1, b)\n"
)


class TestF3TupleReturn:
    def test_borrow_tuple_return_routes(self):
        # `return h.pair` lifts the storage tuple field into the borrow-form tuple
        # return via a STORAGE->BORROW convert (the tuple_to_pointer family).
        thir = _lower_ctx(
            _F3_RECORDS
            + "def ret_field(h: Holder) -> tuple[Int32, Leaf]:\n    return h.pair\n")
        fn = _fn(thir, "ret_field")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRFormConvert) and ret.value.form is Form.BORROW
        assert ret.value.is_const is False  # mutable receiver -> mutable Leaf* elements
        assert ret.value.value.form is Form.STORAGE  # the h.pair storage read

    def test_value_tuple_return_is_ineligible(self):
        # An all-value-scalar tuple has no pointer-repr element (borrow == storage),
        # so no tuple_to_pointer lift applies -- it stays on the AST path.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def ret_pair(h: Holder) -> tuple[Int32, Int32]:\n    return (1, 2)\n")
        assert _fn(thir, "ret_pair") is None

    def test_tuple_field_init_ctor_stays_on_ast_path(self):
        # Regression guard for the M3 ctor-frontier fix: `Holder.__init__` does
        # `self.pair = (1, b)` -- a leading own-field init of an F3+ tuple type the
        # AST hoists into the member-init-list but THIR cannot reproduce there. It
        # must REJECT the whole ctor (return None, AST path) rather than demote the
        # init into the body, which would diverge from the AST's MIL hoist.
        assert _lower_ctor(_F3_RECORDS, "Holder") is None

    def test_storage_tuple_alias_local_routes(self):
        # A storage-tuple alias local (`t = h.pair`) binds `auto&&` (a STORAGE-form
        # alias) and a `return t` lifts it via tuple_to_pointer like a direct field
        # source.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def ret_alias(h: Holder) -> tuple[Int32, Leaf]:\n"
            + "    t = h.pair\n    return t\n")
        fn = _fn(thir, "ret_alias")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.STORAGE_TUPLE_ALIAS
        assert decl.form is Form.STORAGE
        ret = fn.body[1]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRFormConvert)
        assert ret.value.form is Form.BORROW
        assert ret.value.value.form is Form.STORAGE  # the `t` alias read


class TestF3TupleReturnEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F3_RECORDS
        + "def ret_field(h: Holder) -> tuple[Int32, Leaf]:\n    return h.pair\n"
        + "def main():\n    h = Holder(Leaf(5))\n    t = ret_field(h)\n    print(0)\n"
        + "main()\n"
    )

    def test_f3_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_tuple_to_pointer(self):
        assert ("return ::tpy::tuple_to_pointer<std::tuple<int32_t, Leaf*>>(h.pair);"
                in self._cpp(self.SRC, thir=True))

    ALIAS_SRC = (
        _F3_RECORDS
        + "def ret_alias(h: Holder) -> tuple[Int32, Leaf]:\n"
        + "    t = h.pair\n    return t\n"
        + "def main():\n    h = Holder(Leaf(5))\n    t = ret_alias(h)\n    print(0)\n"
        + "main()\n"
    )

    def test_alias_byte_identical(self):
        assert self._cpp(self.ALIAS_SRC, thir=True) == self._cpp(self.ALIAS_SRC, thir=False)

    def test_alias_emits_auto_ref(self):
        cpp = self._cpp(self.ALIAS_SRC, thir=True)
        assert "auto&& t = h.pair;" in cpp
        assert "return ::tpy::tuple_to_pointer<std::tuple<int32_t, Leaf*>>(t);" in cpp

    # A const (readonly) receiver makes the borrow tuple's element pointers const,
    # exercising the `to_cpp_return_const()` arm of the tuple_to_pointer lift -- for
    # both a direct field return and a storage-tuple alias local.
    CONST_SRC = (
        _F3_RECORDS
        + "def f(h: readonly[Holder]) -> tuple[Int32, readonly[Leaf]]:\n"
        + "    return h.pair\n"
        + "def g(h: readonly[Holder]) -> tuple[Int32, readonly[Leaf]]:\n"
        + "    t = h.pair\n    return t\n"
        + "def main():\n    h = Holder(Leaf(5))\n    a = f(h)\n    b = g(h)\n    print(0)\n"
        + "main()\n"
    )

    def test_const_byte_identical(self):
        assert self._cpp(self.CONST_SRC, thir=True) == self._cpp(self.CONST_SRC, thir=False)

    def test_const_receiver_emits_const_tuple_to_pointer(self):
        cpp = self._cpp(self.CONST_SRC, thir=True)
        # direct field return + alias local both lift with const element pointers.
        assert ("return ::tpy::tuple_to_pointer<std::tuple<int32_t, const Leaf*>>(h.pair);"
                in cpp)
        assert ("return ::tpy::tuple_to_pointer<std::tuple<int32_t, const Leaf*>>(t);"
                in cpp)

    def test_const_receiver_lowers_const_convert(self):
        thir = _lower_ctx(
            _F3_RECORDS
            + "def f(h: readonly[Holder]) -> tuple[Int32, readonly[Leaf]]:\n"
            + "    return h.pair\n")
        ret = _fn(thir, "f").body[0]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRFormConvert)
        assert ret.value.is_const is True


# --- F3 form rung: borrow->storage tuple field write (tuple_to_storage) ---

# Records with a pointer-repr Optional-element tuple field: storage form
# `std::tuple<std::optional<T>, ...>`, borrow form `std::tuple<T*, ...>`.
_F3_OPT_RECORDS = (
    "from tpy import Int32\n"
    "class T:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
    "class Holder:\n"
    "    pair: tuple[T | None, T | None]\n"
    "    def __init__(self) -> None:\n        self.pair = (None, None)\n"
)


class TestF3TupleFieldWrite:
    def test_borrow_tuple_field_write_routes(self):
        # `self.pair = p` where p is a borrow tuple param lifts borrow->storage via
        # a STORAGE-form convert (the tuple_to_storage family); the receiver becomes
        # a written (non-const) self.
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "class Setter:\n"
            + "    pair: tuple[T | None, T | None]\n"
            + "    def __init__(self) -> None:\n        self.pair = (None, None)\n"
            + "    def update(self, p: tuple[T | None, T | None]) -> None:\n"
            + "        self.pair = p\n")
        fn = _fn(thir, "update")
        assert fn is not None
        write = fn.body[0]
        assert isinstance(write, THIRAssign)
        assert isinstance(write.target, THIRFieldAccess) and write.target.field_cpp == "pair"
        assert isinstance(write.value, THIRFormConvert)
        assert write.value.form is Form.STORAGE and write.value.move is False
        assert write.value.value.form is Form.BORROW  # the borrow tuple param p

    def test_storage_source_field_write_is_ineligible(self):
        # A storage-form source (`other.pair`, a field read) is a direct copy with
        # no tuple_to_storage wrap -- a later F3 cell, so it stays on the AST path.
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def copy_from(h: Holder, other: Holder) -> None:\n"
            + "    h.pair = other.pair\n")
        assert _fn(thir, "copy_from") is None


class TestF3TupleFieldWriteEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F3_OPT_RECORDS
        + "def upd(h: Holder, p: tuple[T | None, T | None]) -> None:\n"
        + "    h.pair = p\n"
        + "def main():\n    h = Holder()\n    t = T(1)\n    upd(h, (t, None))\n    print(0)\n"
        + "main()\n"
    )

    def test_f3_write_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_tuple_to_storage(self):
        assert ("h.pair = ::tpy::tuple_to_storage<std::tuple<std::optional<T>, "
                "std::optional<T>>>(p);" in self._cpp(self.SRC, thir=True))


# --- Statement-shape axis: value-result tuple subscript reads (std::get<N>) ---

# The first cell of the statement-shape axis. A value-scalar tuple param
# (`const std::tuple<...>&`, its signature emitted by the AST path) read by
# subscript routes its body; the value-scalar slot of an already-routed
# pointer-repr tuple reads the same way. Borrow-result (record / Optional) element
# reads stay on the AST path.
class TestTupleSubscriptRead:
    def test_value_tuple_param_subscript_routes(self):
        # `tuple[Int32, Int32]` is admitted as a param; `p[0]` / `p[1]` lower to
        # value-form THIRSubscript reads off the param name.
        thir = _lower(
            _PRELUDE
            + "def consume(p: tuple[Int32, Int32]) -> Int32:\n    return p[0] + p[1]\n")
        fn = _fn(thir, "consume")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRBinOp)
        left = ret.value.left
        assert isinstance(left, THIRSubscript) and left.index == 0
        assert left.form is Form.VALUE
        assert isinstance(left.receiver, THIRName) and left.receiver.name == "p"
        assert ret.value.right.index == 1

    def test_negative_index_normalized(self):
        # `p[-3]` on a 3-tuple folds to index 0; `p[-1]` to index 2 -- the AST's
        # _extract_compile_time_index normalization.
        thir = _lower(
            _PRELUDE
            + "def f(p: tuple[Int32, Int32, Int32]) -> Int32:\n    return p[-3] + p[-1]\n")
        ret = _fn(thir, "f").body[0]
        assert ret.value.left.index == 0
        assert ret.value.right.index == 2

    def test_value_scalar_slot_of_pointer_repr_tuple_routes(self):
        # The Int32 slot of a pointer-repr tuple `tuple[Int32, Leaf]` reads as a
        # plain std::get (value form), reusing the already-routed borrow receiver.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def scalar_slot(t: tuple[Int32, Leaf]) -> Int32:\n    return t[0]\n")
        fn = _fn(thir, "scalar_slot")
        assert fn is not None
        sub = fn.body[0].value
        assert isinstance(sub, THIRSubscript) and sub.index == 0
        assert sub.form is Form.VALUE

    def test_value_scalar_tuple_local_is_ineligible(self):
        # Only value-tuple PARAMS are admitted in this cell; a value-tuple local
        # (`t = (1, 2)`) needs literal construction / init-source eligibility, a
        # later cell -- so a function building one stays on the AST path.
        thir = _lower(
            _PRELUDE
            + "def f() -> Int32:\n    t = (1, 2)\n    return t[0]\n")
        assert _fn(thir, "f") is None


class TestTupleSubscriptReadEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _PRELUDE
        + "def consume(p: tuple[Int32, Int32]) -> Int32:\n    return p[0] + p[1]\n"
        + "def main():\n    print(consume((1, 2)))\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_std_get(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "std::get<0>(p)" in cpp and "std::get<1>(p)" in cpp


# The routing-heavy subscript cell: a record-element read `t[N].field`. The subscript
# yields a borrow -- `std::get<N>(t)->field` off a borrow-form tuple param, or
# `std::get<N>(t).field` off a storage `auto&&` alias. Optional-element member access
# (null-check path), standalone binds, and writes stay on the AST path.
class TestTupleSubscriptRecordRead:
    def test_record_element_via_borrow_param_routes(self):
        thir = _lower_ctx(
            _F3_RECORDS
            + "def via_param(t: tuple[Int32, Leaf]) -> Int32:\n    return t[1].n\n")
        fn = _fn(thir, "via_param")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRFieldAccess)
        assert ret.value.is_arrow  # borrow-tuple param -> std::get<1>(t) is a T*
        sub = ret.value.receiver
        assert isinstance(sub, THIRSubscript) and sub.index == 1
        assert sub.form is Form.BORROW

    def test_record_element_via_storage_alias_reads_dot(self):
        thir = _lower_ctx(
            _F3_RECORDS
            + "def via_alias(h: Holder) -> Int32:\n    a = h.pair\n    return a[1].n\n")
        fn = _fn(thir, "via_alias")
        assert fn is not None
        ret = fn.body[1]
        assert isinstance(ret.value, THIRFieldAccess)
        assert ret.value.is_arrow is False  # storage auto&& alias -> T&, dot access
        assert isinstance(ret.value.receiver, THIRSubscript)

    def test_element_form_drives_arrow_vs_dot(self):
        # Element form, not the receiver alone, decides `->` vs `.`: an Own element is
        # held by value (`std::get<0>(p).n`, dot), a bare-reference element is a borrow
        # pointer (`std::get<1>(p)->n`, arrow). Regression for the mixed owned+borrow
        # tuple (_tuple_subscript_yields_borrow_ptr mirror).
        thir = _lower_ctx(
            "from tpy import Own, Int32\n"
            "class A:\n    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
            "def f(p: tuple[Own[A], A]) -> Int32:\n    return p[0].n + p[1].n\n")
        fn = _fn(thir, "f")
        assert fn is not None
        add = fn.body[0].value
        assert isinstance(add.left, THIRFieldAccess) and add.left.is_arrow is False
        assert isinstance(add.right, THIRFieldAccess) and add.right.is_arrow is True

    def test_negative_index_and_multi_element_record_read(self):
        # A negative index on a record element, and a record at index 2 of a 3-tuple:
        # both normalize to std::get<2> and arrow-decide correctly.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def f(t: tuple[Int32, Int32, Leaf]) -> Int32:\n"
            + "    return t[-1].n + t[2].n\n")
        fn = _fn(thir, "f")
        assert fn is not None
        add = fn.body[0].value
        assert isinstance(add.left, THIRFieldAccess) and add.left.is_arrow
        assert isinstance(add.left.receiver, THIRSubscript) and add.left.receiver.index == 2
        assert add.right.receiver.index == 2

    def test_readonly_tuple_record_read_routes(self):
        # A readonly[tuple[...]] receiver still reads a record element via `->` (the
        # const is carried in the param type, not the access) -- byte-identical.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def f(t: readonly[tuple[Int32, Leaf]]) -> Int32:\n    return t[1].n\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret.value, THIRFieldAccess) and ret.value.is_arrow
        assert isinstance(ret.value.receiver, THIRSubscript)

    def test_standalone_record_element_read_is_ineligible(self):
        # `b = t[1]` binds a record borrow local from a subscript -- the borrow-local
        # binding source path keeps its name-receiver gate, so this stays on AST.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def f(t: tuple[Int32, Leaf]) -> Int32:\n    b = t[1]\n    return b.n\n")
        assert _fn(thir, "f") is None


class TestTupleSubscriptRecordReadEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F3_RECORDS
        + "def via_param(t: tuple[Int32, Leaf]) -> Int32:\n    return t[1].n\n"
        + "def via_alias(h: Holder) -> Int32:\n    a = h.pair\n    return a[1].n\n"
        + "def main():\n    h = Holder(Leaf(5))\n"
        + "    print(via_param(h.pair) + via_alias(h))\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_arrow_and_dot(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "return std::get<1>(t)->n;" in cpp   # borrow param
        assert "return std::get<1>(a).n;" in cpp     # storage alias


# The read frontier's tail: an unproven `Optional[record]`-element member access
# `t[N].field` -> `deref_check(...).field`. Off a borrow tuple the element is a nullable
# `T*`; off a storage alias it is `std::optional<T>` lifted to `T*` via optional_to_ptr.
class TestTupleSubscriptOptionalRead:
    def test_optional_element_via_borrow_param_routes(self):
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def f(t: tuple[T | None, T | None]) -> Int32:\n    return t[0].x\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret.value, THIRFieldAccess) and ret.value.deref_check
        assert ret.value.is_arrow is False  # deref_check reads `.` after the checked deref
        assert isinstance(ret.value.receiver, THIRSubscript)  # already a T*, no lift

    def test_optional_element_via_storage_alias_lifts(self):
        # Off a storage auto&& alias the element is std::optional<T>, lifted to T* via a
        # STORAGE->BORROW optional_to_ptr convert before the deref_check.
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def f(h: Holder) -> Int32:\n    a = h.pair\n    return a[0].x\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[1]
        assert isinstance(ret.value, THIRFieldAccess) and ret.value.deref_check
        conv = ret.value.receiver
        assert isinstance(conv, THIRFormConvert) and conv.form is Form.BORROW
        assert isinstance(conv.value, THIRSubscript) and conv.value.form is Form.STORAGE

    def test_index_1_and_readonly_optional_route(self):
        # An Optional element at index 1, and a readonly[tuple] receiver, both route
        # (index normalization + the const deref_check/optional_to_ptr overloads are
        # shared, unmodified machinery).
        thir = _lower_ctx(
            "from tpy import readonly\n" + _F3_OPT_RECORDS
            + "def i1(t: tuple[T | None, T | None]) -> Int32:\n    return t[1].x\n"
            + "def ro(t: readonly[tuple[T | None, T | None]]) -> Int32:\n    return t[0].x\n")
        assert _fn(thir, "i1") is not None and _fn(thir, "ro") is not None
        assert _fn(thir, "i1").body[0].value.receiver.index == 1

    def test_optional_element_write_is_ineligible(self):
        # A write through an Optional-element subscript (`t[0].x = 5`) keeps the
        # name-receiver gate on the write path -> stays on the AST path.
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def f(t: tuple[T | None, T | None]) -> None:\n    t[0].x = 5\n")
        assert _fn(thir, "f") is None

    def test_optional_element_bind_is_ineligible(self):
        # A standalone bind of an Optional element (`e = t[0]`) is a borrow-local
        # binding source, which keeps the name-receiver gate -> stays on the AST path.
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def f(t: tuple[T | None, T | None]) -> Int32:\n    e = t[0]\n    return e.x\n")
        assert _fn(thir, "f") is None

    def test_dump_renders_deref_check(self):
        # The --dump-thir rendering of the runtime-null-checked member access (the one
        # test exercising dump.py's deref_check branch).
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def f(t: tuple[T | None, T | None]) -> Int32:\n    return t[0].x\n")
        assert "deref_check(%t[0] [borrow]).x" in dump_thir(thir)


class TestTupleSubscriptOptionalReadEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F3_OPT_RECORDS
        + "def viap(t: tuple[T | None, T | None]) -> Int32:\n    return t[0].x\n"
        + "def viaa(h: Holder) -> Int32:\n    a = h.pair\n    return a[0].x\n"
        + "def main():\n    h = Holder()\n    print(0)\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_deref_check(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "::tpy::deref_check(std::get<0>(t)).x" in cpp
        assert "::tpy::deref_check(::tpy::optional_to_ptr(std::get<0>(a))).x" in cpp


# The write position that closes the tuple-subscript family: a scalar field write to a
# record element, `t[N].field = <scalar>` (and `+= <scalar>`) -> `std::get<N>(t)->field
# = ...`. The target renders the same as the record-element read; only the write/aug-write
# eligibility gates are extended to the subscript target.
class TestTupleSubscriptWrite:
    def test_record_element_field_write_routes(self):
        thir = _lower_ctx(
            _F3_RECORDS
            + "def w(t: tuple[Int32, Leaf]) -> None:\n    t[1].n = 5\n")
        fn = _fn(thir, "w")
        assert fn is not None
        st = fn.body[0]
        assert isinstance(st, THIRAssign)
        assert isinstance(st.target, THIRFieldAccess) and st.target.is_arrow
        assert isinstance(st.target.receiver, THIRSubscript)

    def test_record_element_field_aug_write_routes(self):
        # `t[N].field += y` lowers to `target = (target OP value)`; the subscript target
        # renders identically on both sides.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def w(t: tuple[Int32, Leaf]) -> None:\n    t[1].n += 3\n")
        st = _fn(thir, "w").body[0]
        assert isinstance(st, THIRAssign) and isinstance(st.value, THIRBinOp)
        assert isinstance(st.target, THIRFieldAccess) and st.target.is_arrow

    def test_write_via_storage_alias_dots(self):
        thir = _lower_ctx(
            _F3_RECORDS
            + "def w(h: Holder) -> None:\n    a = h.pair\n    a[1].n = 9\n")
        st = _fn(thir, "w").body[1]
        assert isinstance(st.target, THIRFieldAccess) and st.target.is_arrow is False

    def test_optional_element_write_still_ineligible(self):
        # Extending the scalar-field-write gate to subscript targets must NOT admit an
        # Optional-element write (needs a null-checked write) -- markers reject it.
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def w(t: tuple[T | None, T | None]) -> None:\n    t[0].x = 5\n")
        assert _fn(thir, "w") is None


class TestTupleSubscriptWriteEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    # Both target forms are byte-diffed and emit-checked: the borrow-param arrow write
    # (`w`) and the storage-alias dot write (`wa`), each with a plain and a `+=` variant.
    SRC = (
        "from tpy import Int32\n"
        "class Leaf:\n    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
        "class Holder:\n    pair: tuple[Int32, Leaf]\n"
        "    def __init__(self, b: Leaf) -> None:\n        self.pair = (1, b)\n"
        "def w(t: tuple[Int32, Leaf]) -> None:\n    t[1].n = 5\n    t[1].n += 3\n"
        "def wa(h: Holder) -> None:\n    a = h.pair\n    a[1].n = 9\n    a[1].n += 2\n"
        "def main():\n    leaf = Leaf(1)\n    w((5, leaf))\n    h = Holder(leaf)\n"
        "    wa(h)\n    print(leaf.n)\n"
        "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_writes(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "std::get<1>(t)->n = 5;" in cpp                      # borrow param -> arrow
        assert ("std::get<1>(t)->n = ::tpy::add_check<int32_t>(std::get<1>(t)->n, 3);"
                in cpp)
        assert "std::get<1>(a).n = 9;" in cpp                       # storage alias -> dot
        assert ("std::get<1>(a).n = ::tpy::add_check<int32_t>(std::get<1>(a).n, 2);"
                in cpp)


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

    def test_record_param_method_routes(self):
        # M2: a non-readonly method takes an F1-record param like a free function;
        # `other.n` is a scalar field read off the record param.
        thir = _lower_ctx(
            _F1_RECORDS
            + "    def with_box(self, other: Box) -> Int32:\n        return other.n\n")
        assert _fn(thir, "with_box") is not None

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


# --- M2: record params on instance methods (readonly or not) ---

_M2_METHODS = (
    _F1_RECORDS
    # reads a scalar field off the record param -> param is const-ref (not mutated)
    + "    def sum_with(self, other: Box) -> Int32:\n        return self.n + other.n\n"
    # reads other.opt -> a borrow local off a const record param (const Inner*)
    + "    def peek_other(self, other: Box) -> Int32:\n        p = other.opt\n        return 0\n"
    # writes other.opt -> the param is mutated, so it is a mutable ref (Inner*)
    + "    def clear_other(self, other: Box):\n        p = other.opt\n        other.opt = None\n"
    # mutates b but only reads a.opt: the const verdict must key on a's param index
    + "    def mix(self, a: Box, b: Box) -> Int32:\n        b.n = 1\n        p = a.opt\n        return 0\n"
)


class TestMethodFrontierM2:
    def test_const_record_param_scalar_read_routes(self):
        assert _fn(_lower_ctx(_M2_METHODS), "sum_with") is not None

    def test_optional_to_ptr_off_const_record_param_is_const(self):
        # `other` is not mutated -> const-ref param -> the borrow off other.opt
        # lifts to `const Inner*` (the const verdict comes from the method's own
        # const_borrow_params, looked up on the owning record).
        decl = _fn(_lower_ctx(_M2_METHODS), "peek_other").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR
        assert decl.form is Form.BORROW and decl.is_const

    def test_optional_to_ptr_off_mutated_record_param_is_nonconst(self):
        # `clear_other` writes other.opt -> `other` is a mutable ref, so the
        # borrow off it is `Inner*`, not `const Inner*`.
        decl = _fn(_lower_ctx(_M2_METHODS), "clear_other").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR
        assert decl.form is Form.BORROW and not decl.is_const

    def test_readonly_method_with_record_param_routes(self):
        # An explicit @readonly method with a record param routes: for a plain
        # F1-record (ref) param the forced-const and inferred-const verdicts
        # coincide, so const_borrow_params is exact (no carve-out needed).
        thir = _lower_ctx(
            _F1_RECORDS
            + "    @readonly\n    def ro_with(self, other: Box) -> Int32:\n"
            + "        return other.n\n")
        assert _fn(thir, "ro_with") is not None

    def test_auto_readonly_method_with_record_param_routes(self):
        # A non-mutating method that reads a record param is auto-readonly; it
        # must still route (this is the common case the rung exists for).
        thir = _lower_ctx(
            _F1_RECORDS
            + "    def auto_ro(self, other: Box) -> Int32:\n        return other.n\n")
        assert _fn(thir, "auto_ro") is not None

    def test_optional_to_ptr_off_explicit_readonly_record_param_is_const(self):
        # An explicit @readonly method that lifts a borrow off a record param:
        # the param is not mutated, so the forced-const verdict and the inferred
        # const_borrow_params verdict coincide -> `const Inner*` (pins the claim
        # the eligibility comment rests on, for the explicit-readonly path).
        thir = _lower_ctx(
            _F1_RECORDS
            + "    @readonly\n    def ro_peek(self, other: Box) -> Int32:\n"
            + "        p = other.opt\n        return 0\n")
        decl = _fn(thir, "ro_peek").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR
        assert decl.form is Form.BORROW and decl.is_const

    def test_mixed_mutation_const_verdict_keys_on_param_index(self):
        # `mix` mutates b but only reads a.opt; the const verdict must key on a's
        # param index (0), not b's (1) -- exercises the index-based
        # const_borrow_params lookup that a single-param method never does.
        decl = next(s for s in _fn(_lower_ctx(_M2_METHODS), "mix").body
                    if isinstance(s, THIRVarDecl)
                    and s.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR)
        assert decl.form is Form.BORROW and decl.is_const


class TestMethodFrontierM2Emit:
    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _M2_METHODS
        + "def main():\n    a = Box(Inner(1))\n    b = Box(Inner(2))\n"
        + "    print(a.sum_with(b))\n    print(a.peek_other(b))\n    a.clear_other(b)\n"
        + "main()\n"
    )

    def test_m2_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_const_record_param_borrow_emits_const(self):
        assert ("const Inner* p = ::tpy::optional_to_ptr(other.opt);"
                in self._emit(self.SRC, thir=True))


# --- Readonly free functions: a `@readonly` free function is admitted (its
# record params are forced const, which coincides with the inferred verdict) ---

_RO_FREE = (
    _F1_RECORDS
    + "@readonly\ndef width(b: Box) -> Int32:\n    return b.n\n"
    + "@readonly\ndef peek_free(b: Box) -> Int32:\n    p = b.opt\n    return 0\n"
)


class TestReadonlyFreeFunction:
    def test_readonly_free_function_routes(self):
        assert _fn(_lower_ctx(_RO_FREE), "width") is not None

    def test_optional_to_ptr_off_readonly_free_param_is_const(self):
        # A readonly free function forces its record param const; that coincides
        # with the inferred const_borrow_params verdict (param not mutated), so the
        # borrow off b.opt lifts to `const Inner*`.
        decl = _fn(_lower_ctx(_RO_FREE), "peek_free").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR
        assert decl.form is Form.BORROW and decl.is_const


class TestReadonlyFreeFunctionEmit:
    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _RO_FREE
        + "def main():\n    b = Box(Inner(5))\n    print(width(b))\n    print(peek_free(b))\n"
        + "main()\n"
    )

    def test_readonly_free_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_readonly_free_param_borrow_emits_const(self):
        assert ("const Inner* p = ::tpy::optional_to_ptr(b.opt);"
                in self._emit(self.SRC, thir=True))


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


# --- Augmented assignment: `x += y` / `recv.field += y` (scalar) ---

_AUG = (
    "from tpy import Int32\n"
    "class Counter:\n    count: Int32\n"
    "    def __init__(self, count: Int32):\n        self.count = count\n"
    "    def tick(self, n: Int32):\n        self.count += n\n"
    # local aug-assign + a field aug-assign off a record param (non-self)
    "def bump(c: Counter, n: Int32) -> Int32:\n"
    "    n += 1\n    c.count += n\n    return n\n"
)


class TestScalarAugAssign:
    def test_local_aug_assign_routes_as_binop(self):
        st = _fn(_lower_ctx(_AUG), "bump").body[0]
        assert isinstance(st, THIRAssign) and isinstance(st.target, THIRName)
        assert isinstance(st.value, THIRBinOp) and st.value.op == "+"
        # the binop's left operand re-reads the target (the AST likewise
        # substitutes the target string into both the lvalue and the binop).
        assert isinstance(st.value.left, THIRName) and st.value.left.name == "n"

    def test_field_aug_assign_off_param(self):
        st = _fn(_lower_ctx(_AUG), "bump").body[1]
        assert isinstance(st, THIRAssign) and isinstance(st.target, THIRFieldAccess)
        assert isinstance(st.value, THIRBinOp)
        assert isinstance(st.value.left, THIRFieldAccess)

    def test_field_aug_assign_off_self(self):
        st = _fn(_lower_ctx(_AUG), "tick").body[0]
        assert isinstance(st, THIRAssign) and isinstance(st.target, THIRFieldAccess)
        assert st.target.is_arrow
        assert isinstance(st.value, THIRBinOp)

    def test_inplace_dunder_excluded(self):
        # `xs += [v]` resolves to list_extend (__iadd__) -- mutates in place via a
        # method call, not the binop substitution -> AST path.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def grow(v: Int32):\n    xs = [1]\n    xs += [v]\n")
        assert _fn(thir, "grow") is None

    def test_str_aug_assign_excluded(self):
        # `s += t` on a str takes the in-place-append optimization; a str target is
        # not an eligible scalar, so the body stays on the AST path.
        thir = _lower_ctx(
            "def cat(t: str):\n    s = 'a'\n    s += t\n")
        assert _fn(thir, "cat") is None

    def test_subscript_aug_assign_excluded(self):
        # A subscript target takes the set_value/get_value path -> AST.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def at(xs: list[Int32], i: Int32):\n    xs[i] += 1\n")
        assert _fn(thir, "at") is None

    def test_float_local_aug_assign_routes(self):
        # A double `float` is an eligible scalar -- the value-scalar slice is not
        # int-only.
        st = _fn(_lower(
            "def f(a: float) -> float:\n    a += 1.0\n    return a\n"), "f").body[0]
        assert isinstance(st, THIRAssign) and isinstance(st.value, THIRBinOp)


class TestScalarAugAssignEmit:
    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _AUG
        + "def main():\n    c = Counter(3)\n    c.tick(2)\n    print(bump(c, 4))\n"
        + "    print(c.count)\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_self_field_aug_emits_arrow(self):
        # No outer parens (the aug-assign RHS is a full statement RHS).
        assert ("this->count = ::tpy::add_check<int32_t>(this->count, n);"
                in self._emit(self.SRC, thir=True))

    def test_floordiv_aug_not_swapped(self):
        # `q //= d` with a non-proven-zero divisor must emit the checked helper,
        # not div_floor -- the AST aug-assign path never swaps it.
        src = (
            "from tpy import Int32\n"
            "def f(q: Int32, d: Int32) -> Int32:\n    q //= d\n    return q\n"
            "def main():\n    print(f(10, 3))\nmain()\n")
        out = self._emit(src, thir=True)
        assert "div_floor" not in out
        assert out == self._emit(src, thir=False)

    def test_other_ops_byte_identical(self):
        # -= *= %= alongside the += / //= already covered.
        src = (
            "from tpy import Int32\n"
            "def f(a: Int32, b: Int32) -> Int32:\n"
            "    a -= b\n    a *= b\n    a %= b\n    return a\n"
            "def main():\n    print(f(20, 3))\nmain()\n")
        assert self._emit(src, thir=True) == self._emit(src, thir=False)

    def test_float_aug_byte_identical(self):
        src = (
            "def g(a: float, b: float) -> float:\n    a += b\n    a *= b\n    return a\n"
            "def main():\n    print(g(1.5, 2.0))\nmain()\n")
        assert self._emit(src, thir=True) == self._emit(src, thir=False)


class TestConstructor:
    """The M3a ctor frontier: pure-MIL scalar constructors of flat records --
    every `__init__` statement is a hoistable own-scalar field init, so the
    member-init-list is the whole body and the C++ body is `{}`."""

    _POINT = (
        _PRELUDE
        + "class Point:\n    x: Int32\n    y: Int32\n"
        + "    def __init__(self, x: Int32, y: Int32):\n"
        + "        self.x = x\n        self.y = y\n")

    def test_pure_scalar_ctor_routes(self):
        ctor = _lower_ctor(self._POINT, "Point")
        assert ctor is not None
        assert [mi.field_cpp for mi in ctor.mil_inits] == ["x", "y"]
        assert ctor.body == ()  # pure-MIL: empty body

    def test_pure_scalar_ctor_tail_emit(self):
        ctor = _lower_ctor(self._POINT, "Point")
        assert _ctor_tail(ctor) == " : x(x), y(y) {}\n"

    def test_no_param_literal_inits_route(self):
        ctor = _lower_ctor(
            _PRELUDE
            + "class Counter:\n    n: Int32\n    step: Int32\n"
            + "    def __init__(self):\n        self.n = 0\n        self.step = 1\n",
            "Counter")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : n(0), step(1) {}\n"

    def test_field_init_from_sibling_field_routes(self):
        # An RHS reading a sibling scalar field renders `b(this->a)` (the corpus
        # byte-diff validates this against the AST path).
        ctor = _lower_ctor(
            _PRELUDE
            + "class C:\n    a: Int32\n    b: Int32\n"
            + "    def __init__(self, a: Int32):\n"
            + "        self.a = a\n        self.b = self.a\n",
            "C")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : a(a), b(this->a) {}\n"

    def test_pass_body_ctor_routes(self):
        # M3c-trivia: `pass` is non-init trivia -- it stays in the body (so the
        # braces are ` {\n    }`, not ` {}`) but breaks no chain, so the field init
        # still hoists. It emits no code; its `loc` carries the `// pass` comment.
        ctor = _lower_ctor(
            _PRELUDE
            + "class P:\n    x: Int32\n"
            + "    def __init__(self, x: Int32):\n        self.x = x\n        pass\n",
            "P")
        assert ctor is not None
        assert [mi.field_cpp for mi in ctor.mil_inits] == ["x"]
        assert len(ctor.body) == 1
        assert _ctor_tail(ctor) == " : x(x) {\n    }\n"

    def test_docstring_ctor_routes(self):
        # M3c-trivia: a docstring is non-init trivia too -- same body-brace effect,
        # chain intact. It emits neither code nor comment (loc=None).
        ctor = _lower_ctor(
            _PRELUDE
            + "class P:\n    x: Int32\n"
            + "    def __init__(self, x: Int32):\n"
            + '        """doc"""\n        self.x = x\n',
            "P")
        assert ctor is not None
        assert [mi.field_cpp for mi in ctor.mil_inits] == ["x"]
        assert _ctor_tail(ctor) == " : x(x) {\n    }\n"

    def test_trivia_only_ctor_routes(self):
        # A ctor whose body is only trivia (no field inits) -- the body is
        # non-empty but emits nothing, so ` {\n    }` with no init list.
        ctor = _lower_ctor(
            _PRELUDE
            + "class E:\n    def __init__(self) -> None:\n        pass\n",
            "E")
        assert ctor is not None
        assert ctor.mil_inits == ()
        assert _ctor_tail(ctor) == " {\n    }\n"

    def test_trivia_comment_loc_asymmetry(self):
        # The byte-identity hinge: `pass` keeps its source loc (the AST emits its
        # `// pass` source comment), a docstring lowers with loc=None (the AST emits
        # NO comment for a docstring -- its simple-stmt code is None).
        pass_ctor = _lower_ctor(
            _PRELUDE
            + "class P:\n    x: Int32\n"
            + "    def __init__(self, x: Int32):\n        self.x = x\n        pass\n",
            "P")
        assert pass_ctor.body[0].loc is not None
        doc_ctor = _lower_ctor(
            _PRELUDE
            + "class P:\n    x: Int32\n"
            + "    def __init__(self, x: Int32):\n"
            + '        """doc"""\n        self.x = x\n',
            "P")
        assert doc_ctor.body[0].loc is None

    def test_non_init_call_body_is_ineligible(self):
        # A real non-init statement (not trivia) needs the M3c-demotion rung; the
        # ctor stays on the AST path.
        ctor = _lower_ctor(
            _PRELUDE
            + "class P:\n    x: Int32\n"
            + "    def __init__(self, x: Int32):\n"
            + "        self.x = x\n        print(x)\n",
            "P")
        assert ctor is None

    def test_non_init_body_statement_is_ineligible(self):
        # A non-field-init statement (here a call) means the ctor needs the body
        # rung (M3c); the whole ctor stays on the AST path.
        ctor = _lower_ctor(
            _PRELUDE
            + "class C:\n    x: Int32\n"
            + "    def __init__(self, x: Int32):\n"
            + "        self.x = x\n        print(x)\n",
            "C")
        assert ctor is None

    def test_body_local_demotion_routes(self):
        # M3c-demotion: a field init whose RHS reads a body-local can't hoist (the
        # local isn't in scope at MIL time), so it demotes into the body alongside
        # the local's var-decl. No MIL; the body holds both statements.
        ctor = _lower_ctor(
            _PRELUDE
            + "class W:\n    size: Int32\n"
            + "    def __init__(self, w: Int32, h: Int32):\n"
            + "        area = w * h\n        self.size = area\n",
            "W")
        assert ctor is not None
        assert ctor.mil_inits == ()
        assert len(ctor.body) == 2  # the var-decl + the demoted field write
        assert _ctor_tail(ctor) == (
            " {\n        int32_t area = (::tpy::mul_check<int32_t>(w, h));\n"
            "        this->size = area;\n    }\n")

    def test_chain_break_demotes_hoistable_init(self):
        # M3c-demotion: a non-init statement breaks the hoist chain, so a *hoistable*
        # field init after it must demote (the MIL runs before the body -- hoisting
        # would reorder it past the chain-breaking statement). `self.a` (before the
        # break) hoists; `self.b` (after) demotes.
        ctor = _lower_ctor(
            _PRELUDE
            + "def helper(x: Int32) -> Int32:\n    return x\n"
            + "class W:\n    a: Int32\n    b: Int32\n"
            + "    def __init__(self, x: Int32, y: Int32):\n"
            + "        self.a = x\n        z = helper(y)\n        self.b = z\n",
            "W")
        assert ctor is not None
        assert [mi.field_cpp for mi in ctor.mil_inits] == ["a"]
        assert _ctor_tail(ctor) == (
            " : a(x) {\n        int32_t z = helper(y);\n"
            "        this->b = z;\n    }\n")

    def test_demotion_byte_identical(self):
        # End-to-end byte-identity for the demotion shapes (body-local demote +
        # chain-break demote of a hoistable init) through the THIR seam vs the AST.
        src = (
            _PRELUDE
            + "def helper(x: Int32) -> Int32:\n    return x\n"
            + "class W:\n    a: Int32\n    b: Int32\n    c: Int32\n"
            + "    def __init__(self, x: Int32, y: Int32):\n"
            + "        self.a = x\n        t = helper(y)\n"
            + "        self.b = t\n        self.c = x\n"
            + "def main():\n    w = W(1, 2)\n    print(w.a + w.b + w.c)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_demotion_cascade_byte_identical(self):
        # A demoted init breaks the chain, so a subsequent otherwise-hoistable init
        # also demotes (cascade). All three end up in the body in source order.
        src = (
            _PRELUDE
            + "class W:\n    a: Int32\n    b: Int32\n"
            + "    def __init__(self, x: Int32):\n"
            + "        n = x + 1\n        self.a = n\n        self.b = x\n"
            + "def main():\n    w = W(5)\n    print(w.a + w.b)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_record_field_demotion_is_ineligible(self):
        # A demoted *record*-field write is not a body-eligible statement
        # (`_stmt_eligible` admits only scalar / Optional field writes), so the ctor
        # stays on the AST path. Byte-safe; bounds the M3c-demotion slice.
        ctor = _lower_ctor(
            self._INNER
            + "class W:\n    rec: Inner\n"
            + "    def __init__(self, v: Int32):\n"
            + "        m = Inner(v)\n        self.rec = m\n",
            "W")
        assert ctor is None

    def test_single_base_super_init_routes(self):
        # M3d-1: a single-F1-base ctor routes -- `super().__init__(a)` lowers to a
        # `Base(a)` base initializer prepended to the MIL; own fields hoist as usual.
        src = (
            _PRELUDE
            + "class Base:\n    a: Int32\n"
            + "    def __init__(self, a: Int32):\n        self.a = a\n"
            + "class Derived(Base):\n    b: Int32\n"
            + "    def __init__(self, a: Int32, b: Int32):\n"
            + "        super().__init__(a)\n        self.b = b\n")
        ctor = _lower_ctor(src, "Derived")
        assert ctor is not None
        assert [bi.base_cpp for bi in ctor.base_inits] == ["Base"]
        assert [mi.field_cpp for mi in ctor.mil_inits] == ["b"]
        assert _ctor_tail(ctor) == " : Base(a), b(b) {}\n"
        assert _lower_ctor(src, "Base") is not None  # the flat base routes too

    def test_inherited_field_write_routes(self):
        # M3d: a direct inherited-field write (`self.a = ...`, `a` owned by the base)
        # goes to the BODY (the base ctor owns the MIL slot), without breaking the hoist
        # chain -- so the own field `b` still hoists. `this->a = a;` lands in the body.
        src = (
            _PRELUDE
            + "class Base:\n    a: Int32\n"
            + "    def __init__(self, a: Int32):\n        self.a = a\n"
            + "class Derived(Base):\n    b: Int32\n"
            + "    def __init__(self, a: Int32, b: Int32):\n"
            + "        super().__init__(a)\n        self.a = a\n        self.b = b\n")
        ctor = _lower_ctor(src, "Derived")
        assert ctor is not None
        assert [bi.base_cpp for bi in ctor.base_inits] == ["Base"]
        assert [mi.field_cpp for mi in ctor.mil_inits] == ["b"]  # own field hoists
        assert _ctor_tail(ctor) == " : Base(a), b(b) {\n        this->a = a;\n    }\n"

    def test_init_reading_inherited_field_demotes(self):
        # An own-field init reading an inherited field written earlier in the body must
        # demote (the MIL runs before that write) -- the expr_reads_self_field trigger.
        src = (
            _PRELUDE
            + "class Base:\n    a: Int32\n"
            + "    def __init__(self, a: Int32):\n        self.a = a\n"
            + "class Derived(Base):\n    b: Int32\n"
            + "    def __init__(self, a: Int32):\n"
            + "        super().__init__(a)\n        self.a = a\n        self.b = self.a\n")
        ctor = _lower_ctor(src, "Derived")
        assert ctor is not None
        assert ctor.mil_inits == ()  # b demotes (reads self.a written in the body)
        assert _ctor_tail(ctor) == (
            " : Base(a) {\n        this->a = a;\n        this->b = this->a;\n    }\n")

    def test_multi_base_routes(self):
        # M3d: multiple bases route -- each explicit `BaseN.__init__(self, ...)` lowers
        # to a base initializer, sorted by parent declaration order (A before B).
        src = (
            _PRELUDE
            + "class A:\n    x: Int32\n    def __init__(self, x: Int32):\n        self.x = x\n"
            + "class B:\n    y: Int32\n    def __init__(self, y: Int32):\n        self.y = y\n"
            + "class C(A, B):\n    z: Int32\n"
            + "    def __init__(self, x: Int32, y: Int32, z: Int32):\n"
            + "        A.__init__(self, x)\n        B.__init__(self, y)\n        self.z = z\n")
        ctor = _lower_ctor(src, "C")
        assert ctor is not None
        assert [bi.base_cpp for bi in ctor.base_inits] == ["A", "B"]
        assert _ctor_tail(ctor) == " : A(x), B(y), z(z) {}\n"

    def test_multi_base_byte_identical(self):
        src = (
            _PRELUDE
            + "class A:\n    x: Int32\n    def __init__(self, x: Int32):\n        self.x = x\n"
            + "class B:\n    y: Int32\n    def __init__(self, y: Int32):\n        self.y = y\n"
            + "class C(A, B):\n    z: Int32\n"
            + "    def __init__(self, x: Int32, y: Int32, z: Int32):\n"
            + "        A.__init__(self, x)\n        B.__init__(self, y)\n        self.z = z\n"
            + "def main():\n    c = C(1, 2, 3)\n    print(c.x + c.y + c.z)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_single_base_byte_identical(self):
        # End-to-end byte-identity for the single-base super-init ctor through the
        # THIR seam vs the AST path (the derived signature is AST-emitted; only the
        # base-init + field MIL tail routes).
        src = (
            _PRELUDE
            + "class Base:\n    a: Int32\n"
            + "    def __init__(self, a: Int32):\n        self.a = a\n"
            + "class Derived(Base):\n    b: Int32\n"
            + "    def __init__(self, a: Int32, b: Int32):\n"
            + "        super().__init__(a)\n        self.b = b\n"
            + "def main():\n    d = Derived(1, 2)\n    print(d.a + d.b)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_non_f1_base_is_ineligible(self):
        # A non-F1 base (here generic) keeps the derived ctor on the AST path -- its
        # `to_cpp()` would not match the bare render. Guards the `_f1_record(parent)`
        # gate (no corpus byte-diff covers it -- the routed ctors all have F1 bases).
        src = (
            _PRELUDE
            + "class Box[T]:\n    v: T\n    def __init__(self, v: T):\n        self.v = v\n"
            + "class IntBox(Box[Int32]):\n    n: Int32\n"
            + "    def __init__(self, v: Int32, n: Int32):\n"
            + "        super().__init__(v)\n        self.n = n\n")
        assert _lower_ctor(src, "IntBox") is None

    def test_field_read_optional_byte_identical(self):
        # A param field-read into an Optional[record] field (`self.opt = b.inner`)
        # constructs the optional directly -- the TpyFieldAccess arm of
        # `_is_record_value_source` flowing into the Optional `else` (no ptr_to_optional).
        src = (
            self._INNER
            + "class Box:\n    inner: Inner\n"
            + "    def __init__(self, inner: Inner):\n        self.inner = inner\n"
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, b: Box):\n        self.opt = b.inner\n"
            + "def main():\n    h = H(Box(Inner(5)))\n    print(0)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_optional_own_param_sibling_byte_identical(self):
        # The `Optional[Own[Inner]]` own-optional peel shape emits identically end-to-end
        # (the sibling `Own[Inner | None]` is `test_own_optional_param_byte_identical`).
        src = (
            "from typing import Optional\n" + self._OWN_INNER
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, m: Optional[Own[Inner]]):\n        self.opt = m\n"
            + "def main():\n    h = H(Inner(4))\n    print(0)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_inherited_field_write_byte_identical(self):
        # End-to-end byte-identity for the M3d inherited-field-write body branch and
        # the demote-reads-inherited-field path through the THIR seam vs the AST.
        src = (
            _PRELUDE
            + "class Base:\n    a: Int32\n"
            + "    def __init__(self, a: Int32):\n        self.a = a\n"
            + "class Derived(Base):\n    b: Int32\n"
            + "    def __init__(self, a: Int32):\n"
            + "        super().__init__(a)\n        self.a = a\n        self.b = self.a\n"
            + "def main():\n    d = Derived(7)\n    print(d.a + d.b)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_non_scalar_field_is_ineligible(self):
        # A str field is M3b+ form work, not M3a scalar.
        ctor = _lower_ctor(
            _PRELUDE
            + "class S:\n    name: str\n"
            + "    def __init__(self, name: str):\n        self.name = name\n",
            "S")
        assert ctor is None

    def test_bigint_field_is_ineligible(self):
        # Bare `int` -> BigInt is outside the eligible-scalar set (as everywhere in
        # the THIR slice), so a BigInt-field ctor stays on the AST path.
        ctor = _lower_ctor(
            "class C:\n    n: int\n"
            + "    def __init__(self, n: int):\n        self.n = n\n",
            "C")
        assert ctor is None

    def test_ineligible_param_with_scalar_fields_is_ineligible(self):
        # The PARAM gate must reject a ctor whose fields are all scalar but a param
        # is non-scalar: it would otherwise emit `: n(n) {}` byte-identically, so the
        # corpus byte-diff cannot guard a regression here -- only this unit test can.
        ctor = _lower_ctor(
            _PRELUDE
            + "class C:\n    n: Int32\n"
            + "    def __init__(self, n: Int32, xs: list[Int32]):\n"
            + "        self.n = n\n",
            "C")
        assert ctor is None

    def _hpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, _ = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp

    def test_ctor_byte_identical(self):
        # End-to-end: the ctor MIL tail emits identically through the THIR seam
        # (generator -> records -> emit) and the AST path. The ctor lives in the
        # .hpp (inline in the struct), so compare that half.
        src = (
            _PRELUDE
            + "class Point:\n    x: Int32\n    y: Int32\n"
            + "    def __init__(self, x: Int32, y: Int32):\n"
            + "        self.x = x\n        self.y = y\n"
            + "def main():\n    p = Point(1, 2)\n    print(p.x + p.y)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    # --- M3b: record / Optional[record] member-init-list fields ---

    _INNER = (
        "from tpy import Int32\n"
        "class Inner:\n    v: Int32\n"
        "    def __init__(self, v: Int32):\n        self.v = v\n")

    def test_optional_field_from_optional_param_routes(self):
        # The M3 cell: an Optional[record] field <- Optional[record] borrow param
        # lifts via ptr_to_optional (the F2b conversion, now in MIL position).
        ctor = _lower_ctor(
            self._INNER
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, m: Inner | None):\n        self.opt = m\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : opt(::tpy::ptr_to_optional(m)) {}\n"

    def test_optional_field_none_routes(self):
        ctor = _lower_ctor(
            self._INNER
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self):\n        self.opt = None\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : opt(std::nullopt) {}\n"

    def test_record_field_copy_routes(self):
        # A plain record field <- non-own record param: an implicit MIL copy.
        ctor = _lower_ctor(
            self._INNER
            + "class H:\n    rec: Inner\n"
            + "    def __init__(self, p: Inner):\n        self.rec = p\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : rec(p) {}\n"

    def test_record_field_explicit_copy_unwraps(self):
        # `copy(p)` is the explicit field-copy acknowledgment; it unwraps to the same
        # `rec(p)` direct-init as the bare `self.rec = p` (the MIL copies implicitly).
        ctor = _lower_ctor(
            "from tpy import Int32, copy\n"
            + "class Inner:\n    v: Int32\n"
            + "    def __init__(self, v: Int32):\n        self.v = v\n"
            + "class H:\n    rec: Inner\n"
            + "    def __init__(self, p: Inner):\n        self.rec = copy(p)\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : rec(p) {}\n"

    _OWN_INNER = (
        "from tpy import Int32, Own\n"
        "class Inner:\n    v: Int32\n"
        "    def __init__(self, v: Int32):\n        self.v = v\n")

    def test_own_record_param_record_field_moves(self):
        # M3b-move: an Own[record] param at last use moves into a record field
        # (the common ownership-taking ctor) -- `rec(std::move(p))`.
        ctor = _lower_ctor(
            self._OWN_INNER
            + "class H:\n    rec: Inner\n"
            + "    def __init__(self, p: Own[Inner]):\n        self.rec = p\n",
            "H")
        assert ctor is not None
        assert ctor.mil_inits[0].move
        assert _ctor_tail(ctor) == " : rec(std::move(p)) {}\n"

    def test_own_record_param_optional_field_moves(self):
        # M3b-move: an Own[record] param moves into an Optional[record] field --
        # `opt(std::move(p))`, NOT ptr_to_optional (an own source skips that arm).
        ctor = _lower_ctor(
            self._OWN_INNER
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, p: Own[Inner]):\n        self.opt = p\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : opt(std::move(p)) {}\n"

    def test_own_optional_param_moves(self):
        # M3b-rvalue: an own-optional param (`Own[Inner | None]`) moves into an
        # Optional[record] field via the move arm -- `opt(std::move(m))`, NOT
        # ptr_to_optional (the own source skips that arm, as for a plain Own param).
        ctor = _lower_ctor(
            self._OWN_INNER
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, m: Own[Inner | None]):\n        self.opt = m\n",
            "H")
        assert ctor is not None
        assert ctor.mil_inits[0].move
        assert _ctor_tail(ctor) == " : opt(std::move(m)) {}\n"

    def test_optional_own_param_moves(self):
        # The sibling own-optional shape `Optional[Own[Inner]]` peels differently but
        # emits the same move MIL.
        ctor = _lower_ctor(
            "from typing import Optional\n" + self._OWN_INNER
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, m: Optional[Own[Inner]]):\n        self.opt = m\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : opt(std::move(m)) {}\n"

    def test_ctor_call_record_source_routes(self):
        # M3b-rvalue: an rvalue ctor-call source (`self.rec = Inner(v)`) constructs the
        # record field directly from the prvalue -- `rec(Inner(v))`.
        ctor = _lower_ctor(
            self._INNER
            + "class H:\n    rec: Inner\n"
            + "    def __init__(self, v: Int32):\n        self.rec = Inner(v)\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : rec(Inner(v)) {}\n"

    def test_ctor_call_optional_source_routes(self):
        # M3b-rvalue: an rvalue ctor-call into an Optional[record] field constructs the
        # optional directly from the prvalue -- `opt(Inner(v))`, NOT ptr_to_optional.
        ctor = _lower_ctor(
            self._INNER
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, v: Int32):\n        self.opt = Inner(v)\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : opt(Inner(v)) {}\n"

    def test_record_field_from_param_field_read_routes(self):
        # M3b-rvalue: a field-read off a param record (`self.rec = b.inner`) copies the
        # field into the record member -- `rec(b.inner)`.
        ctor = _lower_ctor(
            self._INNER
            + "class Box:\n    inner: Inner\n"
            + "    def __init__(self, inner: Inner):\n        self.inner = inner\n"
            + "class H:\n    rec: Inner\n"
            + "    def __init__(self, b: Box):\n        self.rec = b.inner\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : rec(b.inner) {}\n"

    def test_self_field_record_read_source_is_ineligible(self):
        # A `self.<record field>` read source is ordering-sensitive in the MIL (the
        # pointee may be uninitialized) -- excluded; the ctor falls to the AST path.
        ctor = _lower_ctor(
            self._INNER
            + "class H:\n    a: Inner\n    b: Inner\n"
            + "    def __init__(self, p: Inner):\n"
            + "        self.a = p\n        self.b = self.a\n",
            "H")
        assert ctor is None

    def test_optional_field_byte_identical(self):
        # End-to-end byte-identity for the ptr_to_optional + None MIL cases, mixed
        # with a scalar field, through the THIR seam vs the AST path.
        src = (
            self._INNER
            + "class H:\n    n: Int32\n    opt: Inner | None\n"
            + "    def __init__(self, n: Int32, m: Inner | None):\n"
            + "        self.n = n\n        self.opt = m\n"
            + "def main():\n    h = H(5, None)\n    print(h.n)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_own_param_move_byte_identical(self):
        # The move arm's load-bearing contract: the own-param std::move MIL (into
        # both a record field and an Optional field) emits identically through THIR
        # and the AST path.
        src = (
            "from tpy import Int32, Own\n"
            + "class Inner:\n    v: Int32\n"
            + "    def __init__(self, v: Int32):\n        self.v = v\n"
            + "class H:\n    inner: Inner\n    opt: Inner | None\n"
            + "    def __init__(self, a: Own[Inner], b: Own[Inner]):\n"
            + "        self.inner = a\n        self.opt = b\n"
            + "def main():\n    h = H(Inner(1), Inner(2))\n    print(h.inner.v)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_rvalue_source_byte_identical(self):
        # End-to-end byte-identity for the M3b-rvalue shapes: a ctor-call source into
        # a record field and into an Optional field, and a param field-read into a
        # record field, all through the THIR seam vs the AST path.
        src = (
            self._INNER
            + "class Box:\n    inner: Inner\n"
            + "    def __init__(self, inner: Inner):\n        self.inner = inner\n"
            + "class H:\n    rec: Inner\n    opt: Inner | None\n    cp: Inner\n"
            + "    def __init__(self, v: Int32, b: Box):\n"
            + "        self.rec = Inner(v)\n        self.opt = Inner(v)\n"
            + "        self.cp = b.inner\n"
            + "def main():\n    h = H(7, Box(Inner(3)))\n    print(h.rec.v)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_own_optional_param_byte_identical(self):
        # An own-optional param (`Own[Inner | None]`) moving into an Optional field
        # emits identically through THIR and the AST path.
        src = (
            self._OWN_INNER
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, m: Own[Inner | None]):\n        self.opt = m\n"
            + "def main():\n    h = H(Inner(4))\n    print(0)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_trivia_body_byte_identical(self):
        # M3c-trivia: docstring + pass non-init bodies emit the ` {\n    }` braces
        # identically through THIR and the AST path (the byte-diff with source
        # comments ON further validates the pass/docstring comment asymmetry).
        src = (
            _PRELUDE
            + "class P:\n    x: Int32\n    y: Int32\n"
            + "    def __init__(self, x: Int32, y: Int32):\n"
            + '        """A point."""\n        self.x = x\n        self.y = y\n        pass\n'
            + "def main():\n    p = P(1, 2)\n    print(p.x + p.y)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_optional_copy_source_routes(self):
        # M3b-rvalue: `copy()` is unwrapped before the Optional check (matching the
        # record arm, `test_record_field_explicit_copy_unwraps`), so a copy()-wrapped
        # pointer-repr Optional borrow source lifts via ptr_to_optional just like the
        # bare `self.opt = m` -- `opt(::tpy::ptr_to_optional(m))`.
        ctor = _lower_ctor(
            "from tpy import Int32, copy\n"
            + "class Inner:\n    v: Int32\n"
            + "    def __init__(self, v: Int32):\n        self.v = v\n"
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, m: Inner | None):\n        self.opt = copy(m)\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : opt(::tpy::ptr_to_optional(m)) {}\n"
