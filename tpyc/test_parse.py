"""Tests for parser utilities."""

import ast
from collections.abc import Callable
import pytest
from .parse import RelativeImportKey, Parser, ParseError
from .parse.imports import (
    get_tpy_exports, scan_star_exports, NonLiteralAllError, read_module_all,
    module_all_run,
)
from .parse.nodes import AllUpdate
from . import get_lib_dir
from .parse.parser import _TEMPLATE_STRING_NODES, _validate_cpp_template


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


@pytest.fixture(scope="module")
def extern_parser() -> Callable[[], Parser]:
    """A parser that knows the `tpy.extern` decorator kwargs: the schemas
    come from parsing the extern stubs, as the compiler does first."""
    stubs = Parser()
    stubs.parse((get_lib_dir() / "tpy" / "tpy" / "_bootstrap" / "_extern.py").read_text(),
                module_name="tpy._bootstrap._extern")
    return lambda: Parser(decorator_schemas=stubs._decorator_schemas)


class TestTransientKwarg:
    """`transient=True` is a binding fact on `@native` / `@cpp_template`,
    carried beside `@pure`."""

    STUBS = (
        "from tpy import pure, int32\n"
        "from tpy.extern import native, cpp_template\n"
        "@native(\"clock\", transient=True)\ndef clock() -> float: ...\n"
        "@cpp_template(\"({0})\", transient=True)\ndef ident(x: int32) -> int32: ...\n"
        "@pure\n@native(\"sq\")\ndef sq(x: float) -> float: ...\n"
        "@native(\"plain\", transient=False)\ndef plain() -> float: ...\n"
        "@native(\"K\")\n"
        "class K:\n"
        "    @native(\"k_m\", transient=True)\n    def m(self) -> int32: ...\n"
        "    @cpp_template(\"{self}.n\", transient=True)\n    def t(self) -> int32: ...\n")

    def test_parsed_on_native_and_template_stubs(self, extern_parser):
        module = extern_parser().parse(self.STUBS, module_name="main")
        flags = {f.name: (f.is_transient, f.is_pure) for f in module.functions}
        assert flags == {"clock": (True, False), "ident": (True, False), "sq": (False, True),
                         "plain": (False, False)}
        assert [(f.name, f.is_transient) for r in module.records for f in r.methods] == [("m", True), ("t", True)]
        by_name = {f.name: f for f in module.functions}
        assert by_name["clock"].native_name == "clock" and by_name["ident"].cpp_template == "({0})"

    def test_non_bool_value_rejected(self, extern_parser):
        with pytest.raises(ParseError, match=r"@native\(transient=\.\.\.\) expects bool"):
            extern_parser().parse("from tpy.extern import native\n"
                                  "@native(\"clock\", transient=1)\ndef clock() -> float: ...\n",
                                  module_name="main")

    def test_rejected_on_a_method_with_a_body(self, extern_parser):
        # A @native method may carry a TPy body; the promise would shadow it.
        with pytest.raises(ParseError, match=r"@native\(transient=True\) is only valid on a stub: .*'m' has a TPy body"):
            extern_parser().parse("from tpy import int32\nfrom tpy.extern import native\n"
                                  "@native(\"K\")\nclass K:\n"
                                  "    @native(\"k_m\", transient=True)\n"
                                  "    def m(self) -> int32:\n        return 1\n",
                                  module_name="main")

    def test_rejected_on_a_class(self, extern_parser):
        with pytest.raises(ParseError, match=r"@native\(transient=\.\.\.\) is only valid on a function or method stub"):
            extern_parser().parse("from tpy.extern import native\n"
                                  "@native(\"K\", transient=True)\nclass K:\n    pass\n",
                                  module_name="main")


class TestChecksSignalsKwarg:
    """`checks_signals=True` marks a `@native` / `@cpp_template` binding as a
    Ctrl-C check point, beside `transient=True`."""

    STUBS = (
        "from tpy import int32\n"
        "from tpy.extern import native, cpp_template\n"
        "@native(\"wait\", checks_signals=True)\ndef wait() -> None: ...\n"
        "@cpp_template(\"poll({0})\", checks_signals=True)\ndef poll(x: int32) -> int32: ...\n"
        "@native(\"plain\")\ndef plain() -> None: ...\n"
        "@native(\"K\")\n"
        "class K:\n"
        "    @native(\"k_read\", checks_signals=True)\n    def read(self) -> int32: ...\n"
        "    @native(\"k_close\")\n    def close(self) -> None: ...\n")

    def test_parsed_on_native_and_template_stubs(self, extern_parser):
        module = extern_parser().parse(self.STUBS, module_name="main")
        assert {f.name: f.checks_signals for f in module.functions} == {
            "wait": True, "poll": True, "plain": False}
        assert [(f.name, f.checks_signals) for r in module.records for f in r.methods] == [
            ("read", True), ("close", False)]

    def test_rejected_on_a_method_with_a_body(self, extern_parser):
        with pytest.raises(ParseError, match=r"@native\(checks_signals=True\) is only valid on a stub: .*'m' has a TPy body"):
            extern_parser().parse("from tpy import int32\nfrom tpy.extern import native\n"
                                  "@native(\"K\")\nclass K:\n"
                                  "    @native(\"k_m\", checks_signals=True)\n"
                                  "    def m(self) -> int32:\n        return 1\n",
                                  module_name="main")

    def test_rejected_on_a_free_native_function_with_a_body(self, extern_parser):
        with pytest.raises(ParseError, match=r"@native\(checks_signals=True\) is only valid on a stub: .*'f' has a TPy body"):
            extern_parser().parse("from tpy import int32\nfrom tpy.extern import native\n"
                                  "@native(\"x\", checks_signals=True)\n"
                                  "def f() -> int32:\n    return 1\n",
                                  module_name="main")

    def test_rejected_on_a_cpp_template_with_a_body(self, extern_parser):
        with pytest.raises(ParseError, match=r"@cpp_template\(checks_signals=True\) is only valid on a stub: .*'g' has a TPy body"):
            extern_parser().parse("from tpy import int32\nfrom tpy.extern import cpp_template\n"
                                  "@cpp_template(\"g({0})\", checks_signals=True)\n"
                                  "def g(x: int32) -> int32:\n    return x\n",
                                  module_name="main")

    def test_rejected_on_a_class(self, extern_parser):
        with pytest.raises(ParseError, match=r"@native\(checks_signals=\.\.\.\) is only valid on a function or method stub"):
            extern_parser().parse("from tpy.extern import native\n"
                                  "@native(\"K\", checks_signals=True)\nclass K:\n    pass\n",
                                  module_name="main")


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

    def test_run_updates_per_statement(self):
        # A replaced non-literal value is an update with unknown names.
        source = ('x = 1\n__all__ = list()\n__all__ = ["a"]\n'
                  '__all__ += ["b"]\n')
        result, updates = module_all_run(self._parse(source))
        assert result is not None and result[0] == frozenset({"a", "b"})
        assert updates == [None, AllUpdate(None, False),
                           AllUpdate(("a",), False), AllUpdate(("b",), True)]


# Master b4ca0ae5cd's `__all__` rule: only the final `=` value must be a
# literal; each `+=` value must be, after some `=`; a method call never is.
_ALL_LITERAL_MSG = ("__all__ is not a compile-time literal (must be a "
                    "list / tuple / set of string literals)")
_ALL_SHAPES = [
    ('__all__ = None\n__all__ = ["f"]\n', frozenset({"f"})),
    ('__all__ = list()\n__all__ += ["f"]\n__all__ = ["f"]\n', frozenset({"f"})),
    ('__all__ = f()\n__all__ = ["a"]\n', frozenset({"a"})),
    ('__all__: list[str] = f()\n__all__ = ["a"]\n', frozenset({"a"})),
    ('__all__ = ["a"]\n__all__ += ["b"]\n', frozenset({"a", "b"})),
    ('__all__ = ["a"]\n__all__ += ["b"]\n__all__ = ["c"]\n', frozenset({"c"})),
    ('__all__ += ["b"]\n', "__all__ += without a prior __all__ assignment"),
    ('__all__ -= ["a"]\n', "__all__ += without a prior __all__ assignment"),
    ('__all__ = ["a"]\n__all__ -= ["a"]\n',
     "__all__ only supports '+=' augmented assignment"),
    ('__all__ = ["a"]\n__all__ += g()\n',
     "__all__ += value is not a compile-time literal"),
    ('__all__ = ["a"]\n__all__.append("b")\n',
     "__all__ method mutation is not supported; use '__all__ += [...]'"),
    ('__all__ = ["a"]\n__all__ = f()\n', _ALL_LITERAL_MSG),
    ('__all__ = list()\n__all__ += ["f"]\n', _ALL_LITERAL_MSG),
    ('__all__ = None\n', _ALL_LITERAL_MSG),
]


@pytest.mark.parametrize("source,expected", _ALL_SHAPES)
def test_module_all_matches_master(source: str, expected) -> None:
    if isinstance(expected, str):
        with pytest.raises(NonLiteralAllError) as exc:
            read_module_all(ast.parse(source))
        assert str(exc.value) == expected
    else:
        result = read_module_all(ast.parse(source))
        assert result is not None and result[0] == expected


def test_module_all_replaced_values_are_unknown_updates() -> None:
    # A replaced non-literal value, and a `+=` onto it, update `__all__`
    # to an unknown value until the next literal `=`.
    source = '__all__ = None\n__all__ += ["f"]\n__all__ = ["f"]\n'
    _result, updates = module_all_run(ast.parse(source))
    assert updates == [AllUpdate(None, False), AllUpdate(None, True),
                       AllUpdate(("f",), False)]


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


class TestProtocolOnlyDecoratorRejected:
    """@runtime_checkable and @dynamic apply only to protocols; every other
    decorated definition rejects them by name, as CPython raises TypeError."""

    _SRC = ("from typing import Protocol, TypedDict, runtime_checkable\n"
            "from enum import Enum\n"
            "from tpy import dynamic\n")

    @pytest.mark.parametrize("decorator, body", [
        ("runtime_checkable", "@{d}\nclass C:\n    pass\n"),
        ("runtime_checkable", "@{d}\nclass E(Enum):\n    A = 1\n"),
        ("runtime_checkable", "@{d}\ndef f() -> int:\n    return 1\n"),
        ("runtime_checkable",
         "class C:\n    @{d}\n    def m(self) -> int:\n        return 1\n"),
        ("runtime_checkable", "@{d}\nclass TD(TypedDict):\n    a: int\n"),
        ("runtime_checkable",
         "class P(Protocol):\n    @{d}\n    def m(self) -> int: ...\n"),
        ("dynamic", "@{d}\nclass C:\n    pass\n"),
        ("dynamic",
         "class P(Protocol):\n    @{d}\n    def m(self) -> int: ...\n"),
    ], ids=["class", "enum", "function", "method", "typeddict",
            "protocol_method", "dynamic_class", "dynamic_protocol_method"])
    def test_rejected_by_name(self, decorator, body):
        with pytest.raises(ParseError) as info:
            Parser().parse(self._SRC + body.format(d=decorator), module_name="m")
        assert info.value.message == f"@{decorator} applies only to protocols"


class TestValidateCppTemplate:
    """`_validate_cpp_template` requires each runtime-value slot ({self}/{N})
    exactly once; type placeholders ({cpp}, {T}) are free to repeat."""

    @staticmethod
    def _v(tmpl: str, n: int = 1, needs_self: bool = False) -> str | None:
        return _validate_cpp_template(tmpl, [f"p{i}" for i in range(n)],
                                      needs_self)

    def test_single_use_ok(self):
        assert self._v("{self}[{0}] = {1}", 2, needs_self=True) is None

    def test_repeated_positional_rejected(self):
        assert "more than once" in self._v("{0} + {0}")

    def test_repeated_self_rejected(self):
        assert "more than once" in self._v(
            "std::stable_sort({self}.begin(), {self}.end())", 0,
            needs_self=True)

    def test_index_normalized_before_dedup(self):
        # {0} and {00} are the same argument to expand_cpp_template (int(field)),
        # so a template mixing them must still be rejected.
        assert "more than once" in self._v("{0} + {00}")

    def test_repeat_detected_across_literal_braces(self):
        assert "more than once" in self._v("{{ return {0} + {0}; }}")

    def test_repeated_type_placeholder_ok(self):
        # {cpp} and named type params are inert (resolved before arg expansion).
        assert self._v("static_cast<{cpp}>(static_cast<{cpp}>({0}))") is None
        assert self._v("::tpy::construct<{T}, {T}>({0})") is None

    def test_omitted_positional_rejected(self):
        err = self._v("f({0})", 2)
        assert "parameter 'p1'" in err and "static_cast<void>({1})" in err

    def test_omitted_self_rejected(self):
        err = self._v("f({0})", 1, needs_self=True)
        assert "receiver" in err and "static_cast<void>({self})" in err

    def test_explicit_discard_ok(self):
        assert self._v("(static_cast<void>({0}), f({1}))", 2) is None

    def test_literal_brace_is_not_a_use(self):
        assert "parameter 'p0'" in self._v("{{0}}")

    def test_out_of_range_rejected(self):
        assert "names no parameter" in self._v("f({0}, {1})", 1)

    def test_self_without_receiver_rejected(self):
        assert "no receiver" in self._v("{self}.f({0})", 1)

    def test_malformed_rejected(self):
        assert "unmatched '{'" in self._v("{0} + {")
        assert "unmatched '}'" in self._v("{0} }")
        assert "invalid placeholder" in self._v("T{}({0})")

    def test_non_ascii_digit_is_malformed(self):
        # str.isdigit() holds for a superscript, which int() refuses.
        assert "invalid placeholder '{\u00b2}'" in self._v("{\u00b2}")

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

    @pytest.mark.parametrize("src, expect", [
        # *args and keyword-only slots number as sema's ParamInfo order.
        ("@cpp_template(\"f({0}, {1}, {2}, {3})\")\n"
         "def g(a: int32, *xs: int32, k: int32, **kw: Unpack[TD]) -> None: ...\n",
         None),
        ("@cpp_template(\"f({0}, {1})\")\n"
         "def g(a: int32, *, k: int32 = 1) -> None: ...\n", None),
        ("@cpp_template(\"f({0})\")\n"
         "def g(a: int32, *, k: int32 = 1) -> None: ...\n", "parameter 'k'"),
        ("@cpp_template(\"f({0})\")\n"
         "def g(a: int32, *xs: int32) -> None: ...\n", "parameter 'xs'"),
        ("class K:\n"
         "    @cpp_template(\"K({0})\")\n"
         "    def __init__(self, a: int32) -> None: ...\n", None),
        ("class K:\n"
         "    @staticmethod\n"
         "    @cpp_template(\"f({0})\")\n"
         "    def s(a: int32) -> None: ...\n", None),
        ("class K:\n"
         "    @classmethod\n"
         "    @cpp_template(\"f({self})\")\n"
         "    def c(cls) -> None: ...\n", "no receiver"),
        ("class K:\n"
         "    @cpp_template(\"({0} * 2)\")\n"
         "    def m(self, a: int32) -> int32: ...\n",
         "never spells the receiver 'self'"),
        ("class K:\n"
         "    @property\n"
         "    @cpp_template(\"7\")\n"
         "    def p(self) -> int32: ...\n", "receiver"),
    ])
    def test_signature_slots_via_parser(self, src, expect):
        full = ("from tpy.extern import cpp_template\n"
                "from tpy import int32\n"
                "from typing import TypedDict, Unpack\n"
                "class TD(TypedDict):\n"
                "    z: int32\n" + src)
        if expect is None:
            Parser().parse(full, module_name="m")
        else:
            with pytest.raises(ParseError, match=expect):
                Parser().parse(full, module_name="m")


# The expression node classes `Parser._parse_expr` lowers (FormattedValue
# only as a JoinedStr part). A node class a newer Python adds is in neither
# this set nor the describe table and fails the walk below, so its
# rejection wording is chosen on purpose rather than falling to the default.
_LOWERED_EXPR_NODES = frozenset({
    "Attribute", "Await", "BinOp", "BoolOp", "Call", "Compare", "Constant",
    "Dict", "DictComp", "FormattedValue", "GeneratorExp", "IfExp",
    "JoinedStr", "Lambda", "List", "ListComp", "Name", "NamedExpr", "Set",
    "SetComp", "Subscript", "Tuple", "UnaryOp", "Yield", "YieldFrom",
})
_DESCRIBED_EXPR_NODES = frozenset({"Starred", "Slice"}) | _TEMPLATE_STRING_NODES


class TestUnsupportedExprWording:
    def test_every_expr_node_is_lowered_or_described(self):
        names = {cls.__name__ for cls in ast.expr.__subclasses__()}
        assert names - _LOWERED_EXPR_NODES - _DESCRIBED_EXPR_NODES == set()

    @pytest.mark.parametrize("source, message", [
        ("ys = [*xs, 3]\n", "unpacking with '*' ('*xs') is not supported here"),
        ("y = xs[0:1, 1]\n", "a slice ('0:1') is only supported as the whole subscript"),
        ("x = ...\n", "this expression is not supported: '...'"),
        ("x = a @ b\n", "operator not supported: 'a @ b'"),
    ])
    def test_wording_names_no_ast_class(self, source, message):
        with pytest.raises(ParseError) as info:
            Parser().parse(source)
        assert message in info.value.message
        assert info.value.lineno == 1
