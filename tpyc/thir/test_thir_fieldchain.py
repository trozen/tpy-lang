"""THIR field-CHAIN read gate: a value scalar/Char/enum field read off a
receiver that is itself a value F1-record field chain (`o.mid.inner.v`,
`self.a.b`). The lowering already recurses `_lower_expr` through the receiver;
these tests pin that the gate admits the chain and the emit stays byte-
identical to the AST path, across return / local / arg / compare positions and
the self / param / narrowed-Optional bottom receivers."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import _compile, _entry, _fn, _lower_ctx, _lower_ctx_witnessed

# Three-deep value F1-record chain. `f` returns a param chain, `via_self` a
# `this->` chain, `local` binds the chain then reads it, `narrowed` reaches the
# chain through a proven Optional-ptr receiver.
_CHAIN = (
    "from tpy import Int32, Char\n"
    "class Inner:\n"
    "    v: Int32\n"
    "    c: Char\n"
    "    def __init__(self, v: Int32, c: Char):\n        self.v = v\n        self.c = c\n"
    "class Mid:\n"
    "    inner: Inner\n"
    "    def __init__(self, v: Int32, c: Char):\n        self.inner = Inner(v, c)\n"
    "class Outer:\n"
    "    mid: Mid\n"
    "    def __init__(self, v: Int32, c: Char):\n        self.mid = Mid(v, c)\n"
    "    def via_self(self) -> Int32:\n        return self.mid.inner.v\n"
    "def f(o: Outer) -> Int32:\n    return o.mid.inner.v\n"
    "def get_char(o: Outer) -> Char:\n    return o.mid.inner.c\n"
    "def local(o: Outer) -> Int32:\n"
    "    x = o.mid.inner.v\n    return x + 1\n"
    "def as_compare(o: Outer) -> bool:\n    return o.mid.inner.v > 5\n"
    "def narrowed(o: Outer | None) -> Int32:\n"
    "    if o is not None:\n        return o.mid.inner.v\n    return 0\n"
)


class TestFieldChainRead:
    def _emit(self, src: str, thir: bool) -> str:
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry,
            options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    def test_routed(self):
        thir = _lower_ctx(_CHAIN)
        for name in ("f", "get_char", "local", "as_compare", "narrowed"):
            assert _fn(thir, name) is not None, name

    def test_byte_identical(self):
        assert self._emit(_CHAIN, thir=True) == self._emit(_CHAIN, thir=False)

    def test_emit_arms(self):
        cpp = self._emit(_CHAIN, thir=True)
        assert "return o.mid.inner.v;" in cpp          # param chain, `.`
        assert "return this->mid.inner.v;" in cpp      # self chain, `this->`
        assert "int32_t x = o.mid.inner.v;" in cpp     # local bind
        assert "return o.mid.inner.c;" in cpp          # Char terminal

    def test_face_witnessed(self):
        _, witnessed = _lower_ctx_witnessed(_CHAIN)
        assert witnessed.get("field.chain_recv", 0) >= 1


class TestFieldChainRejects:
    """Chains that reach beyond the value-F1-record slice stay on the AST path
    -- the receiver widening never mis-routes an Optional / container / non-name
    intermediate that would need a deref / unwrap the mirror lacks."""

    def _rejects(self, extra_field: str, body: str) -> bool:
        src = (
            "from tpy import Int32\n"
            "class Inner:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32):\n        self.v = v\n"
            "class Mid:\n"
            f"    {extra_field}\n"
            "    def __init__(self, v: Int32):\n        self.inner = Inner(v)\n"
            "class Outer:\n"
            "    mid: Mid\n"
            "    def __init__(self, v: Int32):\n        self.mid = Mid(v)\n"
            f"{body}"
        )
        return _fn(_lower_ctx(src), "f") is None

    def test_optional_intermediate_rejects(self):
        # `o.mid.inner.v` where `inner` is `Inner | None`: the AST unwraps the
        # intermediate (`deref_check`), a shape the plain chain does not carry.
        assert self._rejects(
            "inner: Inner | None",
            "def f(o: Outer) -> Int32:\n    return o.mid.inner.v\n")
