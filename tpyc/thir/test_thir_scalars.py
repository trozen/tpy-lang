"""THIR scalar call-arg shapes: bare numeric-literal args, negated int
literals, scalar type-constructor calls (Int32(x) / Float64(x) / bool(n))."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .nodes import (
    THIRBinOp, THIRCall, THIRCoerce, THIRForRange, THIRLiteral,
)
from .testutil import (
    _compile, _entry, _lower, _fn,
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

    def test_float32_slot_ineligible(self):
        # A float literal into a Float32 slot arrives float_literal_to_float32
        # coerce-wrapped (the `f`-suffix render) -> AST path.
        thir = _lower(
            _NUMLIT_PRELUDE
            + "def f(x: Float32) -> Float32:\n    return x\n"
            + "def g() -> None:\n    v = f(1.5)\n    print(1)\n")
        assert _fn(thir, "g") is None

    def test_inf_literal_arg_ineligible(self):
        # `1e400` parses to inf; repr(inf) is not valid C++ -> AST path.
        thir = _lower(
            _NUMLIT_PRELUDE
            + "def f(x: Float64) -> Float64:\n    return x\n"
            + "def g() -> None:\n    v = f(1e400)\n    print(1)\n")
        assert _fn(thir, "g") is None

    def test_bigint_slot_literal_ineligible(self):
        # An int literal into a BigInt slot wraps `::tpy::BigInt(3)` -> AST.
        thir = _lower(
            _NUMLIT_PRELUDE
            + "def f(x: int) -> int:\n    return x\n"
            + "def g() -> None:\n    v = f(3)\n    print(1)\n")
        assert _fn(thir, "g") is None

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

    def test_out_of_range_negation_ineligible(self):
        # A negation outside the +-int32 literal range takes the wide-literal
        # suffix/cast render -> AST path. INT32_MIN itself (-2**31) still folds.
        thir = _lower(
            _NUMLIT_PRELUDE
            + "def g() -> Int64:\n    return -2147483649\n"
            + "def h() -> Int32:\n    return -2147483648\n")
        assert _fn(thir, "g") is None
        assert _fn(thir, "h") is not None

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

    def test_bigint_result_ineligible(self):
        # int(x) constructs a BigInt -- not an eligible scalar result.
        thir = _lower(_CTOR_PRELUDE
                      + "def f(a: Int32) -> None:\n    x = int(a)\n    print(a)\n")
        assert _fn(thir, "f") is None

    def test_str_result_ineligible(self):
        thir = _lower(_CTOR_PRELUDE
                      + "def f(a: Int32) -> None:\n    s = str(a)\n    print(a)\n")
        assert _fn(thir, "f") is None

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

    def test_float32_ctor_ineligible(self):
        # Float32 literals need an `f` suffix the slice does not emit.
        thir = _lower("from tpy import Float32\n"
                      + "def f() -> None:\n    x = Float32(1.5)\n    print(1)\n")
        assert _fn(thir, "f") is None

    def test_wide_literal_arg_ineligible(self):
        # A literal outside int32 range renders with a static_cast wrap
        # (_gen_int_literal_value) the bare THIRLiteral emit does not reproduce.
        thir = _lower(_CTOR_PRELUDE
                      + "def f() -> None:\n    g = UInt32(4294967295)\n    print(1)\n")
        assert _fn(thir, "f") is None

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
