"""The optional-ptr `scalar_temp` face: a resolved-scalar RVALUE at a
pointer-repr `Optional[scalar]` call-arg slot hoists a typed temp and lifts its
address (`int32_t __tmp_1 = 99; c.set(&(__tmp_1));`). Corpus witness:
none_safety/generic_optional. The CTOR pin is also the regression guard for the
ill-formed `Container<int32_t>(42)` the render used to emit there."""

import pytest

from .testutil import (_assert_byte_identical, _assert_routes_byte_identical,
                       _compile, _entry, _fn, _lower_ctx_witnessed)
from ..codegen_cpp import CodeGenOptions

_CONTAINER = (
    "from tpy import Int32, Int64, Char\n"
    "class Container[T]:\n"
    "    _val: T | None\n"
    "    def __init__(self, val: T | None):\n"
    "        self._val = val\n"
    "    def get(self) -> T | None:\n"
    "        return self._val\n"
    "    def set(self, val: T | None) -> None:\n"
    "        self._val = val\n"
    "    def probe(self, val: T | None) -> bool:\n"
    "        return val is not None\n"
)
_MAIN = "\ndef main() -> None:\n    f()\nmain()\n"


def _cpp(src: str, thir: bool) -> str:
    compiler, modules = _compile(src)
    _, cpp = compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False,
                                                thir_codegen=thir))
    return cpp


class TestScalarTempMethodArg:
    SRC = _CONTAINER + (
        "def f() -> None:\n"
        "    c = Container[Int32](None)\n"
        "    c.set(Int32(99))\n") + _MAIN

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC)

    def test_witnesses_scalar_temp_face(self):
        thir, witnesses = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "f") is not None
        assert witnesses.get("optptr.scalar_temp", 0) == 1

    def test_emits_typed_temp_and_addr_of(self):
        out = _cpp(self.SRC, thir=True)
        assert "int32_t __tmp_1 = 99;" in out
        assert "c.set(&(__tmp_1));" in out


class TestScalarTempCtorArg:
    """The CONSTRUCTOR position, where the arg gate admits the scalar
    slot-blind: before the face existed the render passed the value bare
    (`Container<int32_t>(42)`) into an `int32_t*` slot -- ill-formed C++ that
    the corpus byte-diff could not see, since the only witness body also fell
    back for an unrelated reason."""

    SRC = _CONTAINER + (
        "def f() -> None:\n"
        "    c = Container[Int32](Int32(42))\n"
        "    print(1)\n") + _MAIN

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC)

    def test_witnesses_scalar_temp_face(self):
        thir, witnesses = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "f") is not None
        assert witnesses.get("optptr.scalar_temp", 0) == 1

    def test_emits_typed_temp_not_bare_value(self):
        out = _cpp(self.SRC, thir=True)
        assert "int32_t __tmp_1 = 42;" in out
        assert "Container<int32_t> c = Container<int32_t>(&(__tmp_1));" in out
        assert "Container<int32_t>(42)" not in out


class TestScalarTempRvalueSources:
    """The non-call rvalue sources the AST's `is_temporary_expr` hoists too:
    a binop, a unary op, and a free call returning the pointee type."""

    BINOP = _CONTAINER + (
        "def f() -> None:\n"
        "    n = Int32(3)\n"
        "    c = Container[Int32](None)\n"
        "    c.set(n + 1)\n") + _MAIN
    UNARY = _CONTAINER + (
        "def f() -> None:\n"
        "    n = Int32(3)\n"
        "    c = Container[Int32](None)\n"
        "    c.set(-n)\n") + _MAIN
    FREECALL = _CONTAINER + (
        "def mk() -> Int32:\n    return Int32(5)\n"
        "def f() -> None:\n"
        "    c = Container[Int32](None)\n"
        "    c.set(mk())\n") + _MAIN
    WIDE = _CONTAINER + (
        "def f() -> None:\n"
        "    c = Container[Int64](Int64(9))\n"
        "    c.set(Int64(11))\n") + _MAIN

    @pytest.mark.parametrize("src", [BINOP, UNARY, FREECALL, WIDE])
    def test_routes_byte_identical(self, src):
        _assert_routes_byte_identical(src)

    @pytest.mark.parametrize("src", [BINOP, UNARY, FREECALL])
    def test_witnesses_scalar_temp_face(self, src):
        thir, witnesses = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert witnesses.get("optptr.scalar_temp", 0) == 1

    def test_binop_emits_typed_temp(self):
        out = _cpp(self.BINOP, thir=True)
        assert "int32_t __tmp_1 = (::tpy::add_check<int32_t>(n, 1));" in out
        assert "c.set(&(__tmp_1));" in out


class TestScalarTempBoundaries:
    """Shapes at the same slot family that must keep rejecting. Each stays
    byte-identical via the AST fallback and never reaches the new face."""

    STR_INNER = _CONTAINER + (
        "def f() -> None:\n"
        "    c = Container[str](\"hi\")\n"
        "    c.set(\"yo\")\n") + _MAIN
    CHAR_INNER = _CONTAINER + (
        "def f() -> None:\n"
        "    c = Container[Char](Char(\"a\"))\n"
        "    c.set(Char(\"b\"))\n") + _MAIN
    FLOAT_LITERAL = _CONTAINER + (
        "def f() -> None:\n"
        "    c = Container[float](1.5)\n"
        "    c.set(2.5)\n") + _MAIN
    BARE_INT_LITERAL = _CONTAINER + (
        "def f() -> None:\n"
        "    c = Container[Int32](7)\n"
        "    print(1)\n") + _MAIN
    FIELD_LVALUE = _CONTAINER + (
        "class S:\n    v: Int32\n"
        "    def __init__(self, v: Int32) -> None:\n        self.v = v\n"
        "def f() -> None:\n"
        "    s = S(Int32(4))\n"
        "    c = Container[Int32](None)\n"
        "    c.set(s.v)\n") + _MAIN
    RECORD_INNER = _CONTAINER + (
        "class P:\n    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
        "def f() -> None:\n"
        "    c = Container[P](P(Int32(1)))\n"
        "    c.set(P(Int32(2)))\n") + _MAIN
    # The AST hoists on `is_temporary_expr`, this face on `is_rvalue_source`:
    # a ternary of two scalar NAMES is an rvalue source but NOT a temporary
    # (the AST takes `&(cond ? a : b)` directly), so admitting it would
    # diverge. It must stay out.
    TERNARY_SOURCE = _CONTAINER + (
        "def f() -> None:\n"
        "    a = Int32(1)\n"
        "    b = Int32(2)\n"
        "    t = True\n"
        "    c = Container[Int32](None)\n"
        "    c.set(a if t else b)\n") + _MAIN
    MATCH_GUARD = _CONTAINER + (
        "def f() -> None:\n"
        "    c = Container[Int32](None)\n"
        "    n = Int32(1)\n"
        "    match n:\n"
        "        case _ if c.probe(Int32(9)):\n            print(1)\n"
        "        case _:\n            print(2)\n") + _MAIN
    LISTCOMP_BODY = _CONTAINER + (
        "def f() -> None:\n"
        "    c = Container[Int32](None)\n"
        "    xs = [c.probe(Int32(i)) for i in range(3)]\n"
        "    print(len(xs))\n") + _MAIN

    @pytest.mark.parametrize("src", [STR_INNER, CHAR_INNER, FLOAT_LITERAL,
                                     BARE_INT_LITERAL, FIELD_LVALUE,
                                     RECORD_INNER, TERNARY_SOURCE,
                                     MATCH_GUARD])
    def test_stays_ast_byte_identical(self, src):
        _assert_byte_identical(src)
        _thir, witnesses = _lower_ctx_witnessed(src)
        assert witnesses.get("optptr.scalar_temp", 0) == 0

    def test_listcomp_body_routes_with_temp_flush(self):
        # The Array-demoted list comp's element became a flush position
        # (the array_from_index lambda flush): the scalar_temp face now
        # fires INSIDE the lambda, byte-identical to the AST's
        # per-iteration hoist.
        from .testutil import _assert_routes_byte_identical
        _thir, witnesses = _lower_ctx_witnessed(self.LISTCOMP_BODY)
        assert witnesses.get("optptr.scalar_temp", 0) >= 1
        _assert_routes_byte_identical(self.LISTCOMP_BODY)


class TestScalarTempShortCircuit:
    """This face inside an `and`/`or` RHS falls back rather than hoisting: a
    short-circuit operand evaluates conditionally, so the flush right stops at
    the logical binop (the ternary-arm fence's sibling). The face-wide fence
    and its other three faces are pinned in
    `test_thir_shortcircuit_argtemp.py`; this pin keeps the scalar face's own
    exposure covered where the face lives."""

    SRC = _CONTAINER + (
        "def g(c: Container[Int32], b: bool) -> bool:\n"
        "    return b and c.probe(Int32(1))\n"
        "def f() -> None:\n"
        "    c = Container[Int32](None)\n"
        "    print(g(c, True))\n") + _MAIN

    def test_falls_back_byte_identical(self):
        _assert_byte_identical(self.SRC)
