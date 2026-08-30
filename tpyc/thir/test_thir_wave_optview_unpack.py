"""Tuple unpack over an owned-inner `Optional[str]` / `Optional[bytes]`
element (`body, ctype = encode(...)` at `tuple[bytes | None, str | None]`).

The element is owned inside the tuple's storage, so the whole optional
copies out by value like a scalar and the target is a plain typed copy. The
target is registered VIEW-kind, without which a narrowed read renders the
bare name where the AST derefs. A VIEW-INNER optional element
(`StrView | None`) stays out: its copy would alias the source tuple.

Corpus witness: tplib.requests `_request_on`.
"""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical, _assert_rejects_at,
    _assert_routes_byte_identical, _compile, _entry, _fn, _lower_ctx,
)
from ..codegen_cpp import CodeGenOptions


def _emit(src: str):
    compiler, modules = _compile(src)
    hpp, cpp = compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False,
                                                thir_codegen=True))
    return compiler, hpp + cpp


_SINKS = (
    "from tpy import Int32, StrView\n"
    "def sink(v: str | None) -> Int32:\n"
    "    return 1 if v is not None else 0\n"
    "def bsink(v: bytes | None) -> Int32:\n"
    "    return 1 if v is not None else 0\n"
)


class TestOptViewElementUnpack:
    def test_name_source_routes_with_derefed_reads(self):
        src = (_SINKS
               + "def use(t: tuple[bytes | None, str | None]) -> Int32:\n"
               + "    body, ctype = t\n"
               + "    n = 0\n"
               + "    if ctype is not None:\n"
               + "        n += Int32(len(ctype))\n"
               + "    if body is not None:\n"
               + "        n += Int32(len(body))\n"
               + "    return n + sink(ctype) + bsink(body)\n")
        _assert_routes_byte_identical(src)
        _, code = _emit(src)
        assert ("std::optional<std::vector<uint8_t>> body = "
                "std::get<0>(__tup_1);" in code)
        assert "std::optional<std::string> ctype = std::get<1>(__tup_1);" \
            in code
        # The VIEW-kind registration: a narrowed read must deref.
        assert "::tpy::__len__((*ctype))" in code
        assert "::tpy::__len__((*body))" in code

    def test_for_each_unpack_routes_with_derefed_reads(self):
        src = (_SINKS
               + "def use(rows: list[tuple[bytes | None, str | None]]"
                 ") -> Int32:\n"
               + "    n = 0\n"
               + "    for body, ctype in rows:\n"
               + "        if ctype is not None:\n"
               + "            n += Int32(len(ctype))\n"
               + "        n += sink(ctype)\n"
               + "    return n\n")
        _assert_routes_byte_identical(src)
        _, code = _emit(src)
        assert "::tpy::__len__((*ctype))" in code

    def test_call_source_routes(self):
        # An `Own[tuple[..]]`-declared callee, matching the corpus witness:
        # the owning return binds the same `auto __tup_N = <call>;` capture.
        src = (_SINKS
               + "from tpy import Own\n"
               + "def encode(k: Int32) -> Own[tuple[bytes | None, str | None]]:\n"
               + "    if k > 0:\n"
               + "        return (b\"x\", \"text\")\n"
               + "    return (None, None)\n"
               + "def use(k: Int32) -> Int32:\n"
               + "    body, ctype = encode(k)\n"
               + "    return sink(ctype) + bsink(body)\n")
        # Module-wide routing cannot be claimed here -- the CALLEE's own
        # `return (None, None)` at an Own[tuple] slot is a different,
        # still-unrouted shape -- so the unpack's routing is asserted on the
        # consuming function itself.
        _assert_byte_identical(src)
        assert _fn(_lower_ctx(src), "use") is not None
        _, code = _emit(src)
        assert "auto __tup_1 = encode(k);" in code

    def test_view_inner_optional_element_stays_ast(self):
        # BOUNDARY (dualgen-probed): a `StrView | None` element holds a view
        # into somebody else's buffer, so the by-value target copy is not the
        # same decision and must not inherit this admission.
        src = (_SINKS
               + "def use(t: tuple[StrView | None, Int32]) -> Int32:\n"
               + "    s, k = t\n"
               + "    if s is not None:\n"
               + "        return k + Int32(len(s))\n"
               + "    return k\n")
        compiler, _ = _emit(src)
        _assert_rejects_at(compiler._thir_fallback, "body:stmt.tuple_unpack")
        _assert_byte_identical(src)
