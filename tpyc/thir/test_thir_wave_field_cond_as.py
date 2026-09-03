"""`field=<literal> as name` sub-patterns in a class pattern.

The form carries two independent renders: the literal's `==` condition on the
tested field, and an `as` name aliasing that same field. They compose the way
the AST's separate condition and binding walks do -- the condition around the
tier's runtime base, the binding around the binding base -- so the arm admits
only when BOTH answers are eligible.

The `as` name takes the plain capture's binding shapes (`auto&` alias, a copy
for a by-value bind, an assignment into a pre-declared local). The hoisted
pointer and frame-slot flavors cannot occur here rather than being refused:
the literal condition constrains the field to a scalar / str / value-optional
type, and every one of those routes as a plain-value hoist.
"""

from __future__ import annotations

import io

from .emit import emit_thir_body
from .testutil import (
    _reject_tally, _lower_ctx, _fn, _assert_routes_byte_identical,
                       _compile, _entry)
from ..codegen_cpp import CodeGenOptions


def _body(thir, name: str) -> str:
    buf = io.StringIO()
    emit_thir_body(buf, _fn(thir, name))
    return buf.getvalue()


def _reject_tags(src: str) -> dict:
    return _reject_tally(src)


_REC = (
    "from tpy import Int32, readonly\n"
    "class P:\n"
    "    n: Int32\n"
    "    s: readonly[str]\n"
    "    opt: Int32 | None\n"
    "    def __init__(self, n: Int32, s: str, opt: Int32 | None) -> None:\n"
    "        self.n = n\n"
    "        self.s = s\n"
    "        self.opt = opt\n"
)


class TestFieldCondAsRoutes:
    def test_scalar_field_cond_as_binds_alias(self):
        src = _REC + ("def f(p: P) -> Int32:\n"
                      "    match p:\n"
                      "        case P(n=3 as v):\n"
                      "            return v\n"
                      "        case _:\n"
                      "            return -1\n")
        body = _body(_lower_ctx(src), "f")
        assert "__match_subject_1.n == 3" in body
        assert "auto v = __match_subject_1.n;" in body
        _assert_routes_byte_identical(src)

    def test_readonly_str_field_cond_as(self):
        src = _REC + ('def f(p: P) -> str:\n'
                      '    match p:\n'
                      '        case P(s="hi" as v):\n'
                      '            return "x" + v\n'
                      '        case _:\n'
                      '            return "other"\n')
        _assert_routes_byte_identical(src)

    def test_value_opt_field_cond_as_reads_through_binding(self):
        # The `as` name aliases the whole `std::optional` field, so the body's
        # None-test has to ride the value-opt binding registration.
        src = _REC + ("def f(p: P) -> Int32:\n"
                      "    match p:\n"
                      "        case P(opt=3 as v):\n"
                      "            return 1 if v is not None else 0\n"
                      "        case _:\n"
                      "            return -1\n")
        body = _body(_lower_ctx(src), "f")
        assert "v.has_value()" in body
        _assert_routes_byte_identical(src)

    def test_cond_as_alongside_plain_cond(self):
        src = _REC + ("def f(p: P) -> Int32:\n"
                      "    match p:\n"
                      "        case P(n=3, s=\"hi\" as v):\n"
                      "            return Int32(len(v))\n"
                      "        case _:\n"
                      "            return -1\n")
        _assert_routes_byte_identical(src)


class TestFieldCondAsBoundaries:
    def test_wildcard_as_field_still_defers(self):
        # `_ as x` is the AST's double-bind shape, not a condition: it stays
        # on its own rung. It matches every value, so it admits no later arm.
        src = _REC + ("def f(p: P) -> Int32:\n"
                      "    match p:\n"
                      "        case P(n=_ as v):\n"
                      "            return v\n"
                      "    return -1\n")
        assert "body:stmt.match" in _reject_tags(src)

    def test_or_alternative_cond_as_still_defers(self):
        # The or-pattern alternative walk admits bare literal conditions only;
        # an alternative binding a name has no mirrored render.
        src = _REC + ("def f(p: P) -> Int32:\n"
                      "    match p:\n"
                      "        case P(n=3 as v) | P(n=4 as v):\n"
                      "            return v\n"
                      "        case _:\n"
                      "            return -1\n")
        assert "body:stmt.match" in _reject_tags(src)
