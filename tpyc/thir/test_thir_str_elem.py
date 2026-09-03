"""Str-element subscript rows: view-method receivers (`argv[i].startswith`)
and container-append args (`tag.append(argv[i])`)."""
from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import _compile, _entry, _fn, _lower

_PRELUDE = "from tpy import Int32\n"


def _cpp(src: str) -> str:
    compiler, modules = _compile(src)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False))
    return hpp + cpp


class TestStrElemRows:
    SRC = (
        _PRELUDE
        + "def recv(argv: list[str], i: Int32) -> bool:\n"
        + "    return argv[i].startswith(\"-\")\n"
        + "def cond(argv: list[str]) -> Int32:\n"
        + "    i = 0\n"
        + "    while i < len(argv) and not argv[i].startswith(\"-\"):\n"
        + "        i += 1\n"
        + "    return i\n"
        + "def app(tag: list[str], argv: list[str], i: Int32) -> None:\n"
        + "    tag.append(argv[i])\n"
    )

    def test_routing_is_non_vacuous(self):
        thir = _lower(self.SRC)
        for name in ("recv", "cond", "app"):
            assert _fn(thir, name) is not None, name

    def test_emitted_shapes(self):
        out = _cpp(self.SRC)
        # The element read feeds the native str view-method positionally.
        assert ('::tpy::str_startswith(::tpy::__getitem__(argv, i), "-")'
                in out)
        # The append arg lands bare (push_back copies on insert).
        assert "tag.push_back(::tpy::__getitem__(argv, i));" in out
