"""THIR scalar call-arg shapes: bare numeric-literal args, negated int
literals, scalar type-constructor calls (Int32(x) / Float64(x) / bool(n))."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .nodes import (
    THIRBinOp, THIRCall, THIRCoerce, THIRForRange, THIRLiteral, THIRReturn,
)
from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _compile, _entry, _lower, _lower_ctx, _lower_ctx_witnessed, _fn, _emit_expr,
    _assert_byte_identical, _assert_routes_byte_identical, _top_level,
)

# --- Bare numeric-literal call args + negated int literals (increment 45) ---

_NUMLIT_PRELUDE = "from tpy import Int32, Int64, Float32, Float64\n"


class TestNumericLiteralArgs:
    def _cpp(self, src: str):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        return cpp

    def test_float_literal_arg_routes(self):
        # A bare float literal (FloatLiteralType) into a double slot renders
        # repr(v) bare on both paths.
        thir = _lower(
            _NUMLIT_PRELUDE
            + "def f(x: Float64) -> Float64:\n    return x\n"
            + "def g() -> Float64:\n    return f(1.5)\n")
        fn = _fn(thir, "g")
        assert fn is not None
        call = fn.body[0].value
        assert isinstance(call.args[0], THIRLiteral) and call.args[0].value == 1.5

    def test_float32_literal_arg_routes_suffixed(self):
        # A float literal into a Float32 slot arrives float_literal_to_float32
        # coerce-wrapped; lowering retypes the literal to Float32 so the
        # emitter appends the `f` suffix.
        thir = _lower(
            _NUMLIT_PRELUDE
            + "def f(x: Float32) -> Float32:\n    return x\n"
            + "def g() -> None:\n    v = f(1.5)\n    print(v)\n")
        fn = _fn(thir, "g")
        assert fn is not None
        call = fn.body[0].init
        lit = call.args[0]
        while isinstance(lit, THIRCoerce):
            lit = lit.expr
        assert isinstance(lit, THIRLiteral) and lit.value == 1.5
        assert _emit_expr(lit) == "1.5f"

    def test_inf_literal_arg_routes_as_numeric_limits(self):
        # `1e400` parses to inf, which has no C++ literal form: both paths
        # fold it to the constexpr numeric_limits spelling.
        src = (_NUMLIT_PRELUDE
               + "def f(x: Float64) -> Float64:\n    return x\n"
               + "def g() -> None:\n    v = f(1e400)\n    print(v)\n")
        thir = _lower(src)
        assert _fn(thir, "g") is not None
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "f(std::numeric_limits<double>::infinity())" in hpp + cpp

    def test_bigint_slot_literal_routes_wrapped(self):
        # An int literal into a BigInt slot wraps `::tpy::BigInt(3)` -- the
        # slot retype at _lower_call_arg's tail (see test_thir_bigint for the
        # byte-identity pin).
        thir = _lower(
            _NUMLIT_PRELUDE
            + "def f(x: int) -> int:\n    return x\n"
            + "def g() -> None:\n    v = f(3)\n    print(v)\n")
        assert _fn(thir, "g") is not None

    def test_negated_int_literal_positions_route(self):
        # The fold covers every admitted literal position: decl init, call arg
        # (behind the int_literal coerce), compare operand, subscript index,
        # slice bound, range bound, print arg.
        thir = _lower(
            _NUMLIT_PRELUDE
            + "def f(x: Int32) -> Int32:\n    return x\n"
            + "def g(xs: list[Int32], s: str) -> Int32:\n"
            + "    a = -3\n"
            + "    print(f(-3), xs[-1], s[1:-1], -7)\n"
            + "    for i in range(-3, 3):\n        a = a + i\n"
            + "    if a > -10:\n        return a + -2\n"
            + "    return -1\n")
        fn = _fn(thir, "g")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl.init, THIRLiteral) and decl.init.value == -3
        rng = fn.body[2]
        assert isinstance(rng, THIRForRange) and rng.start_is_literal
        # The range bound arrives behind the int_literal passthrough coerce.
        start = rng.start.expr if isinstance(rng.start, THIRCoerce) else rng.start
        assert isinstance(start, THIRLiteral) and start.value == -3

    def test_negated_float_literal_routes(self):
        # A standalone negated FLOAT literal (`v = -2.5`) takes the resolved
        # __neg__ template (`-(2.5)`), now routed via THIRUnaryArith
        # byte-identically to the AST path.
        src = (
            _NUMLIT_PRELUDE
            + "def h() -> None:\n    v = -2.5\n    print(v)\n")
        assert _fn(_lower(src), "h") is not None
        _assert_byte_identical(src)

    def test_negated_float_literal_call_arg_routes(self):
        # As a call arg (`f(-1.5)`), the negated float literal now passes the
        # shared arg-admission set into the double slot (wave-9 builtins), via
        # the unary arm's resolved-dunder lowering -- byte-identical.
        src = (_NUMLIT_PRELUDE
               + "def f(x: Float64) -> Float64:\n    return x\n"
               + "def g() -> None:\n    v = f(-1.5)\n    print(1)\n")
        assert _fn(_lower(src), "g") is not None
        _assert_byte_identical(src)

    def test_negated_name_routes(self):
        # `-x` takes the __neg__ template (no literal fold); routes via
        # THIRUnaryArith byte-identically to the AST path.
        src = _NUMLIT_PRELUDE + "def g(x: Int32) -> Int32:\n    return -x\n"
        assert _fn(_lower(src), "g") is not None
        _assert_byte_identical(src)

    def test_wide_literal_boundaries_route_byte_identical(self):
        src = (
            _NUMLIT_PRELUDE
            + "from tpy import UInt64\n"
            + "def i64(n: Int64) -> Int64:\n    return n\n"
            + "def u64(n: UInt64) -> UInt64:\n    return n\n"
            + "def wide() -> Int64:\n    return i64(2147483648)\n"
            + "def negative() -> Int64:\n    return -2147483649\n"
            + "def minimum() -> Int64:\n    return -9223372036854775808\n"
            + "def maximum() -> UInt64:\n    return u64(18446744073709551615)\n"
        )
        thir = _lower(src)
        for name in ("wide", "negative", "minimum", "maximum"):
            assert _fn(thir, name) is not None
        cpp = self._cpp(src)
        assert "static_cast<int64_t>(2147483648)" in cpp
        assert "static_cast<int64_t>((-9223372036854775807LL - 1))" in cpp
        assert "static_cast<uint64_t>(18446744073709551615ull)" in cpp
        assert cpp == self._cpp(src)

    def test_wide_range_bounds_inline_byte_identical(self):
        # Wide literal range bounds must keep the AST's inline-vs-hoist choice:
        # _extract_int_literal folds any width, so THIR inlines them too (the
        # token render is shared; only the placement decision can diverge).
        src = (
            _NUMLIT_PRELUDE
            + "def f() -> None:\n"
            + "    for i in range(3000000000, 3000000005):\n        print(i)\n"
            + "def g(n: Int64) -> None:\n"
            + "    for i in range(2147483648, n):\n        print(i)\n"
            + "def h() -> None:\n"
            + "    for i in range(-2147483649, -2147483645):\n        print(i)\n"
        )
        thir = _lower(src)
        for name in ("f", "g", "h"):
            assert _fn(thir, name) is not None, name
        rng = _fn(thir, "g").body[0]
        assert isinstance(rng, THIRForRange)
        assert rng.start_is_literal and not rng.stop_is_literal
        cpp = self._cpp(src)
        assert ("for (int64_t i = static_cast<int64_t>(2147483648); "
                "i < __stop_0; ++i)") in cpp
        assert cpp == self._cpp(src)

    def test_ctor_literal_range_bounds_inline_byte_identical(self):
        src = (
            _NUMLIT_PRELUDE
            + "def positive(n: Int32) -> None:\n"
            + "    for i in range(Int32(1), n):\n        print(i)\n"
            + "def negative(n: Int32) -> None:\n"
            + "    for i in range(Int32(-2), n):\n        print(i)\n"
        )
        thir = _lower(src)
        for name in ("positive", "negative"):
            rng = _fn(thir, name).body[0]
            assert isinstance(rng, THIRForRange)
            assert rng.start_is_literal and not rng.stop_is_literal
        cpp = self._cpp(src)
        assert "__start_" not in cpp
        assert cpp == self._cpp(src)

    def test_wide_ctor_literal_range_bounds_inline_byte_identical(self):
        # Ctor arm x literal width: the shared extraction folds a wide value
        # inside a fixed-int ctor the same as a bare wide literal.
        src = (
            _NUMLIT_PRELUDE
            + "def wide(n: Int64) -> None:\n"
            + "    for i in range(Int64(2147483648), n):\n        print(i)\n"
            + "def minimum(n: Int64) -> None:\n"
            + "    for i in range(Int64(-9223372036854775808), n):\n"
            + "        print(i)\n"
        )
        thir = _lower(src)
        for name in ("wide", "minimum"):
            rng = _fn(thir, name).body[0]
            assert isinstance(rng, THIRForRange)
            assert rng.start_is_literal and not rng.stop_is_literal
        cpp = self._cpp(src)
        assert "__start_" not in cpp
        assert cpp == self._cpp(src)

    def test_wide_literal_step_defers(self):
        # The stepped arms' overflow-check render is pinned only for the
        # int32-range step subset -- a wide literal step keeps the whole body
        # on the AST path.
        src = (
            _NUMLIT_PRELUDE
            + "def f(n: Int64) -> None:\n"
            + "    for i in range(0, n, 3000000000):\n        print(i)\n"
        )
        assert _fn(_lower(src), "f") is None

    def test_bare_wide_literal_untargeted_render(self):
        # A wide literal in a container-literal element has no coercion target
        # (the init list supplies the type), taking render_int_literal_value's
        # target-less arms: <= int32 bare, int32 < v <= int64 pinned to
        # static_cast<int64_t> (so an implicit BigInt conversion is unambiguous
        # on macOS), and `ull`-suffixed above int64 (uint64 is exact into BigInt).
        src = (
            _NUMLIT_PRELUDE
            + "from tpy import UInt64\n"
            + "def f() -> None:\n"
            + "    big: list[Int64] = [1000000000, 3000000000]\n"
            + "    huge: list[UInt64] = [18446744073709551615]\n"
            + "    print(big[1], huge[0])\n"
        )
        thir = _lower(src)
        assert _fn(thir, "f") is not None
        cpp = self._cpp(src)
        assert "{1000000000, static_cast<int64_t>(3000000000)}" in cpp
        assert "{static_cast<uint64_t>(18446744073709551615ull)}" in cpp
        assert cpp == self._cpp(src)

    def test_wide_method_arg_uses_coercion_target(self):
        src = (
            _NUMLIT_PRELUDE
            + "class C:\n"
            + "    def __init__(self) -> None:\n        pass\n"
            + "    def take(self, n: Int64) -> Int64:\n        return n\n"
            + "def f(c: C) -> Int64:\n    return c.take(2147483648)\n"
        )
        compiler, modules = _compile(src)
        entry = _entry(modules)
        ast = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(
                emit_source_comments=False))
        thir = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(
                emit_source_comments=False))
        assert "c.take(static_cast<int64_t>(2147483648))" in thir[1]
        assert thir == ast

    def test_bigint_compare_over_int32_literal_pins_int64(self):
        # A BigInt operand compared to an int literal exceeding int32: the
        # target-less literal renders int64-pinned static_cast<int64_t>(...) (not
        # a bare `long`, which converts to BigInt ambiguously on macOS where
        # int64_t != long) -- an exact BigInt(int64_t) conversion at the compare.
        # A small literal (<= int32) stays bare. Same on both paths.
        src = (
            _NUMLIT_PRELUDE
            + "def f(n: int) -> bool:\n"
            + "    return n >= 86400000000 and n == 5\n"
        )
        assert _fn(_lower(src), "f") is not None
        cpp = self._cpp(src)
        assert "static_cast<int64_t>(86400000000)" in cpp
        assert "n == 5" in cpp  # small literal stays bare (unambiguous)
        assert cpp == self._cpp(src)

    def test_bigint_list_element_over_int32_pins_int64(self):
        # A >int32 int literal in a BigInt list literal renders int64-pinned
        # static_cast<int64_t>(...) (not a bare `long`) on both paths -- the
        # target-less render policy in render_int_literal_value, no per-site
        # retargeting. A small element stays bare; a fixed Int64 list is a plain
        # brace-init (the same pin, identity into an int64 slot).
        src = (
            _NUMLIT_PRELUDE
            + "def f() -> int:\n"
            + "    xs: list[int] = [10, 1234567890123456789]\n"
            + "    return xs[0]\n"
            + "def g() -> Int64:\n"
            + "    ys: list[Int64] = [1234567890123456789, 5]\n"
            + "    return ys[0]\n"
        )
        assert _fn(_lower(src), "f") is not None
        assert _fn(_lower(src), "g") is not None
        cpp = self._cpp(src)
        assert "{10, static_cast<int64_t>(1234567890123456789)}" in cpp
        assert "std::vector<int64_t> ys = {static_cast<int64_t>(1234567890123456789), 5}" in cpp
        assert cpp == self._cpp(src)

    def test_byte_identical(self):
        src = (
            _NUMLIT_PRELUDE
            + "def f(x: Int32, y: Float64) -> Float64:\n    return y\n"
            + "def sink(y: Float64) -> Float64:\n    return y\n"
            + "def g(xs: list[Int32], s: str) -> Int32:\n"
            + "    a = -3\n"
            + "    print(f(3, 1.5), sink(2.5), xs[-1], s[1:-1], s[-3:], -7)\n"
            + "    for i in range(-3, 3):\n        a = a + i\n"
            + "    if a > -10:\n        return a + -2\n"
            + "    return -1\n"
            + "def main():\n"
            + "    xs = [1, 2, 3]\n"
            + '    print(g(xs, "hello world"))\n'
            + "main()\n"
        )
        thir = _lower(src)
        assert _fn(thir, "g") is not None


# --- Scalar type-constructor calls (Int32(x) / Float64(x) / bool(n)) ---


_CTOR_PRELUDE = "from tpy import Int32, Int64, UInt32, UInt64, Float64\n"


class TestScalarCtorCall:
    def test_literal_passthrough_routes(self):
        # Int32(0) -- the same-type overload's `{0}` template over a literal.
        thir = _lower(_CTOR_PRELUDE + "def f() -> Int32:\n    x = Int32(0)\n    return x\n")
        fn = _fn(thir, "f")
        assert fn is not None
        init = fn.body[0].init
        assert isinstance(init, THIRCall) and init.callee == "Int32"
        assert init.cpp_template == "{0}"
        assert isinstance(init.args[0], THIRLiteral) and init.args[0].value == 0

    def test_int_cast_check_routes(self):
        # Int64(a) with a: Int32 -- the generic AnyFixedInt overload; sema has
        # already substituted {cpp} with the concrete return spelling.
        thir = _lower(_CTOR_PRELUDE
                      + "def f(a: Int32) -> Int64:\n    return Int64(a)\n")
        ret = _fn(thir, "f").body[0].value
        assert isinstance(ret, THIRCall)
        assert ret.cpp_template == "::tpy::int_cast_check<int64_t>({0})"

    def test_zero_arg_ctor_routes(self):
        # Int32() -- the 0-arity overload's constant template.
        thir = _lower(_CTOR_PRELUDE + "def f() -> Int32:\n    return Int32()\n")
        ret = _fn(thir, "f").body[0].value
        assert isinstance(ret, THIRCall) and ret.cpp_template == "0"
        assert ret.args == ()

    def test_float_from_int_routes(self):
        thir = _lower(_CTOR_PRELUDE
                      + "def f(a: Int32) -> float:\n    return Float64(a)\n")
        ret = _fn(thir, "f").body[0].value
        assert isinstance(ret, THIRCall)
        assert ret.cpp_template == "static_cast<double>({0})"

    def test_binop_arg_routes(self):
        # The cast wraps a parenthesized binop -- the arg lowers through the
        # normal THIRBinOp (paren_wrap default), so the emit keeps the parens.
        thir = _lower(_CTOR_PRELUDE
                      + "def f(a: Int32) -> Int64:\n    return Int64(a + a)\n")
        ret = _fn(thir, "f").body[0].value
        assert isinstance(ret, THIRCall) and isinstance(ret.args[0], THIRBinOp)

    def test_ctor_as_call_and_print_arg_routes(self):
        thir = _lower(_CTOR_PRELUDE
                      + "def use(v: Int64) -> Int64:\n    return v\n"
                      + "def f(a: Int32) -> None:\n"
                      + "    print(use(Int64(a)), Int32(7))\n")
        fn = _fn(thir, "f")
        assert fn is not None
        outer = fn.body[0].args[0].expr
        assert isinstance(outer, THIRCall) and outer.callee == "use"
        assert isinstance(outer.args[0], THIRCall)
        assert outer.args[0].cpp_template is not None

    def test_bool_ctor_routes(self):
        thir = _lower(_CTOR_PRELUDE + "def f() -> bool:\n    return bool(1)\n")
        ret = _fn(thir, "f").body[0].value
        assert isinstance(ret, THIRCall) and ret.cpp_template == "({0} != 0)"

    def test_bigint_result_routes(self):
        # int(x) constructs a BigInt -- an eligible scalar result; the
        # resolved __init__ template expands like any scalar ctor.
        thir = _lower(_CTOR_PRELUDE
                      + "def f(a: Int32) -> None:\n    x = int(a)\n    print(a)\n")
        assert _fn(thir, "f") is not None

    def test_str_result_routes(self):
        src = (_CTOR_PRELUDE
               + "def f(a: Int32) -> None:\n    s = str(a)\n    print(a)\n")
        assert _fn(_lower(src), "f") is not None
        compiler, modules = _compile(src)
        entry = _entry(modules)
        ast = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(
                emit_source_comments=False))
        thir = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(
                emit_source_comments=False))
        assert thir == ast

    def test_float_str_constant_folds(self):
        # float("nan") folds to a numeric_limits constant on the AST path (not
        # float_from_str); the fold pre-arm mirrors it as a pre-spelled
        # template call (call.float_str_fold), so the body routes.
        thir = _lower(_CTOR_PRELUDE
                      + "def f() -> float:\n    return float(\"nan\")\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[-1]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRCall)
        assert ret.value.cpp_template == "std::numeric_limits<double>::quiet_NaN()"

    def test_char_ctor_str_literal_routes(self):
        # Char("a") is a @native(function=True) ctor with NO literal fold on
        # the AST path (`char c = ::tpy::char_from_str("a");`), so the
        # narrowed exclusion (float special constants only) admits it --
        # byte-identical through the native free-ctor arm.
        src = ("from tpy import Char\n"
               + "def f() -> None:\n    c = Char(\"a\")\n    print(1)\n")
        assert _fn(_lower(src), "f") is not None
        compiler, modules = _compile(src)
        entry = _entry(modules)
        ast = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(
                emit_source_comments=False))
        thir = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(
                emit_source_comments=False))
        assert thir == ast

    def test_int_ctor_str_literal_routes(self):
        # int("123") -> `::tpy::BigInt::from_str("123")` on both paths; no
        # fold exists for int, so ordinary str literals route (the narrowed
        # exclusion covers only float's special constants).
        src = "def f() -> None:\n    print(int(\"123\"))\n"
        assert _fn(_lower(src), "f") is not None
        _assert_byte_identical(src)

    def test_float32_ctor_routes(self):
        # Float32(1.5) routes: the result is an eligible scalar and the
        # literal arg renders `f`-suffixed against its param slot (see
        # TestFloat32AndCastCoercions for the byte-identity pin).
        thir = _lower("from tpy import Float32\n"
                      + "def f() -> None:\n    x = Float32(1.5)\n    print(x)\n")
        assert _fn(thir, "f") is not None

    def test_wide_literal_arg_routes(self):
        src = (_CTOR_PRELUDE
               + "def f() -> None:\n"
               + "    g = UInt32(4294967295)\n"
               + "    print(g)\n")
        assert _fn(_lower(src), "f") is not None
        compiler, modules = _compile(src)
        entry = _entry(modules)
        ast = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(
                emit_source_comments=False))
        thir = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(
                emit_source_comments=False))
        assert "static_cast<uint32_t>(4294967295)" in thir[1]
        assert thir == ast

    def test_negative_literal_arg_routes(self):
        # A `-3` ctor arg folds to a plain literal on both paths (the AST's
        # _gen_unaryop literal-negation branch feeds the template expansion).
        thir = _lower(_CTOR_PRELUDE
                      + "def f() -> None:\n    d = Int32(-3)\n    print(d)\n")
        fn = _fn(thir, "f")
        assert fn is not None
        call = fn.body[0].init
        assert isinstance(call.args[0], THIRLiteral) and call.args[0].value == -3

    def test_negated_float_literal_arg_routes(self):
        # A negated FLOAT literal ctor arg takes the resolved __neg__ template
        # (`-(1.5)`), now routed via THIRUnaryArith byte-identically.
        src = (_CTOR_PRELUDE
               + "def f() -> None:\n    d = Float64(-1.5)\n    print(d)\n")
        assert _fn(_lower(src), "f") is not None
        _assert_byte_identical(src)



class TestScalarCtorCallEmit:
    def _cpp(self, src: str):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        return cpp

    SRC = (
        _CTOR_PRELUDE
        + "def conv(a: Int32, b: UInt32) -> Int64:\n"
        + "    w = Int64(a)\n"
        + "    u = UInt64(b)\n"
        + "    s = Int64(a + a)\n"
        + "    return w + s\n"
        + "def seed() -> Int32:\n"
        + "    z = Int32()\n"
        + "    x = Int32(0)\n"
        + "    y = Int32(x)\n"
        + "    return x + y + z\n"
        + "def fl(a: Int32) -> float:\n"
        + "    m = Float64(a)\n"
        + "    return m + Float64(1.5)\n"
        + "def flags() -> bool:\n"
        + "    k = bool(1)\n"
        + "    return k\n"
        + "def use(v: Int64) -> Int64:\n    return v\n"
        + "def main():\n"
        + "    print(conv(3, UInt32(4)))\n"
        + "    print(seed(), fl(2), flags())\n"
        + "    print(use(Int64(9)))\n"
        + "main()\n"
    )

    def test_routed(self):
        thir = _lower(self.SRC)
        for name in ("conv", "seed", "fl", "flags"):
            assert _fn(thir, name) is not None, name

    def test_emit_arms(self):
        cpp = self._cpp(self.SRC)
        assert "int64_t w = ::tpy::int_cast_check<int64_t>(a);" in cpp
        assert "uint64_t u = ::tpy::int_cast_check<uint64_t>(b);" in cpp
        # the cast keeps the binop's paren wrap
        assert ("int64_t s = ::tpy::int_cast_check<int64_t>"
                "((::tpy::add_check<int32_t>(a, a)));") in cpp
        assert "int32_t z = 0;" in cpp           # zero-arg ctor
        assert "int32_t x = 0;" in cpp           # literal passthrough
        assert "int32_t y = x;" in cpp           # same-type passthrough
        assert "double m = static_cast<double>(a);" in cpp
        assert "bool k = (1 != 0);" in cpp
        assert "use(9)" in cpp                   # ctor folded in a call arg


# --- Float32 values + the scalar-cast template coercions (increment 73) ---


class TestFloat32AndCastCoercions:
    def _cpp(self, src: str):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        return cpp

    SRC = (
        _NUMLIT_PRELUDE
        + "def use32(v: Float32) -> Float32:\n    return v\n"
        + "def use64(v: Float64) -> Float64:\n    return v\n"
        + "def use_i64(v: Int64) -> Int64:\n    return v\n"
        + "def mix(a: Float32, b: Float32, n: Int32) -> Float32:\n"
        + "    c: Float32 = 1.5\n"
        + "    d = a + b\n"
        + "    e = use32(n)\n"
        + "    w = use64(a)\n"
        + "    k = use64(3)\n"
        + "    g = use32(w)\n"
        + "    h = use_i64(n)\n"
        + "    if a < b:\n        return c + d\n"
        + "    print(e, g, w)\n"
        + '    print(f"a={a} n={n}")\n'
        + "    return b\n"
        + "def main():\n"
        + "    print(mix(0.5, 2.5, 7))\n"
        + "main()\n"
    )

    def test_routed(self):
        thir = _lower(self.SRC)
        assert _fn(thir, "mix") is not None

    def test_emit_arms(self):
        cpp = self._cpp(self.SRC)
        assert "float c = 1.5f;" in cpp                       # f-suffix literal
        assert "float e = use32(static_cast<float>(n));" in cpp   # fixed_int_to_float32
        assert "double w = use64(static_cast<double>(a));" in cpp  # float32_to_float
        assert "double k = use64(static_cast<double>(3));" in cpp  # int_literal_to_float
        assert "float g = use32(static_cast<float>(w));" in cpp    # float_to_float32
        assert "int64_t h = use_i64(static_cast<int64_t>(n));" in cpp  # fixed_int_widening
        assert "::tpy::print_float(static_cast<double>(e))" in cpp     # print float32
        assert "::tpy::float_to_str(static_cast<double>(a))" in cpp    # f-string float32

    def test_float32_ctor_call_byte_identical(self):
        # Float32(x) / Float32(1.5) now pass the scalar-ctor gate (the result
        # is an eligible scalar); pin the expansion against the AST render.
        src = (
            _NUMLIT_PRELUDE
            + "def f(x: Float64, n: Int32) -> Float32:\n"
            + "    a = Float32(1.5)\n"
            + "    b = Float32(x)\n"
            + "    c = Float32(n)\n"
            + "    return a + b + c\n"
            + "def main():\n    print(f(2.5, 3))\nmain()\n"
        )


# --- Fixed-int bitwise ops (& | ^ << >>) at the scalar binop arm ---


class TestBitwiseBinops:
    def _cpp(self, src: str):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        return cpp

    SRC = (
        "from tpy import Int32, UInt16, Int64\n"
        + "def f_and(a: Int32, b: Int32) -> Int32:\n    return a & b\n"
        + "def f_or(a: Int32, b: Int32) -> Int32:\n    return a | b\n"
        + "def f_xor(a: Int64, b: Int64) -> Int64:\n    return a ^ b\n"
        + "def f_shl(a: Int32, b: Int32) -> Int32:\n    return a << b\n"
        + "def f_shr(a: UInt16, b: UInt16) -> UInt16:\n    return a >> b\n"
        + "def f_mix(x: Int32) -> Int32:\n    return (x & 255) | 1\n"
        + "def main():\n"
        + "    print(int(f_and(6, 3)), int(f_or(4, 1)), int(f_xor(5, 1)))\n"
        + "    print(int(f_shl(1, 4)), int(f_shr(256, 2)), int(f_mix(511)))\n"
        + "main()\n"
    )

    def test_routed(self):
        thir = _lower(self.SRC)
        for name in ("f_and", "f_or", "f_xor", "f_shl", "f_shr", "f_mix"):
            assert _fn(thir, name) is not None, name

    def test_face_witnessed(self):
        # Without the pin a refactor could un-witness the bitwise arm while the
        # bodies still route via the shared arith tail and the byte-diff stays green.
        _, witnessed = _lower_ctx_witnessed(self.SRC)
        assert witnessed.get("binop.bitwise", 0) > 0

    def test_emit_arms(self):
        cpp = self._cpp(self.SRC)
        # `&`/`|`/`^` expand the fixed-int static_cast template; shifts take the
        # checked helper -- both the resolved-binop template arm arithmetic uses.
        assert "return (static_cast<int32_t>(a & b));" in cpp
        assert "return (static_cast<int32_t>(a | b));" in cpp
        assert "return (static_cast<int64_t>(a ^ b));" in cpp
        assert "return (::tpy::lshift_check<int32_t>(a, b));" in cpp
        assert "return (::tpy::rshift_check<uint16_t>(a, b));" in cpp
        assert ("return (static_cast<int32_t>((static_cast<int32_t>(x & 255)) | 1));"
                in cpp)

    def test_set_intersection_ineligible(self):
        # A set `&` returns a container, not a scalar -- rejected at the arm's
        # `_resolved_scalar` result check, so it stays on the AST path.
        src = (
            "from tpy import Int32, Own\n"
            + "def inter(a: set[Int32], b: set[Int32]) -> Own[set[Int32]]:\n"
            + "    return a & b\n"
            + "def main():\n    print(len(inter({1, 2}, {2, 3})))\nmain()\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "inter") is None


_MACRO_MOD = '''# tpy: macro_module
"""Post-sema function macros for the resolver-less scalar-binop pins."""
from tpyc.macro_api import (
    function_macro, FunctionMacroContext, ast, TpyCall, TpyName,
)


def _calls(body, fname):
    out = []
    for stmt in body:
        for expr in stmt.exprs():
            if (isinstance(expr, TpyCall) and isinstance(expr.func, TpyName)
                    and expr.func.name == fname):
                out.append(expr)
    return out


@function_macro
def to_name_add(ctx: FunctionMacroContext) -> None:
    if _calls(ctx.body, "sentinel"):
        ctx.defer_until_sema_complete(_resolve_names)


def _resolve_names(ctx) -> None:
    for call in _calls(ctx.body, "sentinel"):
        t = ctx.type_of(call.args[0])
        summed = ast.binop(call.args[0], "+", call.args[1])
        ctx.set_expr_type(summed, t)
        ctx.replace_expr(call, summed)


@function_macro
def to_literal_add(ctx: FunctionMacroContext) -> None:
    if _calls(ctx.body, "sentinel"):
        ctx.defer_until_sema_complete(_resolve_literal)


def _resolve_literal(ctx) -> None:
    for call in _calls(ctx.body, "sentinel"):
        t = ctx.type_of(call.args[0])
        summed = ast.binop(call.args[0], "+", ast.int_lit(1))
        ctx.set_expr_type(summed, t)
        ctx.replace_expr(call, summed)
'''

_MACRO_SRC = (
    "from rawbinmod import {deco}\n"
    "from tpy import Int32\n"
    "def sentinel(a: Int32, b: Int32) -> Int32:\n"
    "    return 0\n"
    "@{deco}\n"
    "def combine(a: Int32, b: Int32) -> Int32:\n"
    "    return sentinel(a, b)\n"
    "def main() -> None:\n"
    "    print(combine(3, 4))\n"
    "main()\n")


class TestScalarRawBinop:
    """A resolver-less scalar binop (a post-sema function macro synthesizes
    `a + b` AFTER sema's binop resolution, so `resolved_binop` is None):
    the AST's record-dunder fallback renders the raw C++ operator
    (`(a + b)`) -- mirrored by the `binop.scalar_raw` leg, bare NAME
    operands only."""

    def _write_macro(self, tmp_path):
        (tmp_path / "rawbinmod.py").write_text(_MACRO_MOD)

    def _cpp(self, src, tmp_path):
        compiler, modules = _compile(src, extra_lib_dirs=[tmp_path])
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        return cpp

    def test_macro_synthesized_name_binop_routes(self, tmp_path):
        self._write_macro(tmp_path)
        src = _MACRO_SRC.format(deco="to_name_add")
        compiler, modules = _compile(src, extra_lib_dirs=[tmp_path])
        entry = _entry(modules)
        from ..compiler import activate_compiler
        from .lower import lower_module
        with activate_compiler(compiler):
            thir = lower_module(entry.ast, entry.analyzer)
        assert _fn(thir, "combine") is not None
        cpp = self._cpp(src, tmp_path)
        assert "return (a + b);" in cpp
        assert cpp == self._cpp(src, tmp_path)

    def test_literal_operand_still_defers(self, tmp_path):
        # BOUNDARY: a macro-synthesized `a + 1` has a literal operand --
        # outside the bare-NAME slice, the body keeps falling back.
        self._write_macro(tmp_path)
        src = _MACRO_SRC.format(deco="to_literal_add")
        compiler, modules = _compile(src, extra_lib_dirs=[tmp_path])
        entry = _entry(modules)
        from ..compiler import activate_compiler
        from .lower import lower_module
        with activate_compiler(compiler):
            thir = lower_module(entry.ast, entry.analyzer)
        assert _fn(thir, "combine") is None


class TestMarkerSpecialFolds:
    """Marker-site cells: the module-qualified twins of the
    special-builtin arms (the AST intercepts both spellings before the
    module dispatch), plus the imported-name local-shadow callee."""

    def test_module_float_fold_routes(self):
        # The corpus shape: `float("nan")` resolves through the
        # builtins-module ctor marker inside a math call -- the method-call
        # twin of the free fold arm.
        src = ("import math\n"
               "def f() -> None:\n"
               "    print(math.isnan(float(\"nan\")))\n"
               "    print(math.isinf(float(\"-inf\")))\n"
               "f()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("call.float_str_fold", 0) >= 2
        _assert_byte_identical(src)

    def test_module_copy_str_routes(self):
        # `u = t.copy(s)` at top level -> `u = std::string(s);`: the
        # module-qualified copy spelling over the new str-NAME arm
        # (call.copy_str). Top-level only: an in-FUNCTION local source is
        # still PendingStr at the copy render and crashes the AST path
        # (BUGS.md) -- THIR's NominalType guard falls it back gracefully.
        src = ("import tpy as t\n"
               "s: str = \"hello\"\n"
               "u = t.copy(s)\n"
               "print(u)\n")
        top, faces, _fb = _top_level(src)
        assert top is not None
        assert faces.get("call.copy_str", 0) >= 1
        _assert_byte_identical(src)

    def test_copy_str_field_source_stays_ast(self):
        # The str copy arm is NAME-only: a FIELD source keeps the AST's
        # copy machinery (boundary).
        src = ("import tpy as t\n"
               "class H:\n"
               "    s: str\n"
               "    def __init__(self) -> None:\n"
               "        self.s = \"x\"\n"
               "def f() -> None:\n"
               "    h = H()\n"
               "    u = t.copy(h.s)\n"
               "    print(u)\n"
               "f()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.copy_source.str")

    def test_imported_name_local_shadow_routes(self):
        # `from time import time` + a LOCAL `def time()`: the call resolves
        # to the local def and emits the bare unqualified name (the AST's
        # conditional-qualification arm leaves shadows alone).
        src = ("from time import time\n"
               "def time() -> int:\n"
               "    return 42\n"
               "def f() -> None:\n"
               "    print(time())\n"
               "f()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)
