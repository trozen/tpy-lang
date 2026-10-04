"""`borrows=(...)` / `element_of=(...)` on `@native` / `@cpp_template`: the
declaration a bodyless binding makes about what its result borrows, and the
call-site decision sema stamps from it (`TpyCall.result_form`).

The rendered C++ and the runtime behavior are pinned by the snapshot cases
(`tests/cases/builtins/borrow_result`, `tests/cases/native/native_declared_borrows`);
these pin the two facts themselves -- the registration stamp and the
call-site verdict -- and every declaration error, of which a case can hold
only one."""
import dataclasses

import pytest

from . import get_lib_dir
from .compiler import Compiler
from .diagnostics import DiagnosticLevel, SemanticError
from .parse.nodes import ResultForm, TpyCall
from .parse.nodes import ParseError

_STDLIB_DIRS = [get_lib_dir() / "tpy"]

_RECORD = (
    "class P:\n"
    "    v: int\n"
    "\n"
    "    def __init__(self, v: int) -> None:\n"
    "        self.v = v\n"
    "\n"
    "\n"
    "def key_of(p: P) -> int:\n"
    "    return p.v\n"
    "\n"
    "\n"
)


def _entry(source: str):
    compiler = Compiler.from_source(source, lib_dirs=_STDLIB_DIRS)
    return [m for m in compiler.compile() if m.is_entry_point][0]


def _calls(node, out: list[TpyCall]) -> list[TpyCall]:
    """Every TpyCall under `node`, in field order."""
    if isinstance(node, TpyCall):
        out.append(node)
    if isinstance(node, list):
        for item in node:
            _calls(item, out)
    elif dataclasses.is_dataclass(node) and not isinstance(node, type):
        for f in dataclasses.fields(node):
            if f.name in ("resolved_function_info", "loc"):
                continue
            _calls(getattr(node, f.name), out)
    return out


def _min_max_calls(entry, func: str) -> list[TpyCall]:
    fn = next(f for f in entry.ast.functions if f.name == func)
    return [c for c in _calls(fn.body, [])
            if getattr(c.func, "name", None) in ("min", "max")]


def test_keyed_min_max_stubs_declare_their_operands():
    entry = _entry(
        _RECORD
        + "def f(a: P, b: P, c: P) -> int:\n"
        "    m = min(a, b, key=key_of)\n"
        "    n = max(a, b, c, key=key_of)\n"
        "    return m.v + n.v\n")
    two, three = _min_max_calls(entry, "f")
    assert two.resolved_function_info.root.return_borrows_from == frozenset({0, 1})
    assert three.resolved_function_info.root.return_borrows_from == frozenset({0, 1, 2})
    assert two.resolved_function_info.root.borrow_declared


def test_element_of_declaration_is_recorded_apart():
    # A user binding's element_of= names a parameter the result is an
    # ELEMENT of: recorded in element_borrows_from, inside the union.
    entry = _entry(
        "from tpy.extern import native\n"
        + _RECORD
        + "@native(borrows=(\"d\",), element_of=(\"items\",))\n"
        "def first(items: list[P], d: P) -> P: ...\n"
        "\n"
        "\n"
        "def f(ps: list[P], d: P) -> int:\n"
        "    return first(ps, d).v\n")
    call = next(c for c in _calls(
        next(fn for fn in entry.ast.functions if fn.name == "f").body, [])
        if getattr(c.func, "name", None) == "first")
    root = call.resolved_function_info.root
    assert root.borrow_declared
    assert root.return_borrows_from == frozenset({0, 1})
    assert root.element_borrows_from == frozenset({0})
    assert call.result_form is ResultForm.BORROW


def test_call_site_verdict():
    entry = _entry(
        _RECORD
        + "class O:\n"
        "    x: P\n"
        "\n"
        "    def __init__(self, x: P) -> None:\n"
        "        self.x = x\n"
        "\n"
        "\n"
        "def f(a: P, b: P, o: O, ps: list[P]) -> int:\n"
        "    m = min(a, b, key=key_of)\n"                       # borrow
        "    n = min(o.x, ps[0], key=key_of)\n"                 # borrow
        "    k = min(min(a, b, key=key_of), b, key=key_of)\n"   # borrow, borrow
        "    t = min(a, P(0), key=key_of)\n"                    # value, warned
        "    u = min(P(1), P(2), key=key_of)\n"                 # value, silent
        "    s = min(3, 4, key=lambda n: n)\n"                  # value type
        "    r = min(a, P(-9), key=key_of).v\n"                 # value, read
        "    return m.v + n.v + k.v + t.v + u.v + s + r\n")
    calls = _min_max_calls(entry, "f")
    B, RV, ND = (ResultForm.BORROW, ResultForm.REFERENCE_VALUE,
                 ResultForm.NOT_DECLARED)
    assert [c.result_form for c in calls] == [B, B, B, B, RV, RV, ND, RV]
    assert [c.copy_observable for c in calls] == [
        False, False, False, False, True, False, False, True]
    # Only the binding that holds the value copies; the in-place read does not.
    warnings = [(d.loc.line, d.message) for d in entry.analyzer.diagnostics
                if d.level == DiagnosticLevel.WARNING
                and "min(" not in d.message and "into local" in d.message]
    assert warnings == [
        (23, "copies P into local 't'; use copy() to make this explicit")]
    assert not [d for d in entry.analyzer.diagnostics
                if d.level == DiagnosticLevel.WARNING and d.loc.line == 25]


def test_element_of_parameter_verdict():
    entry = _entry(
        "from typing import Iterator\n"
        "from tpy import Span\n"
        + _RECORD
        + "def walk(ps: list[P]) -> Iterator[P]:\n"
        "    for p in ps:\n"
        "        yield p\n"
        "\n"
        "\n"
        "class Bag:\n"
        "    items: list[P]\n"
        "\n"
        "    def __init__(self) -> None:\n"
        "        self.items = [P(1)]\n"
        "\n"
        "    def __iter__(self) -> Iterator[P]:\n"
        "        for p in self.items:\n"
        "            yield p\n"
        "\n"
        "\n"
        "def f(ps: list[P], xs: Span[P], b: Bag) -> int:\n"
        "    it = walk(ps)\n"
        "    t = 0\n"
        "    t += max(ps, key=key_of).v\n"             # container: borrow
        "    t += max(xs, key=key_of).v\n"             # span: borrow
        "    t += max(it, key=key_of).v\n"             # named iterator: value
        "    t += max(walk(ps), key=key_of).v\n"       # temporary iterator
        "    t += max(b, key=key_of).v\n"              # separate iterator
        "    t += max([P(1), P(2)], key=key_of).v\n"   # fresh container
        "    return t\n")
    # Only a container lends its elements: an iterator's step is valid only
    # until the next step, named or not.
    calls = _min_max_calls(entry, "f")
    B, C = ResultForm.BORROW, ResultForm.COPY
    assert [c.result_form for c in calls] == [
        B, B, C, C, C, ResultForm.REFERENCE_VALUE]
    assert [c.copy_observable for c in calls] == [
        False, False, True, True, True, False]


def test_nocopy_value_result_is_an_error():
    with pytest.raises(SemanticError,
                       match="cannot copy non-copyable type 'N' into local 't'"):
        _entry(
            "from tpy import nocopy\n"
            "@nocopy\n"
            "class N:\n"
            "    def __init__(self, v: int) -> None:\n"
            "        self.v = v\n"
            "\n"
            "\n"
            "def f(a: N) -> int:\n"
            "    t = min(a, N(0), key=lambda n: n.v)\n"
            "    return t.v\n")


_NATIVE = "from tpy.extern import native, cpp_template\nfrom tpy import int32\n"


@pytest.mark.parametrize("decl, message", [
    ("@native(borrows=(\"nope\",))\n"
     "def f(a: list[int32]) -> list[int32]: ...\n",
     "name 'nope', which is not a parameter of 'f'"),
    ("@native(borrows=(\"a\", \"a\"))\n"
     "def f(a: list[int32]) -> list[int32]: ...\n",
     "name 'a' twice"),
    ("@cpp_template(\"f({0}, {1})\", borrows=(\"n\",))\n"
     "def f(a: list[int32], n: int32) -> list[int32]: ...\n",
     "name 'n', whose type 'int32' holds no storage"),
    ("@native(borrows=(\"self\",))\n"
     "def f(a: list[int32]) -> list[int32]: ...\n",
     "name 'self', which is not a parameter of 'f'"),
])
def test_declaration_errors(decl, message):
    with pytest.raises(SemanticError, match=message):
        _entry(_NATIVE + decl)


@pytest.mark.parametrize("decl, message", [
    ("@native(borrows=())\n"
     "def f(a: list[int32]) -> list[int32]: ...\n",
     "must name at least one parameter"),
    ("@native(borrows=\"a\")\n"
     "def f(a: list[int32]) -> list[int32]: ...\n",
     "expects tuple"),
    ("@native(borrows=(\"a\",), element_of=(\"a\",))\n"
     "def f(a: list[int32]) -> list[int32]: ...\n",
     "names 'a' in both borrows= and element_of="),
    ("@native\nclass Node:\n"
     "    @native(borrows=(\"other\",))\n"
     "    def pick(self, other: Node) -> Node: ...\n",
     "not supported on a method yet"),
    ("@native(borrows=(1,))\n"
     "def f(a: list[int32]) -> list[int32]: ...\n",
     "expects a tuple of names"),
    ("@native(\"Node\", borrows=(\"a\",))\nclass Node:\n"
     "    v: int32\n",
     "only valid on a function or method stub"),
])
def test_declaration_spelling_errors(decl, message):
    with pytest.raises(ParseError, match=message):
        _entry(_NATIVE + decl)


_ELEM = (
    "from typing import Iterator\n"
    "from tpy import Own, ReferenceType, ComparableRef, dispatch\n"
    + _RECORD
)


@pytest.mark.parametrize("body, message", [
    # A tuple holding a class instance is neither a value nor a reference
    # type, at every form that hands an element back.
    ("def f(xs: list[tuple[P, int]]) -> None:\n    print(max(xs)[1])\n",
     "holds a class instance, which this call would copy"),
    ("def f(it: Iterator[tuple[P, int]], d: tuple[P, int]) -> None:\n"
     "    print(next(it, d)[1])\n",
     "holds a class instance, which this call would copy"),
    # An Optional element.
    ("def f(xs: list[P | None]) -> None:\n"
     "    print(max(xs, key=lambda p: 0) is None)\n",
     "holds a class instance, which this call would copy"),
])
def test_still_refused_element_shapes(body, message):
    with pytest.raises(SemanticError, match=message):
        _entry(_ELEM + body)


@pytest.mark.parametrize("arg, ok", [
    ("1", False), ("\"s\"", False), ("None", False),
    ("P(1)", True), ("[1]", True),
])
def test_reference_type_marker(arg, ok):
    src = (_ELEM
           + "def touch[T: ReferenceType](x: T) -> int:\n    return 0\n\n\n"
           + f"def f() -> int:\n    return touch({arg})\n")
    if ok:
        _entry(src)
    else:
        with pytest.raises(SemanticError):
            _entry(src)


def test_bodied_dispatch_pair_differing_only_in_bound_is_rejected():
    with pytest.raises(SemanticError, match="identical parameter types"):
        _entry(_ELEM
               + "@dispatch\n"
               + "def pick[T: ReferenceType](a: T) -> T:\n    return a\n\n\n"
               + "@dispatch\n"
               + "def pick[T: ComparableRef](a: T) -> T:\n    return a\n")
