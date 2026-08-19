"""THIR conditional expression (`a if c else b` -> `((c) ? (a) : (b))`):
routing, arm literal retypes, the str view/owned arm handling, the bool
condition position, and the gate-rejected shapes."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from ..type_def_registry import is_big_int_type, is_float32_type
from .nodes import (
    Form, THIRFormConvert, THIRIf, THIRIfExpr, THIRLiteral, THIRName,
    THIRReturn, THIRStrLiteral, THIRVarDecl, THIRWhile,
)
from .testutil import (
    _compile, _entry, _fn, _lower, _lower_ctx, _lower_ctx_witnessed,
    _assert_byte_identical, _assert_routes_byte_identical,
)


class TestIfExprRouting:
    def test_scalar_decl_reassign_return(self):
        thir = _lower(
            "from tpy import Int32\n"
            "def f(c: bool, a: Int32, b: Int32) -> Int32:\n"
            "    x = a if c else b\n"
            "    x = b if c else a\n"
            "    return a if c else b\n")
        body = _fn(thir, "f").body
        decl = body[0]
        assert isinstance(decl, THIRVarDecl)
        assert isinstance(decl.init, THIRIfExpr)
        assert decl.init.form is Form.VALUE
        assert isinstance(decl.init.cond, THIRName)
        assert isinstance(body[2].value, THIRIfExpr)

    def test_literal_arm_retypes_to_bigint_slot(self):
        # `1 if c else n` at a BigInt result: the literal arm carries the
        # BigInt result type so the emitter picks the ctor wrap
        # (`::tpy::BigInt(1)`), mirroring branch_target = result_type.
        thir = _lower(
            "def f(c: bool, n: int) -> int:\n"
            "    return 1 if c else n\n")
        ret = _fn(thir, "f").body[0]
        assert isinstance(ret.value, THIRIfExpr)
        lit = ret.value.then
        assert isinstance(lit, THIRLiteral)
        assert is_big_int_type(lit.result_type)

    def test_literal_arm_retypes_to_float32_slot(self):
        thir = _lower(
            "from tpy import Float32\n"
            "def f(c: bool, g: Float32) -> Float32:\n"
            "    y = 1.5 if c else g\n"
            "    return y\n")
        decl = _fn(thir, "f").body[0]
        lit = decl.init.then
        assert isinstance(lit, THIRLiteral)
        assert is_float32_type(lit.result_type)

    def test_literal_literal_default_int_bigint(self):
        # Under a BigInt module default, `1 if c else 2` resolves BigInt and
        # both arms take the ctor wrap -- the resolved-default mirror of
        # get_resolved_type's literal fallback.
        thir = _lower(
            "def f(c: bool) -> None:\n"
            "    x = 1 if c else 2\n"
            "    print(x)\n", default_int="BigInt")
        decl = _fn(thir, "f").body[0]
        assert is_big_int_type(decl.init.then.result_type)
        assert is_big_int_type(decl.init.orelse.result_type)

    def test_nested_ternary_routes(self):
        thir = _lower(
            "from tpy import Int32\n"
            "def f(c: bool, d: bool, a: Int32, b: Int32) -> Int32:\n"
            "    return a if c else (b if d else a)\n")
        ret = _fn(thir, "f").body[0]
        assert isinstance(ret.value, THIRIfExpr)
        assert isinstance(ret.value.orelse, THIRIfExpr)

    def test_chained_compare_condition(self):
        thir = _lower(
            "from tpy import Int32\n"
            "def f(a: Int32, b: Int32) -> Int32:\n"
            "    return a if a < b <= 10 else b\n")
        assert _fn(thir, "f") is not None

    def test_bool_ternary_as_condition(self):
        # `while d if c else False:` -- a bool result's truthiness render is
        # its value render, so the condition position routes; the witness pins
        # the gate face.
        thir, faces = _lower_ctx_witnessed(
            "def f(c: bool, d: bool) -> Int32:\n"
            "    while d if c else False:\n"
            "        return 1\n"
            "    if d if c else d:\n"
            "        return 2\n"
            "    return 0\n"
            "from tpy import Int32\n")
        body = _fn(thir, "f").body
        assert isinstance(body[0], THIRWhile)
        assert isinstance(body[0].condition, THIRIfExpr)
        assert isinstance(body[1], THIRIf)
        assert faces.get("ifexpr.cond_pos", 0) >= 2

    def test_enum_result_routes(self):
        thir = _lower_ctx(
            "from enum import Enum\n"
            "class Color(Enum):\n"
            "    RED = 1\n"
            "    GREEN = 2\n"
            "def f(c: bool) -> Color:\n"
            "    e = Color.RED if c else Color.GREEN\n"
            "    return e\n")
        decl = _fn(thir, "f").body[0]
        assert isinstance(decl.init, THIRIfExpr)
        assert decl.init.form is Form.VALUE


class TestIfExprStrForms:
    def test_both_view_arms_borrow_form_and_return_wrap(self):
        # Both arms are runtime views -> the ternary is a view source
        # (BORROW): the owned return copies the WHOLE ternary
        # (`return std::string(((c) ? (a) : (b)));`).
        thir = _lower(
            "def f(c: bool, a: str, b: str) -> str:\n"
            "    return a if c else b\n")
        ret = _fn(thir, "f").body[0]
        assert isinstance(ret.value, THIRFormConvert)
        assert ret.value.form is Form.STORAGE
        assert isinstance(ret.value.value, THIRIfExpr)
        assert ret.value.value.form is Form.BORROW

    def test_view_and_literal_arms_stay_bare(self):
        # A str literal is const char* in ternary context: no per-arm wrap,
        # and the pair still counts as a view source (BORROW) -- a view
        # binding takes it bare.
        thir = _lower(
            "def f(c: bool, a: str) -> None:\n"
            '    t = a if c else "fallback"\n'
            "    print(t)\n")
        decl = _fn(thir, "f").body[0]
        assert decl.resolved_type.to_cpp() == "std::string_view"
        assert isinstance(decl.init, THIRIfExpr)
        assert decl.init.form is Form.BORROW
        assert isinstance(decl.init.orelse, THIRStrLiteral)

    def test_mixed_arms_wrap_view_arm(self):
        # A view param opposite an owned local: the view arm materializes
        # (`std::string(a)`) so the C++ ternary deduces std::string; the
        # result is an owned rvalue (STORAGE) landing bare at the owned decl.
        thir, faces = _lower_ctx_witnessed(
            "def f(c: bool, a: str, n: Int32) -> str:\n"
            '    u = f"v{n}"\n'
            "    r = a if c else u\n"
            "    return r\n"
            "from tpy import Int32\n")
        decl = _fn(thir, "f").body[1]
        assert isinstance(decl, THIRVarDecl)
        te = decl.init
        assert isinstance(te, THIRIfExpr)
        assert te.form is Form.STORAGE
        assert isinstance(te.then, THIRFormConvert)
        assert te.then.form is Form.STORAGE
        assert isinstance(te.orelse, THIRName)  # the owned arm stays bare
        assert faces.get("ifexpr.str_mixed", 0) == 1

    def test_owned_and_literal_arms_no_wrap_storage(self):
        # owned f-string arm + literal arm: mixed view-ness but the literal
        # is exempt -> no wrap anywhere; the result is owned (STORAGE), so
        # the owned return takes it bare.
        thir = _lower(
            "def f(c: bool, a: str, n: Int32) -> str:\n"
            '    return f"n{n}" if c else "lit"\n'
            "from tpy import Int32\n")
        ret = _fn(thir, "f").body[0]
        assert isinstance(ret.value, THIRIfExpr)
        assert ret.value.form is Form.STORAGE


class TestIfExprRejects:
    def test_value_opt_scalar_name_arm_routes(self):
        # RE-PINNED ROUTED: a VALUE-repr Optional result wraps every arm in the
        # spelled `std::optional<T>(...)`, and a scalar name renders bare under
        # that wrap like a literal does. The POINTER-repr Optional (a record
        # result) is the ptr-lift family, pinned separately below.
        src = ("from tpy import Int32\n"
               "def f(c: bool, a: Int32) -> Int32 | None:\n"
               "    return a if c else None\n"
               "def main() -> None:\n"
               "    print(f(True, 1))\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        cpp = _assert_routes_byte_identical(src)
        assert "std::optional<int32_t>(a)" in cpp[1]

    def test_value_opt_call_arm_rejected(self):
        # BOUNDARY: the value-opt arm slice is literals plus a scalar NAME --
        # a CALL arm keeps the named reject.
        src = ("from tpy import Int32\n"
               "def one() -> Int32:\n"
               "    return 1\n"
               "def f(c: bool) -> Int32 | None:\n"
               "    return one() if c else None\n"
               "def main() -> None:\n"
               "    print(f(True))\n"
               "main()\n")
        assert _fn(_lower_ctx(src), "f") is None
        _assert_byte_identical(src)

    def test_container_result_routes(self):
        # RE-PINNED ROUTED (decl-slot track): the container ternary renders
        # bare with spelled list-literal arms; the all-rvalue decl is the
        # plain copy (`std::vector<int32_t> xs = ((c) ? (...) : (...));`).
        src = ("def f(c: bool) -> None:\n"
               "    xs = [1] if c else [2]\n"
               "    print(xs[0])\n"
               "f(True)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)

    def test_bytes_result_rejected(self):
        # A PLAIN bytes-param arm + literal mix must keep deferring: the
        # AST emit for it is uncompilable (span-vs-vector ternary, BUGS.md)
        # -- only the value-opt-param flavor routes (ifexpr.bytes_view_lit).
        thir = _lower(
            "def f(c: bool, b: bytes) -> bytes:\n"
            '    return b if c else b"x"\n')
        assert _fn(thir, "f") is None

    def test_nonbool_ternary_condition_rejected(self):
        # `if a if c else b:` (int truthiness) stays AST, mirroring the
        # condition name arm's bool pin.
        thir = _lower(
            "from tpy import Int32\n"
            "def f(c: bool, a: Int32, b: Int32) -> Int32:\n"
            "    if a if c else b:\n"
            "        return 1\n"
            "    return 0\n")
        assert _fn(thir, "f") is None

    def test_isinstance_condition_routes(self):
        # The isinstance-ternary arm carries the condition-scoped inline
        # facts (2-member subject, scalar result); PLAIN-VAR arms (no
        # subject reads) route too and must stay byte-identical. The
        # wide-union and non-scalar boundaries are pinned in
        # test_thir_unions.
        _assert_routes_byte_identical(
            "from tpy import Int32\n"
            "class A:\n"
            "    n: Int32\n"
            "    def __init__(self):\n        self.n = 0\n"
            "class B:\n"
            "    n: Int32\n"
            "    def __init__(self):\n        self.n = 1\n"
            "def f(v: A | B, x: Int32, y: Int32) -> Int32:\n"
            "    return x if isinstance(v, A) else y\n"
            "def main() -> None:\n"
            "    print(f(A(), 1, 2))\n"
            "    print(f(B(), 1, 2))\n"
            "main()\n")


class TestIfExprEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return cpp

    SRC = (
        "from enum import Enum\n"
        "from tpy import Float32, Int32\n"
        "class Color(Enum):\n"
        "    RED = 1\n"
        "    GREEN = 2\n"
        "def pick(c: bool, a: Int32, b: Int32) -> Int32:\n"
        "    x = a if c else b\n"
        "    x = b if c else a\n"
        "    return a if c else b\n"
        "def lit(c: bool) -> Int32:\n"
        "    return 1 if c else 2\n"
        "def fl(c: bool, g: Float32) -> Float32:\n"
        "    y = 1.5 if c else g\n"
        "    return y\n"
        "def big(c: bool, n: int) -> int:\n"
        "    return 1 if c else n\n"
        "def use(c: bool, a: Int32) -> Int32:\n"
        "    return pick(c, a if c else 1, a)\n"
        "def nested(c: bool, d: bool, a: Int32, b: Int32) -> Int32:\n"
        "    return a if c else (b if d else a)\n"
        "def condops(a: Int32, b: Int32) -> Int32:\n"
        "    return a if a < b <= 10 else b\n"
        "def views(c: bool, a: str, b: str) -> str:\n"
        "    s = a if c else b\n"
        "    return s\n"
        "def owned_ret(c: bool, a: str, b: str) -> str:\n"
        "    return a if c else b\n"
        "def mixed(c: bool, a: str, n: Int32) -> str:\n"
        '    u = f"v{n}"\n'
        "    r = a if c else u\n"
        "    return r\n"
        "def litmix(c: bool, a: str) -> None:\n"
        '    t = a if c else "fallback"\n'
        "    print(t)\n"
        "def fstr(c: bool, a: str) -> str:\n"
        "    return f\"v={a if c else 'x'}\"\n"
        "def as_cond(c: bool, d: bool) -> Int32:\n"
        "    while d if c else False:\n"
        "        return 2\n"
        "    return 0\n"
        "def as_operand(c: bool, a: Int32, b: Int32) -> bool:\n"
        "    return (a if c else b) > 3\n"
        "def enum_pick(c: bool) -> Color:\n"
        "    e = Color.RED if c else Color.GREEN\n"
        "    return e\n"
        "def main() -> None:\n"
        "    print(pick(True, 1, 2), lit(False), fl(True, 2.0), big(False, 7))\n"
        "    print(use(False, 5), nested(True, False, 1, 2), condops(3, 4))\n"
        '    print(views(True, "p", "q"), owned_ret(False, "r", "s"))\n'
        '    print(mixed(True, "t", 1), fstr(False, "u"))\n'
        '    litmix(True, "v")\n'
        "    print(as_cond(True, False), as_operand(False, 1, 2))\n"
        "    print(enum_pick(True).value)\n"
        "main()\n"
    )

    def test_ifexpr_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        for name in ("pick", "lit", "fl", "big", "use", "nested", "condops",
                     "views", "owned_ret", "mixed", "litmix", "fstr",
                     "as_cond", "as_operand", "enum_pick", "main"):
            assert _fn(thir, name) is not None, name
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_ternary_renders(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "int32_t x = ((c) ? (a) : (b));" in cpp
        assert "return ((c) ? (::tpy::BigInt(1)) : (n));" in cpp
        assert "float y = ((c) ? (1.5f) : (g));" in cpp
        assert "return std::string(((c) ? (a) : (b)));" in cpp
        assert "std::string r = ((c) ? (std::string(a)) : (u));" in cpp
        assert "while (((c) ? (d) : (false)))" in cpp
        assert "return ((((a < b) && (b <= 10))) ? (a) : (b));" in cpp
