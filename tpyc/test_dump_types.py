"""Tests for builtin type dump output (walks lib/tpy/ .py stubs)."""

import ast as pyast
import io
from contextlib import redirect_stdout

from .dump_types import (
    _extract_all_whitelist,
    _format_function_signature,
    _get_docstring,
    _gfm_anchor,
    _indent,
    _is_public,
    _path_to_module,
    _render_module,
    dump_builtin_types,
)


def _parse(src: str) -> pyast.Module:
    return pyast.parse(src)


def test_path_to_module():
    assert _path_to_module("tpy/__init__.py") == "tpy"
    assert _path_to_module("tpy/_core/_types.py") == "tpy._core._types"
    assert _path_to_module("typing.py") == "typing"
    assert _path_to_module("tplib/json/parser.py") == "tplib.json.parser"


def test_is_public():
    assert _is_public("Foo", None)
    assert not _is_public("_hidden", None)
    # Dunder names are public-by-convention module metadata.
    assert _is_public("__version__", None)
    assert _is_public("__all__", None)
    # With an __all__ whitelist, only listed names are public.
    allow = frozenset({"Foo"})
    assert _is_public("Foo", allow)
    assert not _is_public("Bar", allow)
    assert not _is_public("_hidden", allow)
    # __all__ whitelist takes precedence even for dunders.
    assert not _is_public("__version__", allow)


def test_extract_all_whitelist():
    tree = _parse('__all__ = ["A", "B"]\nclass C: ...')
    assert _extract_all_whitelist(tree) == {"A", "B"}

    tree_no_all = _parse("class C: ...")
    assert _extract_all_whitelist(tree_no_all) is None

    tree_tuple = _parse('__all__ = ("A", "B")')
    assert _extract_all_whitelist(tree_tuple) == {"A", "B"}


def test_format_function_signature_basic():
    tree = _parse("def f(x: int, y: str) -> bool: ...")
    fn = tree.body[0]
    assert _format_function_signature("f", fn) == "f(x: int, y: str) -> bool"


def test_format_function_signature_skips_self_for_constructors():
    tree = _parse("class C:\n    def __init__(self, x: int) -> None: ...")
    init = tree.body[0].body[0]
    sig = _format_function_signature("C", init, is_method=True, skip_self=True)
    assert sig == "C(x: int) -> None"


def test_format_function_signature_keeps_self_for_methods():
    tree = _parse("class C:\n    def f(self, x: int) -> int: ...")
    method = tree.body[0].body[0]
    sig = _format_function_signature("f", method, is_method=True)
    assert sig == "f(self, x: int) -> int"


def test_format_function_signature_varargs_and_kwonly():
    tree = _parse("def f(*args: int, flag: bool = True, **kw: str) -> None: ...")
    fn = tree.body[0]
    sig = _format_function_signature("f", fn)
    assert "*args: int" in sig
    assert "flag: bool" in sig
    assert "**kw: str" in sig


def test_format_function_signature_bare_kwonly_separator():
    """A bare `*` separator must be emitted when kwonlyargs exist without *args."""
    tree = _parse("def f(x: int, *, flag: bool) -> None: ...")
    fn = tree.body[0]
    sig = _format_function_signature("f", fn)
    assert sig == "f(x: int, *, flag: bool) -> None"


def test_get_docstring_module_class_function():
    tree = _parse('"""Module doc."""\n'
                  'class C:\n'
                  '    """Class doc."""\n'
                  '    def m(self):\n'
                  '        """Method doc."""\n'
                  '        ...\n'
                  'def f():\n'
                  '    """Function doc."""\n'
                  '    ...\n')
    assert _get_docstring(tree) == "Module doc."
    cls = tree.body[1]
    assert _get_docstring(cls) == "Class doc."
    # cls.body[0] is the docstring expression; cls.body[1] is the method.
    method = cls.body[1]
    assert _get_docstring(method) == "Method doc."
    fn = tree.body[2]
    assert _get_docstring(fn) == "Function doc."


def test_get_docstring_absent_returns_none():
    tree = _parse("class C: ...\ndef f(): ...\n")
    assert _get_docstring(tree) is None
    assert _get_docstring(tree.body[0]) is None
    assert _get_docstring(tree.body[1]) is None


def test_get_docstring_dedents_and_strips():
    tree = _parse('def f():\n    """First line.\n\n    Second line.\n    """\n    ...\n')
    # pyast.get_docstring(clean=True) already handles dedent, but our
    # normalisation strips trailing whitespace too.
    doc = _get_docstring(tree.body[0])
    assert doc == "First line.\n\nSecond line."


def test_indent_preserves_blank_lines():
    assert _indent("a\n\nb", "  ") == "  a\n\n  b"


def test_render_module_end_to_end(tmp_path):
    """End-to-end: synthetic stub rendered through _render_module should
    include class header, constructor overloads, methods, docstring, and a
    trailing blank line after docstrings so the next item isn't swallowed."""
    lib = tmp_path / "lib"
    pkg = lib / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "mod.py").write_text('"""Module doc."""\n'
                                 "class Foo:\n"
                                 '    """Class doc."""\n'
                                 "    def __init__(self, x: int) -> None: ...\n"
                                 "    def __init__(self, x: str) -> None: ...\n"
                                 "    def method(self, y: int) -> int:\n"
                                 '        """Method doc."""\n'
                                 "        ...\n"
                                 "    def another(self) -> None: ...\n"
                                 "VERSION: str = '1.0'\n")

    result = _render_module(pkg / "mod.py", lib)
    assert result is not None
    module_name, body = result
    assert module_name == "pkg.mod"

    # Module docstring appears.
    assert "Module doc." in body
    # Class header + class docstring.
    assert "### Foo" in body
    assert "Class doc." in body
    # Both __init__ overloads are under Constructor, not Methods.
    assert "**Constructor:**" in body
    assert body.count("Foo(x: int)") == 1
    assert body.count("Foo(x: str)") == 1
    # Methods section exists and contains visible methods.
    assert "**Methods:**" in body
    assert "method(self, y: int) -> int" in body
    assert "another(self) -> None" in body
    # Method docstring appears.
    assert "Method doc." in body
    # Module-level typed constant rendered.
    assert "`VERSION: str`" in body


def test_render_module_skips_when_nothing_to_document(tmp_path):
    """A module with only imports/private helpers produces no section."""
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "empty.py").write_text("from os import path\n"
                                   "_private = 1\n"
                                   "def _helper(): ...\n")
    assert _render_module(lib / "empty.py", lib) is None


def test_render_module_handles_syntax_error(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "broken.py").write_text("this is not ::: valid python\n")
    assert _render_module(lib / "broken.py", lib) is None


def test_dump_builtin_types_emits_toc_and_modules():
    """Smoke test: run the real dump against lib/tpy/ and verify structure."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        dump_builtin_types()
    out = buf.getvalue()
    # Header + TOC markers.
    assert out.startswith("# TurboPython Builtin Types")
    assert "## Modules" in out
    # A few well-known modules appear both in the TOC and as sections.
    for mod in ("math", "dataclasses", "tpy.version"):
        assert f"[`{mod}`]" in out, f"missing TOC entry for {mod}"
        assert f"## `{mod}`" in out, f"missing section for {mod}"
    # tpy.version surfaces its typed constants (AnnAssign handling).
    assert "`__version__: Final[str]`" in out
    assert "`is_compiled: Final[bool]`" in out


def test_gfm_anchor():
    # Backticks and dots stripped, underscores preserved, lowercased.
    assert _gfm_anchor("`bisect`") == "bisect"
    assert _gfm_anchor("`tpy._core._types`") == "tpy_core_types"
    assert _gfm_anchor("`tplib.json.parser`") == "tplibjsonparser"
    assert _gfm_anchor("`tplib.array_list`") == "tplibarray_list"
    # Spaces become hyphens.
    assert _gfm_anchor("Hello World") == "hello-world"
