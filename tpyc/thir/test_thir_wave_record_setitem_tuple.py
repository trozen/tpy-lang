"""An owned-str tuple element written through a user record's `__setitem__`.

`self[pair[0]] = pair[1]` on `tuple[str, str]`: the element is a
`std::string` held in the tuple's own storage, so `std::get<N>(pair)` is an
owned lvalue that binds the str slot bare. A view-form NAME source still
takes the AST's view->owned copy and keeps rejecting.
"""

from .testutil import (
    _assert_rejects_at,
    _assert_routes_byte_identical,
    _compile,
    _entry,
)
from ..codegen_cpp.context import CodeGenOptions

_REC = (
    "from tpy import Int32\n"
    "class H:\n"
    "    _d: dict[str, str]\n"
    "    def __init__(self) -> None:\n"
    "        self._d = {}\n"
    "    def __setitem__(self, key: str, value: str) -> None:\n"
    "        self._d[key] = value\n"
    "    def __getitem__(self, key: str) -> str:\n"
    "        return self._d[key]\n"
)


class TestOwnedStrTupleElemThroughSelf:
    SRC = (
        _REC +
        "    def load(self, pair: tuple[str, str]) -> None:\n"
        "        self[pair[0]] = pair[1]\n"
        "def main() -> None:\n"
        "    h = H()\n"
        "    h.load((\"a\", \"b\"))\n"
        "    print(h[\"a\"])\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC)


class TestViewNameValueKeepsRejecting:
    SRC = (
        _REC +
        "    def load(self, k: str, v: str) -> None:\n"
        "        self[k] = v\n"
        "def main() -> None:\n"
        "    h = H()\n"
        "    h.load(\"a\", \"b\")\n"
        "    print(h[\"a\"])\n"
        "main()\n"
    )

    def test_rejects_at_the_setitem_family(self):
        compiler, modules = _compile(self.SRC)
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   comment_line_numbers=False,
                                   thir_codegen=True))
        _assert_rejects_at(dict(compiler._thir_fallback),
                           "body:stmt.assign", shape="setitem.family")
