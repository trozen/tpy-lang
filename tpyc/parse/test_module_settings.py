"""The `__tpy__` module-settings reader: the accepted shapes and every
rejection, each at the offending line."""

from __future__ import annotations

import symtable
from pathlib import Path
from typing import NoReturn

import pytest

from tpyc.parse import Parser, ParseError
from tpyc.parse.module_settings import module_kind, read_source_settings


def _read(source: str):
    return read_source_settings(source)[1]


def _error(source: str) -> ParseError:
    with pytest.raises(ParseError) as exc:
        _read(source)
    return exc.value


def test_full_header_after_docstring_and_future_import():
    s = _read(
        '"""Doc."""\n'
        "from __future__ import annotations\n"
        '__tpy__ = {"mod": "native", "cpp_namespace": "a::b",\n'
        '           "includes": ["<x.hpp>", {"header": "w.hpp", "platform": "win32"}],\n'
        '           "links": ["m", {"lib": "zlib", "managed": True}]}\n'
        "import os\n")
    assert s.kind == "native" and s.native_module and not s.ext_module
    assert s.cpp_namespace == "a::b"
    assert s.includes == [("<x.hpp>", None), ("w.hpp", "win32")]
    assert s.link_libs == [("m", None)]
    assert s.third_party_deps == [("zlib", None)]


def test_tuples_and_link_platform():
    s = _read('__tpy__ = {"mod": "ext", "links": ({"lib": "m", "platform": "linux", "managed": False},)}\n')
    assert s.ext_module and s.link_libs == [("m", "linux")]


def test_empty_and_absent_settings_are_an_ordinary_module():
    assert _read("__tpy__ = {}\nx = 1\n").kind is None
    assert _read("x = 1\n").kind is None


def test_function_local_and_class_attribute_are_ordinary_names():
    s = _read(
        '__tpy__ = {"mod": "ext"}\n'
        "def f():\n"
        "    __tpy__ = 1\n"
        "    return __tpy__\n"
        "class C:\n"
        "    __tpy__ = 1\n"
        "    y = __tpy__\n")
    assert s.kind == "ext"


def test_parser_strips_the_header():
    module = Parser().parse('"""Doc."""\n__tpy__ = {"cpp_namespace": "n"}\nx = 1\n')
    plain = Parser().parse('"""Doc."""\nx = 1\n')
    assert module.directives.cpp_namespace == "n"
    assert len(module.top_level_stmts) == len(plain.top_level_stmts)


@pytest.mark.parametrize("source, message, line", [
    ('__tpy__ = {"mod": "natve"}\n',
     '__tpy__["mod"] must be one of "native", "macro", "ext"', 1),
    ('__tpy__ = {"cpp_namspace": "x"}\n',
     "__tpy__: unknown setting 'cpp_namspace' (known: mod, cpp_namespace, includes, links)", 1),
    ('__tpy__ = {"mod": "ext",\n           "mod": "native"}\n',
     "__tpy__: duplicate key 'mod'", 2),
    ('__tpy__ = {"cpp_namespace": "1bad-ns"}\n',
     "invalid C++ namespace '1bad-ns'", 1),
    ('__tpy__ = {"includes": "x.hpp"}\n',
     '__tpy__["includes"] must be a list or tuple', 1),
    ('__tpy__ = {"links": [{"lib": "z", "managd": True}]}\n',
     "__tpy__[\"links\"] entry: unknown key 'managd' (known: lib, managed, platform)", 1),
    ('__tpy__ = {"links": [{"managed": True}]}\n',
     '__tpy__["links"] entry is missing "lib"', 1),
    ('__tpy__ = {"includes": [{"header": "w.hpp", "platform": "windwos"}]}\n',
     '"platform" must be one of "linux", "darwin", "win32"', 1),
    ('__tpy__ = {"links": [{"lib": "z", "managed": "yes"}]}\n',
     '"managed" must be True or False', 1),
    ('__tpy__ = {"cpp_namespace": NAME}\n', "must be a literal", 1),
    ('__tpy__ = {"cpp_namespace": f"a{1}"}\n', "must be a literal", 1),
    ('__tpy__ = {"cpp_namespace": "a".upper()}\n', "must be a literal", 1),
    ("__tpy__ = dict(mod='ext')\n", "__tpy__ must be a dict literal", 1),
    ('"""Doc."""\nx = 1\n__tpy__ = {"mod": "ext"}\n',
     "__tpy__ must come before any other code", 3),
    # Python's own `from __future__` rule: nothing but the docstring before it.
    ('pass\n__tpy__ = {"mod": "ext"}\n', "__tpy__ must come before any other code", 2),
    ('"""Doc."""\n"""Not the docstring."""\n__tpy__ = {}\n',
     "__tpy__ must come before any other code", 3),
    ('__tpy__ = {**base}\n', "__tpy__: `**` unpacking is not allowed; spell every key", 1),
    ('__tpy__ = {1: "x"}\n', "__tpy__: keys must be string literals", 1),
    ('__tpy__ = {"links": [True]}\n', '__tpy__["links"] entries must be a string or a dict', 1),
    ('__tpy__ = {"includes": [{"platform": "linux"}]}\n',
     '__tpy__["includes"] entry is missing "header"', 1),
    ('__tpy__ = {"cpp_namespace": True}\n', '__tpy__["cpp_namespace"] must be a string', 1),
    ('__tpy__ = {"includes": [{"header": True}]}\n',
     '__tpy__["includes"] entry "header" must be a string', 1),
    ('__tpy__ = {"links": [{"lib": False}]}\n', '__tpy__["links"] entry "lib" must be a string', 1),
])
def test_bad_settings(source: str, message: str, line: int) -> None:
    err = _error(source)
    assert message in err.message
    assert err.lineno == line


_REBIND = "__tpy__ must be assigned once, at module top level"


@pytest.mark.parametrize("tail, line", [
    ('__tpy__ = {"mod": "native"}\n', 2),
    ('__tpy__ += {"mod": "native"}\n', 2),
    ("__tpy__: dict = {}\n", 2),
    ("if c:\n    __tpy__ = {}\n", 3),
    ("del __tpy__\n", 2),
    ("import os as __tpy__\n", 2),
    ("def __tpy__():\n    pass\n", 2),
    ("class __tpy__:\n    pass\n", 2),
    ("try:\n    pass\nexcept E as __tpy__:\n    pass\n", 4),
    ("match v:\n    case __tpy__:\n        pass\n", 3),
    ("def f():\n    global __tpy__\n    __tpy__ = 1\n", 3),
    ("def f():\n    global __tpy__\n    del __tpy__\n", 3),
    ("def f():\n    global __tpy__\n    import os as __tpy__\n", 3),
    ("x = [(__tpy__ := 1) for y in z]\n", 2),
    # An identifier spelling NFKC-normalizes to the name.
    ("__\uff54\uff50\uff59__ += {}\n", 2),
])
def test_rebinding(tail: str, line: int) -> None:
    err = _error('__tpy__ = {"mod": "ext"}\n' + tail)
    assert err.message == _REBIND
    assert err.lineno == line


def test_rebinding_without_a_header():
    err = _error("def f():\n    pass\nclass __tpy__:\n    pass\n")
    assert err.message == _REBIND and err.lineno == 3


_READ = "__tpy__ holds compile-time module settings and cannot be read"


@pytest.mark.parametrize("tail, line", [
    ("print(__tpy__)\n", 2),
    ("def f():\n    return __tpy__\n", 3),
    ("g = lambda: __tpy__\n", 2),
    ("class C:\n    y = __tpy__\n", 3),
    ('__all__ = ["f",\n           "__tpy__"]\n', 2),
    ('__all__ = ["\\x5f\\x5ftpy\\x5f\\x5f"]\n', 2),
    ("def f():\n    return list(__tpy__ for x in [1])\n", 3),
    ("def f(x: __tpy__):\n    pass\n", 2),
    ("def f() -> __tpy__:\n    pass\n", 2),
    ("def f[T](x: __tpy__) -> T:\n    pass\n", 2),
    # The first iterable runs in the enclosing scope, not the target's.
    ("x = [1 for __tpy__ in __tpy__]\n", 2),
    # CPython inlines a list comprehension; its local target must not hide
    # the next comprehension's read of the global.
    ("a = [1 for __tpy__ in z]\nb = [__tpy__ for y in w]\n", 2),
    ("def f():\n    a = [1 for __tpy__ in z]\n    return [__tpy__ for y in w]\n", 3),
    ("class C:\n    __tpy__ = 1\n    y = [__tpy__ for i in z]\n", 3),
    ("print(__\uff54py__)\n", 2),
    # Mutating the dict reads the name; binding it is the other error.
    ('__tpy__["k"] = 1\n', 2),
    ('__tpy__.update(mod="native")\n', 2),
    ('__tpy__["links"].append("m")\n', 2),
    ('__tpy__["includes"][0] = "x.hpp"\n', 2),
    ('del __tpy__["mod"]\n', 2),
    # `global` alone binds nothing: this function reads the module's dict.
    ('def f():\n    global __tpy__\n    return __tpy__["mod"]\n', 3),
    # Type-parameter scopes, and what runs in the enclosing scope.
    ("type X = __tpy__\n", 2),
    ("type X[T] = __tpy__\n", 2),
    ("def f[T: __tpy__]():\n    pass\n", 2),
    ("class C[T](__tpy__):\n    pass\n", 2),
    ("@__tpy__\ndef f():\n    pass\n", 2),
    ("def f(x=__tpy__):\n    pass\n", 2),
    ("g = lambda x=__tpy__: 0\n", 2),
    # tpyc resolves a function-body annotation; CPython records its names
    # only up to 3.13, so the verdict must not follow the running version.
    ("def f():\n    y: __tpy__ = 1\n    return y\n", 3),
    ("def f():\n    y: int = 1\n    def g(x: int):\n        return __tpy__\n    return g\n", 5),
    ("async def f():\n    y: __tpy__ = 1\n", 3),
    ("def f():\n    class C:\n        y: __tpy__\n    return C\n", 4),
])
def test_reads(tail: str, line: int) -> None:
    err = _error('__tpy__ = {"mod": "ext"}\n' + tail)
    assert err.message == _READ
    assert err.lineno == line


def test_the_error_points_at_the_first_occurrence_after_the_header():
    # The verdict is the module's; a function local of the name earlier in
    # the file takes the line.
    err = _error('__tpy__ = {"mod": "ext"}\ndef g():\n    __tpy__ = 1\nprint(__tpy__)\n')
    assert err.message == _READ and err.lineno == 3


def test_multi_line_header_keeps_the_read_line():
    err = _error('__tpy__ = {"mod": "ext",\n           "links": []}\nprint(__tpy__)\n')
    assert err.message == _READ and err.lineno == 3


def test_comprehension_targets_are_comprehension_locals():
    assert _read(
        '__tpy__ = {"mod": "ext"}\n'
        "x = [__tpy__ for __tpy__ in z]\n"
        "y = {__tpy__: 1 for __tpy__ in z}\n"
        "def f():\n"
        "    return [__tpy__ for __tpy__ in z]\n").kind == "ext"


def test_macro_module_reads_and_mutates_its_own_settings():
    s = _read('__tpy__ = {"mod": "macro"}\nKIND = __tpy__["mod"]\n__all__ = ["KIND", "__tpy__"]\n')
    assert s.kind == "macro"
    # A mutation runs under CPython; tpyc has already read the literal.
    s = _read(
        '__tpy__ = {"mod": "macro", "links": [], "includes": ["a.hpp"]}\n'
        '__tpy__["links"].append("m")\n'
        '__tpy__["includes"][0] = "b.hpp"\n'
        '__tpy__.update(cpp_namespace="n")\n'
        "def f():\n"
        "    global __tpy__\n"
        '    return __tpy__["mod"]\n')
    assert s.kind == "macro" and s.link_libs == [] and s.includes == [("a.hpp", None)]
    # Binding the name stays an error in every kind.
    err = _error('__tpy__ = {"mod": "macro"}\n__tpy__ = {}\n')
    assert err.message == _REBIND and err.lineno == 2
    err = _error('__tpy__ = {"mod": "macro"}\ndef f():\n    global __tpy__\n    __tpy__ = {}\n')
    assert err.message == _REBIND and err.lineno == 3


def test_future_annotations_are_not_reads():
    future = "from __future__ import annotations\n"
    assert _read(future + '__tpy__ = {"mod": "ext"}\ndef f(x: __tpy__):\n    pass\n').kind == "ext"
    assert _read(future + '__tpy__ = {"mod": "ext"}\ndef f():\n    y: __tpy__ = 1\n').kind == "ext"
    # A relative `.__future__` module is not the future statement.
    err = _error('__tpy__ = {"mod": "ext"}\nfrom .__future__ import annotations\ny: __tpy__ = 1\n')
    assert err.message == _READ and err.lineno == 3


def test_non_ascii_module_without_uses_skips_the_scope_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("symtable ran")
    monkeypatch.setattr(symtable, "symtable", fail)
    s = _read('"""D\u00e9j\u00e0 vu."""\n__tpy__ = {"mod": "ext"}\ns = "\u00e9"\n')
    assert s.kind == "ext"
    # An NFKC spelling of the name still counts as a use.
    with pytest.raises(AssertionError, match="symtable ran"):
        _read('__tpy__ = {"mod": "ext"}\nprint(__\uff54py__)\n')


def test_symtable_syntax_error_is_reported_at_its_line():
    err = _error('__tpy__ = {}\nprint("__tpy__")\ndef f():\n    y = 1\n    global y\n')
    assert "assigned to before global declaration" in err.message
    assert err.lineno == 5


def test_header_sharing_a_line_with_code_still_sees_the_read():
    err = _error('__tpy__ = {}; print(__tpy__)\n')
    assert err.message == _READ


def test_stale_directive_comment():
    err = _error('"""Doc."""\n# tpy: native_module\nimport os\n')
    assert err.message.startswith("'# tpy:' directives were replaced by __tpy__")
    assert err.lineno == 2
    # A C++ namespace comment is not a directive, nor is docstring text or
    # prose that does not name a retired directive.
    assert _read("# tpy::Foo is the runtime type\nx = 1\n").kind is None
    assert _read('"""Old form:\n# tpy: native_module\n"""\nx = 1\n').kind is None
    assert _read("# tpy: this program prints a greeting\nx = 1\n").kind is None
    err = _error('# tpy: include("<x.hpp>")\nx = 1\n')
    assert err.lineno == 1
    # A comment inside a multi-line header is still a comment.
    err = _error('__tpy__ = {"mod": "ext",\n           # tpy: native_module\n           "links": []}\n')
    assert err.lineno == 2


def test_macro_module_reaching_the_parser_is_rejected():
    with pytest.raises(ParseError) as exc:
        Parser().parse('__tpy__ = {"mod": "macro"}\n')
    assert '"mod": "macro"' in exc.value.message
    assert exc.value.lineno == 1


def test_module_kind(tmp_path: Path) -> None:
    ext = tmp_path / "ext.py"
    ext.write_text('"""Doc."""\n__tpy__ = {"mod": "ext"}\n')
    plain = tmp_path / "plain.py"
    plain.write_text("x = 1\n")
    assert module_kind(ext) == "ext"
    assert module_kind(plain) is None
