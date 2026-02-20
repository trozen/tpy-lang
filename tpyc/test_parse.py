"""Tests for parser utilities."""

import pytest
from .parse import RelativeImportKey, Parser, ParseError


class TestRelativeImportKey:
    def test_roundtrip_simple(self):
        key = RelativeImportKey(level=1, line=10, col=0, partial="")
        assert RelativeImportKey.decode(key.encode()) == key

    def test_roundtrip_with_partial(self):
        key = RelativeImportKey(level=2, line=5, col=4, partial="utils")
        assert RelativeImportKey.decode(key.encode()) == key

    def test_roundtrip_dotted_partial(self):
        key = RelativeImportKey(level=1, line=99, col=12, partial="pkg.sub")
        assert RelativeImportKey.decode(key.encode()) == key

    def test_is_placeholder_positive(self):
        key = RelativeImportKey(level=1, line=1, col=0, partial="mod")
        assert RelativeImportKey.is_placeholder(key.encode())

    def test_is_placeholder_negative(self):
        assert not RelativeImportKey.is_placeholder("mymodule")
        assert not RelativeImportKey.is_placeholder("__init__")
        assert not RelativeImportKey.is_placeholder("")

    def test_prefix_cannot_collide_with_identifiers(self):
        encoded = RelativeImportKey(level=1, line=1, col=0, partial="").encode()
        assert "\x00" in encoded


class TestParserStateIsolation:
    """Verify that Parser.parse() resets all state between calls."""

    def test_module_alias_does_not_leak(self):
        p = Parser()
        # First parse has 'import typing as t'
        p.parse("import typing as t\nfrom tpy import Int32\ndef f(x: t.Optional[Int32]) -> Int32:\n    return Int32(0)\n")
        # Second parse has no typing import -- t.Optional must fail
        with pytest.raises(ParseError):
            p.parse("from tpy import Int32\ndef f(x: t.Optional[Int32]) -> Int32:\n    return Int32(0)\n")

    def test_tpy_star_import_does_not_leak(self):
        p = Parser()
        # First parse has 'from tpy import *'
        p.parse("from tpy import *\ndef f(x: Int32) -> Int32:\n    return x\n")
        # Second parse has no tpy import -- Int32 must fail
        with pytest.raises(ParseError):
            p.parse("def f(x: Int32) -> Int32:\n    return x\n")

    def test_typing_import_does_not_leak(self):
        p = Parser()
        # First parse has 'from typing import Optional'
        p.parse("from typing import Optional\nfrom tpy import Int32\ndef f(x: Optional[Int32]) -> Int32:\n    return Int32(0)\n")
        # Second parse has no typing import -- Optional must fail
        with pytest.raises(ParseError):
            p.parse("from tpy import Int32\ndef f(x: Optional[Int32]) -> Int32:\n    return Int32(0)\n")
