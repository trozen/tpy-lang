"""THIR scalar call-arg shapes: bare numeric-literal args, negated int
literals, scalar type-constructor calls (Int32(x) / Float64(x) / bool(n))."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .nodes import (
    THIRBinOp, THIRCall, THIRCoerce, THIRForRange, THIRLiteral,
)
from .testutil import (
    _compile, _entry, _lower, _lower_ctx, _lower_ctx_witnessed, _fn, _emit_expr,
)

# --- Bare numeric-literal call args + negated int literals (increment 45) ---

_NUMLIT_PRELUDE = "from tpy import Int32, Int64, Float32, Float64\n"


class TestNumericLiteralArgs:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
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

    def test_inf_literal_arg_ineligible(self):
        # `1e400` parses to inf; repr(inf) is not valid C++ -> AST path.
        thir = _lower(
            _NUMLIT_PRELUDE
            + "def f(x: Float64) -> Float64:\n    return x\n"
            + "def g() -> None:\n    v = f(1e400)\n    print(1)\n")
        assert _fn(thir, "g") is None

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

    def test_negated_float_literal_ineligible(self):
        # A negated FLOAT literal is not folded by the AST -- it takes the
        # resolved __neg__ template (`-(1.5)`) -> AST path.
        thir = _lower(
            _NUMLIT_PRELUDE
            + "def f(x: Float64) -> Float64:\n    return x\n"
            + "def g() -> None:\n    v = f(-1.5)\n    print(1)\n"
            + "def h() -> None:\n    v = -2.5\n    print(v)\n")
        assert _fn(thir, "g") is None
        assert _fn(thir, "h") is None

    def test_negated_name_ineligible(self):
        # Only a LITERAL operand folds; `-x` needs the __neg__ template.
        thir = _lower(
            _NUMLIT_PRELUDE
            + "def g(x: Int32) -> Int32:\n    return -x\n")
        assert _fn(thir, "g") is None

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
        cpp = self._cpp(src, thir=True)
        assert "static_cast<int64_t>(2147483648)" in cpp
        assert "static_cast<int64_t>((-9223372036854775807LL - 1))" in cpp
        assert "static_cast<uint64_t>(18446744073709551615ull)" in cpp
        assert cpp == self._cpp(src, thir=False)

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
        cpp = self._cpp(src, thir=True)
        assert ("for (int64_t i = static_cast<int64_t>(2147483648); "
                "i < __stop_0; ++i)") in cpp
        assert cpp == self._cpp(src, thir=False)

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
        cpp = self._cpp(src, thir=True)
        assert "__start_" not in cpp
        assert cpp == self._cpp(src, thir=False)

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
        cpp = self._cpp(src, thir=True)
        assert "__start_" not in cpp
        assert cpp == self._cpp(src, thir=False)

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
        cpp = self._cpp(src, thir=True)
        assert "{1000000000, static_cast<int64_t>(3000000000)}" in cpp
        assert "{static_cast<uint64_t>(18446744073709551615ull)}" in cpp
        assert cpp == self._cpp(src, thir=False)

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
                emit_source_comments=False, thir_codegen=False))
        thir = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
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
        cpp = self._cpp(src, thir=True)
        assert "static_cast<int64_t>(86400000000)" in cpp
        assert "n == 5" in cpp  # small literal stays bare (unambiguous)
        assert cpp == self._cpp(src, thir=False)

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
        cpp = self._cpp(src, thir=True)
        assert "{10, static_cast<int64_t>(1234567890123456789)}" in cpp
        assert "std::vector<int64_t> ys = {static_cast<int64_t>(1234567890123456789), 5}" in cpp
        assert cpp == self._cpp(src, thir=False)

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
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)


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
                emit_source_comments=False, thir_codegen=False))
        thir = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        assert thir == ast

    def test_float_str_arg_ineligible(self):
        # float("nan") folds to a numeric_limits constant on the AST path -- the
        # str-literal arg fails the scalar arg gate, keeping the fold there.
        thir = _lower(_CTOR_PRELUDE
                      + "def f() -> float:\n    return float(\"nan\")\n")
        assert _fn(thir, "f") is None

    def test_char_ctor_ineligible(self):
        # Char("a") resolves to a @native(function=True) ctor (no cpp_template),
        # and neither the str arg nor the Char result is an eligible scalar.
        thir = _lower("from tpy import Char\n"
                      + "def f() -> None:\n    c = Char(\"a\")\n    print(1)\n")
        assert _fn(thir, "f") is None

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
                emit_source_comments=False, thir_codegen=False))
        thir = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
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

    def test_negated_float_literal_arg_ineligible(self):
        # A negated FLOAT literal takes the resolved __neg__ template
        # (`-(1.5)`), a render the slice does not reproduce -> AST path.
        thir = _lower(_CTOR_PRELUDE
                      + "def f() -> None:\n    d = Float64(-1.5)\n    print(d)\n")
        assert _fn(thir, "f") is None



class TestScalarCtorCallEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
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

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emit_arms(self):
        cpp = self._cpp(self.SRC, thir=True)
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
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
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

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emit_arms(self):
        cpp = self._cpp(self.SRC, thir=True)
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
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)


# --- Fixed-int bitwise ops (& | ^ << >>) at the scalar binop arm ---


class TestBitwiseBinops:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
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

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_face_witnessed(self):
        # Without the pin a refactor could un-witness the bitwise arm while the
        # bodies still route via the shared arith tail and the byte-diff stays green.
        _, witnessed = _lower_ctx_witnessed(self.SRC)
        assert witnessed.get("binop.bitwise", 0) > 0

    def test_emit_arms(self):
        cpp = self._cpp(self.SRC, thir=True)
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
