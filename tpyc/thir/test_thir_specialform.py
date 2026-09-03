"""THIR special-form callee pass-throughs: `typing.cast(T, x)` on a non-Any
source (a compile-time no-op rendering the bare source) and a call whose sema
attached a `macro_expansion` replacement (getattr/hasattr/@call_macro -- the
call node renders its expansion in place). Both reuse existing value-expr
lowering; the Any cast (any_cast_or_panic wrap) stays on the AST path."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import _compile, _entry, _fn, _lower_ctx_witnessed


def _cpp(src: str):
    compiler, modules = _compile(src)
    entry = _entry(modules)
    _, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False))
    return cpp


class TestCastPassthrough:
    SRC = (
        "from typing import cast\n"
        "def f(y: int) -> int:\n"
        "    x = cast(int, y)\n"
        "    return x\n"
        "def main():\n    print(f(5))\nmain()\n"
    )

    def test_routed_and_witnessed(self):
        thir, wit = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "f") is not None
        assert wit.get("call.cast_passthrough", 0) >= 1

    def test_emit_bare_source(self):
        cpp = _cpp(self.SRC)
        # The cast is erased: the decl init is the bare source, no cast wrap.
        assert "::tpy::BigInt x = y;" in cpp
        assert "cast" not in cpp


class TestCastAnySourceIneligible:
    # A cast whose SOURCE is Any renders a real `any_cast_or_panic<T>` wrap, not
    # a no-op, so the special-form passthrough must NOT fire -- the body stays
    # byte-identical on the AST path.
    SRC = (
        "from typing import cast, Any\n"
        "def f(y: Any) -> int:\n"
        "    x = cast(int, y)\n"
        "    return x\n"
        "def main():\n    print(f(5))\nmain()\n"
    )

    def test_not_witnessed_as_passthrough(self):
        _, wit = _lower_ctx_witnessed(self.SRC)
        assert wit.get("call.cast_passthrough", 0) == 0

class TestMacroExpansion:
    # hasattr on a declared field static-folds to a bool literal; the call node
    # carries that folded expr as `macro_expansion`.
    SRC = (
        "class P:\n"
        "    x: int\n"
        "    def __init__(self, x: int) -> None:\n        self.x = x\n"
        "def f(p: P) -> bool:\n"
        "    b = hasattr(p, \"x\")\n"
        "    return b\n"
        "def main():\n    print(f(P(3)))\nmain()\n"
    )

    def test_routed_and_witnessed(self):
        thir, wit = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "f") is not None
        assert wit.get("call.macro_expansion", 0) >= 1

    def test_emit_folded_literal(self):
        cpp = _cpp(self.SRC)
        assert "bool b = true;" in cpp
