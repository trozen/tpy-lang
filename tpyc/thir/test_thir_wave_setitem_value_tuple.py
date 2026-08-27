"""A VALUE-tuple value slot written from a tuple LITERAL.

Borrow and storage forms coincide for a value tuple, so the spelled
brace-init stores directly with no `tuple_to_storage` lift, each element
carrying its own view->owned copy -- the setitem sibling of the value-tuple
field write. A non-literal source keeps rejecting: its copy render is
unwitnessed at this position.
"""

from .testutil import (
    _assert_rejects_at,
    _assert_routes_byte_identical,
    _compile,
    _entry,
    _lower_ctx_witnessed,
)
from ..codegen_cpp.context import CodeGenOptions


class TestValueTupleLiteralIntoDictValueSlot:
    SRC = (
        "def main() -> None:\n"
        "    d: dict[str, tuple[str, str]] = {}\n"
        "    k = \"a\"\n"
        "    v = \"b\"\n"
        "    d[k] = (k, v)\n"
        "    print(d[\"a\"][1])\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC)

    def test_witnesses_the_value_tuple_face(self):
        _thir, witnesses = _lower_ctx_witnessed(self.SRC)
        assert witnesses.get("setitem.value_tuple_literal", 0) >= 1, witnesses


class TestNonLiteralSourceKeepsRejecting:
    SRC = (
        "def main() -> None:\n"
        "    d: dict[str, tuple[str, str]] = {}\n"
        "    t: tuple[str, str] = (\"a\", \"b\")\n"
        "    d[\"x\"] = t\n"
        "    print(d[\"x\"][0])\n"
        "main()\n"
    )

    def test_rejects_at_the_value_shape(self):
        compiler, modules = _compile(self.SRC)
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   comment_line_numbers=False,
                                   thir_codegen=True))
        _assert_rejects_at(dict(compiler._thir_fallback),
                           "body:stmt.assign",
                           shape="setitem.value_tuple_value_shape")
