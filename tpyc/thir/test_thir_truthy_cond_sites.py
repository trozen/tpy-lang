"""Truthiness at the three non-`if`-statement boundary contexts.

The resumable CFG branch, the simple-generator while head, and a `match`
case guard are all boolean contexts that used to render the operand raw.
THIR always lowered the first two through the truthiness lowering, so the
pins here are what proves the two paths now agree rather than THIR quietly
mirroring a raw render. The guard is the boundary: THIR still rejects a
non-bool one, so a future widening has to bring its own truthiness render.
"""

from __future__ import annotations

from .testutil import _compile, _entry, _assert_byte_identical
from ..codegen_cpp import CodeGenOptions


def _fallback(src: str) -> dict:
    compiler, modules = _compile(src)
    entry = _entry(modules)
    compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=True))
    return dict(compiler._thir_fallback)


def _thir_cpp(src: str) -> str:
    """Header AND source -- a simple generator's lambda (and so its while
    head) is emitted inline in the header, not the .cpp."""
    compiler, modules = _compile(src)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=True))
    return hpp + cpp


_PRE = "from tpy import Int32\nfrom typing import Iterator\n"


class TestSimpleGeneratorWhileHead:
    def test_container_head_takes_the_len_dispatch(self):
        # The peephole shape: the while IS the last statement, so the lambda
        # renderer -- not the CFG one -- produces this head.
        src = _PRE + ("def drain(xs: list[Int32]) -> Iterator[Int32]:\n"
                      "    while xs:\n"
                      "        yield xs.pop()\n")
        assert not _fallback(src)
        assert "::tpy::__len__(xs) != 0" in _thir_cpp(src)
        _assert_byte_identical(src)

    def test_str_head_takes_the_empty_dispatch(self):
        src = _PRE + ("def chew(t: str) -> Iterator[Int32]:\n"
                      "    while t:\n"
                      "        yield 1\n"
                      "        t = \"\"\n")
        assert "(!t.empty())" in _thir_cpp(src)
        _assert_byte_identical(src)

    def test_bool_head_keeps_its_bare_render(self):
        # The boundary: a genuinely bool condition must not pick up a wrap.
        src = _PRE + ("def count(n: Int32) -> Iterator[Int32]:\n"
                      "    while n > 0:\n"
                      "        yield n\n"
                      "        n -= 1\n")
        cpp = _thir_cpp(src)
        assert "while ((n > 0))" in cpp
        assert "is_truthy" not in cpp
        _assert_byte_identical(src)


class TestResumableBranchHead:
    def test_container_branch_takes_the_len_dispatch(self):
        src = _PRE + ("def g(xs: list[Int32]) -> Iterator[Int32]:\n"
                      "    if xs:\n"
                      "        yield 1\n"
                      "    yield 2\n")
        assert not _fallback(src)
        assert "if ((::tpy::__len__(xs) != 0))" in _thir_cpp(src)
        _assert_byte_identical(src)

    def test_negated_optional_branch_routes(self):
        src = _PRE + ("def g(v: Int32 | None) -> Iterator[Int32]:\n"
                      "    if not v:\n"
                      "        yield 1\n"
                      "    yield 2\n")
        assert not _fallback(src)
        assert "if ((!(::tpy::is_truthy(v))))" in _thir_cpp(src)
        _assert_byte_identical(src)


class TestMatchGuard:
    def test_bool_guard_routes_and_renders_bare(self):
        src = _PRE + ("def f(k: Int32, n: Int32) -> Int32:\n"
                      "    match k:\n"
                      "        case 1 if n > 0:\n"
                      "            return 10\n"
                      "        case _:\n"
                      "            return 20\n")
        cpp = _thir_cpp(src)
        assert "(n > 0)" in cpp
        _assert_byte_identical(src)

    def test_non_bool_guard_still_defers(self):
        # THIR's guard lowering demands a bool result. The AST now renders
        # the truthiness dispatch here, so a widening must reproduce THAT,
        # not the old bare operand.
        src = _PRE + ("def f(k: Int32, xs: list[Int32]) -> Int32:\n"
                      "    match k:\n"
                      "        case 1 if xs:\n"
                      "            return 10\n"
                      "        case _:\n"
                      "            return 20\n")
        assert _fallback(src) == {"body:stmt.match:match.guard_type": 1}
        assert "::tpy::__len__(xs) != 0" in _thir_cpp(src)
        _assert_byte_identical(src)
