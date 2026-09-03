"""Value-repr `Optional[scalar]` LOCALS in a resumable frame.

The frame field is the same bare `std::optional<T>` a value-opt PARAM
captures, so the local joins the param's admission. Two things had to follow
it: pass 1 pre-registers every frame field in `declared`, so the decl arm
reads as a reassign and never registered the binding, and the plain
frame-field write lowered its init without the whole-optional admission the
sync decl sink threads (`v = __self.f;` is a whole-optional copy).

Truthiness of such a name routes too, now that the resumable condition
renderer applies the truthiness lowering instead of the bare value render
(it used to emit `if (v)` -- has_value only -- against the sync path's
`is_truthy`).
"""

from __future__ import annotations

import io

from .emit import emit_thir_body
from .testutil import (_compile, _entry, _assert_byte_identical,
                      _reject_tally)
from ..codegen_cpp import CodeGenOptions
from ..compilation_context import activate_compiler
from .lower import lower_module


def _reject_tags(src: str) -> dict:
    """The per-body fallback map -- a routed RESUMABLE never appears in
    `thir.functions`, so a reject pin must read the fallback keys."""
    return _reject_tally(src)


def _thir_cpp(src: str) -> str:
    compiler, modules = _compile(src)
    entry = _entry(modules)
    _, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False))
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
        assert not _reject_tags(src)
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
        assert _reject_tags(src)

    def test_truthiness_of_a_value_opt_frame_name_routes(self):
        # A resumable branch condition takes the truthiness lowering, so a
        # value-repr optional tests engaged-AND-payload like the sync path.
        # Both the param and the frame-local form must route and render it.
        param_src = ("from tpy import Int32\n"
                     "from typing import Iterator\n"
                     "def g(v: Int32 | None) -> Iterator[Int32]:\n"
                     "    if v:\n"
                     "        yield v\n"
                     "    yield -1\n")
        assert not _reject_tags(param_src)
        assert "if (::tpy::is_truthy(v))" in _thir_cpp(param_src)
        _assert_byte_identical(param_src)
        local_src = _BOX + ("    def gen(self) -> Iterator[Int32]:\n"
                            "        v = self.f\n"
                            "        if v:\n"
                            "            yield v\n"
                            "        yield -1\n")
        assert not _reject_tags(local_src)
        assert "if (::tpy::is_truthy(v))" in _thir_cpp(local_src)
        _assert_byte_identical(local_src)

    def test_none_test_keeps_its_engagement_render(self):
        # The boundary: `is not None` must stay a bare has_value test, not
        # get swept into the truthiness render by the widening above.
        src = _BOX + ("    def gen(self) -> Iterator[Int32]:\n"
                      "        v = self.f\n"
                      "        if v is not None:\n"
                      "            yield v\n"
                      "        yield -1\n")
        cpp = _thir_cpp(src)
        assert "is_truthy" not in cpp
        assert not _reject_tags(src)
