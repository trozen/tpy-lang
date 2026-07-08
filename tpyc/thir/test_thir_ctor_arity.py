"""Ctor calls that OMIT trailing default args (`Dog("Rex")` where __init__ has
defaults). The default rides the C++ ctor signature (records.py emits it via
emit_defaults), so the call passes only the provided args -- byte-identical to
the exact-arity `Name(args)` emit. Variadic ctors and the instantiation form
stay on the AST path."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _compile, _entry, _lower_ctx, _lower_ctx_witnessed, _fn,
)

# A record whose __init__ mixes a required param with two trailing defaults
# (a negative-literal int, a str literal).
_CAT = (
    "from tpy import Int32\n"
    "class Cat:\n"
    "    a: Int32\n    b: Int32\n    c: str\n"
    "    def __init__(self, a: Int32, b: Int32 = -1, c: str = \"hi\"):\n"
    "        self.a = a\n        self.b = b\n        self.c = c\n"
)

# All-scalar defaults, so the DECL gate's scalar-only arg loop admits the
# full-arity call too (a str arg is out of that gate independent of arity).
_PT = (
    "from tpy import Int32\n"
    "class Pt:\n"
    "    a: Int32\n    b: Int32\n"
    "    def __init__(self, a: Int32, b: Int32 = -1):\n"
    "        self.a = a\n        self.b = b\n"
)


def _cpp(src: str, thir: bool) -> str:
    compiler, modules = _compile(src)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=thir))
    return hpp + cpp


class TestOmittedDefaultCtor:
    def test_omit_all_defaults_routes(self):
        src = _CAT + "def make() -> Int32:\n    x = Cat(5)\n    return x.a\n"
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        thir = _lower_ctx(src)
        assert _fn(thir, "make") is not None

    def test_omit_one_default_routes(self):
        src = _CAT + "def make() -> Int32:\n    x = Cat(5, 9)\n    return x.b\n"
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        assert _fn(_lower_ctx(src), "make") is not None

    def test_full_arity_still_routes(self):
        src = _PT + "def make() -> Int32:\n    x = Pt(5, 9)\n    return x.a\n"
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        assert _fn(_lower_ctx(src), "make") is not None

    def test_omission_witnesses_face(self):
        src = _CAT + "def make() -> Int32:\n    x = Cat(5)\n    return x.a\n"
        _thir, w = _lower_ctx_witnessed(src)
        assert w.get("ctor.omit_defaults", 0) > 0

    def test_full_arity_does_not_witness_omission(self):
        src = _PT + "def make() -> Int32:\n    x = Pt(5, 9)\n    return x.a\n"
        _thir, w = _lower_ctx_witnessed(src)
        assert w.get("ctor.omit_defaults", 0) == 0

    def test_emitted_call_passes_only_provided_args(self):
        src = _CAT + "def make() -> Int32:\n    x = Cat(5)\n    return x.a\n"
        out = _cpp(src, thir=True)
        # The default lives on the C++ ctor signature, not the call site.
        assert "Cat x = Cat(5);" in out
        assert "int32_t b = -1" in out

    def test_keyword_only_default_omitted_routes(self):
        # A keyword-only default lowers to a positional C++ default param, so the
        # omitting call is admitted like a plain trailing default.
        src = (
            "from tpy import Int32\n"
            "class K:\n    a: Int32\n    b: Int32\n"
            "    def __init__(self, a: Int32, *, b: Int32 = 7):\n"
            "        self.a = a\n        self.b = b\n"
            "def make() -> Int32:\n    x = K(1)\n    return x.b\n"
        )
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        assert _fn(_lower_ctx(src), "make") is not None

    def test_variadic_ctor_deferred(self):
        # A `*rest` slot has no positional default to fall back on -> AST.
        src = (
            "from tpy import Int32\n"
            "class Bag:\n    a: Int32\n"
            "    def __init__(self, a: Int32, *rest: Int32):\n        self.a = a\n"
            "def make() -> Int32:\n    x = Bag(1)\n    return x.a\n"
        )
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        assert _fn(_lower_ctx(src), "make") is None

    def test_nested_omitted_default_ctor_arg(self):
        # A nested ctor-rvalue arg omitting its own trailing default binds the
        # inline prvalue into the outer const slot; both bare `Cat(5)`.
        src = (
            _CAT
            + "def take(c: Cat) -> Int32:\n    return c.a\n"
            + "def make() -> Int32:\n    return take(Cat(5))\n"
        )
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        assert _fn(_lower_ctx(src), "make") is not None
