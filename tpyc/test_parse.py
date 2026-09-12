"""Tests for parser utilities."""

import ast
import pytest
from .parse import RelativeImportKey, Parser, ParseError
from .parse.imports import (
    get_tpy_exports, scan_star_exports, NonLiteralAllError, read_module_all,
)
from .parse.parser import _validate_cpp_template


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
        for name in ["int32", "float32", "Span", "Array", "Ptr", "Own"]:
            assert name in exports, f"{name} missing from tpy exports"

    def test_contains_decorators(self):
        exports = get_tpy_exports()
        for name in ["readonly", "noalloc", "hotpath", "nocopy", "pure"]:
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
        module = p.parse("from tpy import *\ndef f(x: Span[int32]) -> int32:\n    return x[0]\n")
        assert module.tpy_star_import is True

    def test_star_import_resolves_decorators(self):
        p = Parser()
        module = p.parse("from tpy import *\n@readonly\ndef f(x: int32) -> int32:\n    return x\n")
        assert module.tpy_star_import is True

    def test_star_import_does_not_resolve_unknown(self):
        """Names not in __all__ should not resolve as tpy imports."""
        p = Parser()
        module = p.parse("from tpy import *\ndef f(x: int32) -> int32:\n    return x\n")
        # int32 should resolve, but a name not in exports should not
        source = module.imports.get("tpy")
        assert source is not None
        exported_locals = {local for _, local in source}
        assert "int32" in exported_locals
        assert "not_a_tpy_name" not in exported_locals


class TestParserStateIsolation:
    """Verify that `Parser.parse()` resets import-related state between calls.

    Scope of these tests (narrowed in Phase F.3b.5): we verify that the
    second parse's `module.imports` does not include the first parse's
    imports, AND that invoking the attached `module.resolver` on the
    second parse's AST refs raises (because the required import is
    absent from the parser's current state). They do NOT verify that
    modules produced by separate `parse()` calls hold independent
    resolvers -- in this codebase `resolver` is the same bound method
    across parses on the same Parser instance, so calling the first
    module's resolver AFTER the second parse will see the second
    parse's state. That sharing is safe in production (each module
    gets a fresh Parser) and out of scope for these tests.
    """

    def test_module_alias_does_not_leak(self):
        p = Parser()
        # First parse has 'import typing as t'
        p.parse("import typing as t\nfrom tpy import int32\ndef f(x: t.Optional[int32]) -> int32:\n    return int32(0)\n")
        # Second parse has no typing import -- t.Optional must fail to resolve
        m = p.parse("from tpy import int32\ndef f(x: t.Optional[int32]) -> int32:\n    return int32(0)\n")
        assert "typing" not in m.imports
        func = next(f for f in m.functions if f.name == "f")
        with pytest.raises(ParseError):
            m.resolver.resolve(func.params[0][1])

    def test_tpy_star_import_does_not_leak(self):
        p = Parser()
        # First parse has 'from tpy import *'
        p.parse("from tpy import *\ndef f(x: int32) -> int32:\n    return x\n")
        # Second parse has no tpy import -- int32 must fail to resolve
        m = p.parse("def f(x: int32) -> int32:\n    return x\n")
        assert not m.tpy_star_import
        func = next(f for f in m.functions if f.name == "f")
        with pytest.raises(ParseError):
            m.resolver.resolve(func.params[0][1])

    def test_typing_import_does_not_leak(self):
        p = Parser()
        # First parse has 'from typing import Optional'
        p.parse("from typing import Optional\nfrom tpy import int32\ndef f(x: Optional[int32]) -> int32:\n    return int32(0)\n")
        # Second parse has no typing import -- Optional must fail to resolve
        m = p.parse("from tpy import int32\ndef f(x: Optional[int32]) -> int32:\n    return int32(0)\n")
        typing_source = m.imports.get("typing")
        assert typing_source is None or "Optional" not in {orig for orig, _ in (typing_source or set())}
        func = next(f for f in m.functions if f.name == "f")
        with pytest.raises(ParseError):
            m.resolver.resolve(func.params[0][1])


class TestScanStarExports:
    """Tests for scan_star_exports()."""

    def test_with_all(self):
        source = '__all__ = ["foo", "bar"]\ndef foo(): pass\ndef bar(): pass\ndef baz(): pass\n'
        assert scan_star_exports(source) == frozenset({"foo", "bar"})

    def test_without_all_functions(self):
        source = "def foo(): pass\ndef bar(): pass\n"
        assert scan_star_exports(source) == frozenset({"foo", "bar"})

    def test_without_all_classes(self):
        source = "class Foo: pass\nclass Bar: pass\n"
        assert scan_star_exports(source) == frozenset({"Foo", "Bar"})

    def test_without_all_assignments(self):
        source = "X = 1\nY = 2\n"
        assert scan_star_exports(source) == frozenset({"X", "Y"})

    def test_without_all_annotated_assignments(self):
        source = "x: int = 1\n"
        assert scan_star_exports(source) == frozenset({"x"})

    def test_underscore_filtered(self):
        source = "def _private(): pass\ndef public(): pass\nclass _Internal: pass\n_x = 1\n"
        assert scan_star_exports(source) == frozenset({"public"})

    def test_imports_included_without_all(self):
        source = "from foo import bar\ndef baz(): pass\n"
        assert scan_star_exports(source) == frozenset({"bar", "baz"})

    def test_all_overrides_definitions(self):
        source = '__all__ = ["x"]\ndef x(): pass\ndef y(): pass\n'
        assert scan_star_exports(source) == frozenset({"x"})

    def test_empty_module(self):
        assert scan_star_exports("") == frozenset()

    def test_mixed_definitions(self):
        source = "def add(): pass\nclass Point: pass\nShape = None\n"
        assert scan_star_exports(source) == frozenset({"add", "Point", "Shape"})

    def test_non_literal_all_raises(self):
        source = '__all__ = _base + ["extra"]\ndef foo(): pass\ndef bar(): pass\n'
        with pytest.raises(NonLiteralAllError):
            scan_star_exports(source)

    def test_annotated_all(self):
        source = '__all__: list[str] = ["foo"]\ndef foo(): pass\ndef bar(): pass\n'
        assert scan_star_exports(source) == frozenset({"foo"})

    def test_last_all_wins(self):
        source = '__all__ = ["a"]\n__all__ = ["b"]\ndef a(): pass\ndef b(): pass\n'
        assert scan_star_exports(source) == frozenset({"b"})

    def test_underscore_import_filtered(self):
        source = "from foo import _private, public\n"
        assert scan_star_exports(source) == frozenset({"public"})


class TestReadModuleAll:
    """Tests for read_module_all()."""

    def _parse(self, source: str) -> ast.Module:
        return ast.parse(source)

    def test_no_all_returns_none(self):
        assert read_module_all(self._parse("x = 1\ndef foo(): pass\n")) is None

    def test_empty_module_returns_none(self):
        assert read_module_all(self._parse("")) is None

    def test_literal_list(self):
        result = read_module_all(self._parse('__all__ = ["foo", "bar"]\n'))
        assert result is not None
        assert result[0] == frozenset({"foo", "bar"})
        assert result[1].line == 1

    def test_empty_literal_list(self):
        # `__all__ = []` is the Python idiom for "export nothing"; the
        # empty frozenset must come through cleanly here so the
        # downstream variable-deduction shortcut in
        # `sema/local_deduction.py` (which infers `list[str]` for this
        # exact shape) has the right metadata to work with.
        result = read_module_all(self._parse('__all__ = []\n'))
        assert result is not None
        assert result[0] == frozenset()
        assert result[1].line == 1

    def test_literal_tuple(self):
        result = read_module_all(self._parse('__all__ = ("foo",)\n'))
        assert result is not None
        assert result[0] == frozenset({"foo"})

    def test_literal_set(self):
        result = read_module_all(self._parse('__all__ = {"foo", "bar"}\n'))
        assert result is not None
        assert result[0] == frozenset({"foo", "bar"})

    def test_annotated_all(self):
        result = read_module_all(self._parse('__all__: list[str] = ["foo"]\n'))
        assert result is not None
        assert result[0] == frozenset({"foo"})

    def test_last_assignment_wins(self):
        result = read_module_all(
            self._parse('__all__ = ["a"]\n__all__ = ["b"]\n'))
        assert result is not None
        assert result[0] == frozenset({"b"})
        # Line should be the line of the *winning* assignment's RHS.
        assert result[1].line == 2

    def test_non_literal_raises(self):
        with pytest.raises(NonLiteralAllError):
            read_module_all(self._parse('__all__ = _base + ["x"]\n'))

    def test_annotated_then_plain(self):
        # Mix of AnnAssign followed by plain Assign -- last wins.
        source = '__all__: list[str] = ["a"]\n__all__ = ["b"]\n'
        result = read_module_all(self._parse(source))
        assert result is not None
        assert result[0] == frozenset({"b"})

    def test_conditional_all_ignored(self):
        # read_module_all walks only top-level children, so `__all__`
        # nested inside an `if` / `try` / function body is invisible.
        # Pins this boundary; CPython evaluates it at import time.
        source = 'if True:\n    __all__ = ["x"]\n'
        assert read_module_all(self._parse(source)) is None


class TestUserModuleStarImport:
    """Star imports from user modules are deferred: parser records a
    placeholder, Compiler._expand_star_imports_for_module fills the
    actual name set against the source module's per-attribute table at
    compile time. The unit tests below pin the parser side of that
    contract -- end-to-end expansion is covered by the
    `tests/cases/imports/star_import_*` snippets."""

    def test_user_star_import_records_placeholder(self):
        p = Parser()
        module = p.parse("from mymod import *\ndef f() -> None:\n    pass\n")
        # Parser leaves a placeholder set; expansion happens at compile time.
        assert "mymod" in module.star_imports
        assert module.imports.get("mymod") == set()

    def test_user_star_import_unknown_module_defers_to_compile(self):
        # Parser no longer errors on unknown user modules in star imports
        # (Compiler._process_user_import handles the not-found check
        # downstream). The parser path is purely syntactic.
        p = Parser()
        module = p.parse("from unknown import *\n")
        assert "unknown" in module.star_imports

    def test_tpy_star_import_still_tracked(self):
        p = Parser()
        module = p.parse("from tpy import *\ndef f(x: int32) -> int32:\n    return x\n")
        assert module.tpy_star_import is True
        assert "tpy" in module.star_imports


class TestClassBodyTupleTargetRejected:
    """A tuple-target assignment in a class body is a field-declaration parse
    error, never a tuple unpack -- so the module-level unpack desugar (which
    aliases reference globals) has no class-scope counterpart to handle."""

    def test_bare_tuple_target(self):
        p = Parser()
        with pytest.raises(ParseError, match="Invalid field declaration"):
            p.parse("class K:\n    a, b = 1, 2\n")

    def test_parenthesized_tuple_target(self):
        p = Parser()
        with pytest.raises(ParseError, match="Invalid field declaration"):
            p.parse("class K:\n    a, b = (1, 2)\n")


class TestHotpathDecorator:
    """@hotpath parses on functions and methods and lands on the AST node.
    It carries no compiler behavior yet."""

    _SRC = "from tpy import hotpath\n"

    def test_function(self):
        mod = Parser().parse(self._SRC + "@hotpath\ndef f() -> int:\n    return 1\n")
        assert mod.functions[0].is_hotpath

    def test_method(self):
        mod = Parser().parse(
            self._SRC + "class K:\n    @hotpath\n    def m(self) -> int:\n        return 1\n",
            module_name="m",
        )
        assert mod.records[0].methods[0].is_hotpath

    def test_absent_by_default(self):
        mod = Parser().parse("def f() -> int:\n    return 1\n")
        assert not mod.functions[0].is_hotpath


class TestValidateCppTemplate:
    """`_validate_cpp_template` rejects a repeated runtime-value placeholder
    ({self}/{N}); type placeholders ({cpp}, {T}) and single use stay legal."""

    def test_single_use_ok(self):
        assert _validate_cpp_template("{self}[{0}] = {1}") is None

    def test_repeated_positional_rejected(self):
        assert _validate_cpp_template("{0} + {0}") is not None

    def test_repeated_self_rejected(self):
        assert _validate_cpp_template(
            "std::stable_sort({self}.begin(), {self}.end())") is not None

    def test_index_normalized_before_dedup(self):
        # {0} and {00} are the same argument to expand_cpp_template (int(field)),
        # so a template mixing them must still be rejected.
        assert _validate_cpp_template("{0} + {00}") is not None

    def test_repeat_detected_across_literal_braces(self):
        assert _validate_cpp_template("{{ return {0} + {0}; }}") is not None

    def test_repeated_type_placeholder_ok(self):
        # {cpp} and named type params are inert (resolved before arg expansion).
        assert _validate_cpp_template("static_cast<{cpp}>(static_cast<{cpp}>({0}))") is None
        assert _validate_cpp_template("::tpy::construct<{T}, {T}>({0})") is None

    def test_malformed_defers_to_expand(self):
        # An unmatched brace is expand_cpp_template's diagnostic, not ours.
        assert _validate_cpp_template("{0} + {") is None

    def test_method_site_rejects_via_parser(self):
        # Exercises the method decorator call site (distinct from the free-fn site).
        p = Parser()
        with pytest.raises(ParseError, match="more than once"):
            p.parse(
                "from tpy.extern import cpp_template\n"
                "from tpy import int32\n"
                "class K:\n"
                "    @cpp_template(\"{self} + {self}\")\n"
                "    def dbl(self) -> int32: ...\n")
