"""Value-repr `Optional[scalar]` LOCALS in a resumable frame.

The frame field is the same bare `std::optional<T>` a value-opt PARAM
captures, so the local joins the param's admission. Two things had to follow
it: pass 1 pre-registers every frame field in `declared`, so the decl arm
reads as a reassign and never registered the binding, and the plain
frame-field write lowered its init without the whole-optional admission the
sync decl sink threads (`v = __self.f;` is a whole-optional copy).

Truthiness of such a name stays AST on purpose -- the AST emits a bare
`if (v)` there (has_value only) where the sync path emits `is_truthy`; that
is a live CPython divergence tracked in BUGS.md, not a render THIR mirrors.
"""

from __future__ import annotations

import io

from .emit import emit_thir_body
from .testutil import _compile, _entry, _assert_byte_identical
from ..codegen_cpp import CodeGenOptions
from ..compilation_context import activate_compiler
from .lower import lower_module


def _fallback(src: str) -> dict:
    """The per-body fallback map -- a routed RESUMABLE never appears in
    `thir.functions`, so a reject pin must read the fallback keys."""
    compiler, modules = _compile(src)
    entry = _entry(modules)
    compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=True))
    return dict(compiler._thir_fallback)


def _thir_cpp(src: str) -> str:
    compiler, modules = _compile(src)
    entry = _entry(modules)
    _, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=True))
    return cpp


_BOX = (
    "from tpy import Int32\n"
    "from typing import Iterator\n"
    "class Box:\n"
    "    f: Int32 | None\n"
    "    s: str | None\n"
    "    def __init__(self, v: Int32 | None) -> None:\n"
    "        self.f = v\n        self.s = None\n"
)


class TestResumableValueOptLocal:
    def test_whole_optional_field_binds_the_frame_local(self):
        src = _BOX + ("    def gen(self) -> Iterator[Int32]:\n"
                      "        v = self.f\n"
                      "        if v is not None:\n"
                      "            yield v\n"
                      "        yield -1\n")
        assert not _fallback(src)
        cpp = _thir_cpp(src)
        # The whole optional copies bare; the narrowed read derefs.
        assert "v = __self.f;" in cpp
        assert "return (*v);" in cpp
        _assert_byte_identical(src)

    def test_owned_view_optional_local_still_defers(self):
        # Only the SCALAR family is registered -- an owned-view optional
        # (`str | None`) frame local keeps its own rung.
        src = _BOX + ("    def gen(self) -> Iterator[Int32]:\n"
                      "        t = self.s\n"
                      "        if t is not None:\n"
                      "            yield len(t)\n"
                      "        yield -1\n")
        assert _fallback(src)

    def test_truthiness_of_a_value_opt_frame_name_stays_ast(self):
        # BUGS.md: the AST renders `if (v)` (has_value) here, which disagrees
        # with Python for a falsy payload. Both the param and the local form
        # must keep rejecting until that is fixed.
        param_src = ("from tpy import Int32\n"
                     "from typing import Iterator\n"
                     "def g(v: Int32 | None) -> Iterator[Int32]:\n"
                     "    if v:\n"
                     "        yield v\n"
                     "    yield -1\n")
        # The statement-level key is what survives (the truthiness detail is
        # swallowed by the condition's own reject reason).
        assert list(_fallback(param_src)) == ["resumable:res.cond"]
        local_src = _BOX + ("    def gen(self) -> Iterator[Int32]:\n"
                            "        v = self.f\n"
                            "        if v:\n"
                            "            yield v\n"
                            "        yield -1\n")
        assert list(_fallback(local_src)) == ["resumable:res.cond"]
        # ... and the same body with an explicit None test DOES route, so the
        # reject above is the truthiness render, not the binding.
        none_test = local_src.replace("        if v:\n",
                                      "        if v is not None:\n")
        assert not _fallback(none_test)
