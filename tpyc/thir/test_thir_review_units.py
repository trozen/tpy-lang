"""THIR branch-integration review units: ctor-rvalue arg gates,
Array[str, N] literals, the String-init identity pin."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .nodes import (
    THIRCall, THIRCoerce, THIRContainerLiteral, THIRCtorCall, THIRFormConvert,
)
from .testutil import (
    _compile, _entry, _lower, _lower_ctx, _fn,
)

# --- Branch-integration review units: ctor-rvalue arg gates, Array[str, N] ---
# --- literals, and the String-init identity pin ---


class TestIntegrationReviewUnits:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return cpp

    def test_float_literal_ctor_rvalue_routes(self):
        # A bare float literal into a double ctor slot passes through on the
        # F2d rebind-slot rvalue source, like a free-call arg (incr 45).
        src = (
            "from tpy import Float64\n"
            "class P:\n"
            "    x: Float64\n"
            "    def __init__(self, x: Float64):\n        self.x = x\n"
            "def f() -> Float64:\n"
            "    p = P(1.5)\n    a = p.x\n    p = P(2.5)\n    return p.x + a\n")
        thir = _lower_ctx(src)
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl.init, THIRCtorCall) and decl.init.type_cpp == "P"
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_omitted_default_ctor_rvalue_ineligible(self):
        # An omitted default is synthesized by the AST arg emit; the bare
        # THIRCall does not reproduce it -> the fi/arity gate rejects.
        src = (
            "from tpy import Int32\n"
            "class Q:\n"
            "    a: Int32\n"
            "    b: Int32\n"
            "    def __init__(self, a: Int32, b: Int32 = 2):\n"
            "        self.a = a\n        self.b = b\n"
            "def f() -> Int32:\n"
            "    q = Q(1)\n    x = q.a\n    q = Q(3)\n    return x + q.a\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None

    def test_array_str_literal_routes(self):
        # A read-only str-element list literal demotes to Array[str, N] (the
        # S5 Array-family widening); the view-form param element takes the
        # per-slot owned copy.
        src = (
            "def f(s: str) -> None:\n"
            '    ys = ["a", s]\n'
            "    print(ys[0])\n")
        thir = _lower(src)
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl.init, THIRContainerLiteral)
        assert decl.resolved_type.name == "Array"
        assert isinstance(decl.init.elements[1], THIRFormConvert)
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_string_init_identity_mirrors_ast_miscompile(self):
        # `m: String = s` (a view-form source) is IDENTITY at INIT on both
        # paths and emits `std::string m = s;` -- ill-formed C++ (the
        # string_view ctor is explicit): a PRE-EXISTING AST miscompile, see
        # BUGS.md. THIR mirrors it byte-identically; when the AST arm is
        # fixed, this pin and the byte-diff flag the lockstep update.
        src = (
            "from tpy import String\n"
            "def f(s: str) -> None:\n    m: String = s\n    print(m)\n")
        thir = _lower(src)
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl.init, THIRCoerce)
        assert decl.init.coercion_name == "str_to_string"
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
