"""A VALUE-tuple local hoisted out of a branch chain.

The predecl is the same default-constructed plain-value slot every scalar /
str / value-record hoist takes (`std::tuple<std::string, int32_t> peer;`),
and the later binds ride the value-tuple reassign rows. A tuple the value
family does not carry keeps rejecting at the hoist.
"""

from .testutil import (
    _assert_rejects_at,
    _assert_routes_byte_identical,
    _compile,
    _entry,
)
from ..codegen_cpp.context import CodeGenOptions

_SRC = (
    "from tpy import Int32\n"
    "def probe(n: Int32) -> Int32:\n"
    "    if n < 0:\n"
    "        raise ValueError(\"neg\")\n"
    "    return n\n"
    "def run(n: Int32) -> {ret}:\n"
    # `pair` is bound inside the try and read after it, so the chain head
    # predecls it.
    "    try:\n"
    "        pair = ({elem}, probe(n))\n"
    "    except ValueError:\n"
    "        return {fail}\n"
    "    return {read}\n"
    "def main() -> None:\n"
    "    print(run(1))\n"
    "main()\n"
)

VALUE_TUPLE = _SRC.format(ret="str", elem="\"ok\"", fail="\"bad\"",
                          read="pair[0]")
# A NESTED tuple element is outside the value-tuple family: its predecl is
# not the plain default-constructed slot.
NESTED_TUPLE = _SRC.format(ret="Int32", elem="(1, 2)", fail="-1",
                           read="pair[1]")


def _fallback(src: str) -> dict:
    compiler, modules = _compile(src)
    compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=True,
                                                thir_codegen=True))
    return dict(compiler._thir_fallback)


class TestValueTupleTryHoist:
    def test_routes_byte_identical(self):
        out = "".join(_assert_routes_byte_identical(VALUE_TUPLE))
        assert "std::tuple<std::string, int32_t> pair;" in out
        # The bind is an assign into the predeclared slot, not a decl.
        assert "pair = std::tuple<std::string, int32_t>{" in out


class TestNestedTupleHoistKeepsRejecting:
    def test_rejects_at_the_try_hoist(self):
        _assert_rejects_at(_fallback(NESTED_TUPLE), "body:stmt.try",
                           shape="try.hoist", count=1)
