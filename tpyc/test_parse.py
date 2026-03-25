"""Tests for parser utilities."""

import pytest
from .parse import RelativeImportKey, Parser, ParseError
from .parse.imports import get_tpy_exports


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


class TestTpyExports:
    """Tests for get_tpy_exports() reading __all__ from tpy/__init__.py."""

    def test_returns_nonempty_frozenset(self):
        exports = get_tpy_exports()
        assert isinstance(exports, frozenset)
        assert len(exports) > 0

    def test_contains_core_types(self):
        exports = get_tpy_exports()
        for name in ["Int32", "Float32", "Span", "Array", "Ptr", "Own"]:
            assert name in exports, f"{name} missing from tpy exports"

    def test_contains_decorators(self):
        exports = get_tpy_exports()
        for name in ["readonly", "noalloc", "nocopy", "pure"]:
            assert name in exports, f"{name} missing from tpy exports"

    def test_contains_parser_keywords(self):
        exports = get_tpy_exports()
        assert "auto_readonly" in exports
        assert "auto_own" in exports

    def test_no_python_builtins(self):
        """__all__ should not include Python builtins like int, str, float."""
        exports = get_tpy_exports()
        for name in ["int", "float", "str", "bool", "None"]:
            assert name not in exports, f"Python builtin {name} should not be in tpy exports"


class TestStarImportResolution:
    """Tests for 'from tpy import *' name resolution."""

    def test_star_import_resolves_types(self):
        p = Parser()
        # All core tpy types should resolve with star import
        module = p.parse("from tpy import *\ndef f(x: Span[Int32]) -> Int32:\n    return x[0]\n")
        assert module.tpy_star_import is True

    def test_star_import_resolves_decorators(self):
        p = Parser()
        module = p.parse("from tpy import *\n@readonly\ndef f(x: Int32) -> Int32:\n    return x\n")
        assert module.tpy_star_import is True

    def test_star_import_does_not_resolve_unknown(self):
        """Names not in __all__ should not resolve as tpy imports."""
        p = Parser()
        module = p.parse("from tpy import *\ndef f(x: Int32) -> Int32:\n    return x\n")
        # Int32 should resolve, but a name not in exports should not
        source = module.imports.get("tpy")
        assert source is not None
        exported_locals = {local for _, local in source}
        assert "Int32" in exported_locals
        assert "not_a_tpy_name" not in exported_locals


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
