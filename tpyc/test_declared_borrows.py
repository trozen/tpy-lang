"""`result={...}` on `@native` / `@cpp_template`: the declaration a bodyless
binding makes about what its result borrows, and the
call-site decision sema stamps from it (`TpyCallLike.result_form`), on a
free binding and on a method stub alike.

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
from .parse.nodes import ResultForm, TpyCall, TpyMethodCall
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
    # A user binding's result= "element" names a parameter the result is an
    # ELEMENT of: recorded in element_borrows_from, inside the union.
    entry = _entry(
        "from tpy.extern import native\n"
        + _RECORD
        + "@native(result={\"d\": \"borrow\", \"items\": \"element\"})\n"
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
        "    s = min(3, 4, key=lambda n: n)\n"                  # value
        "    r = min(a, P(-9), key=key_of).v\n"                 # value, read
        "    return m.v + n.v + k.v + t.v + u.v + s + r\n")
    calls = _min_max_calls(entry, "f")
    B, RV, V = (ResultForm.BORROW, ResultForm.REFERENCE_VALUE,
                ResultForm.VALUE)
    assert [c.result_form for c in calls] == [B, B, B, B, RV, RV, V, RV]
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


_NATIVE = "from tpy.extern import native, cpp_template, export\nfrom tpy import int32, readonly\n"


@pytest.mark.parametrize("decl, message", [
    ("@native(result={\"nope\": \"borrow\"})\n"
     "def f(a: list[int32]) -> list[int32]: ...\n",
     "names 'nope', which is not a parameter of 'f'"),
    ("@cpp_template(\"f({0}, {1})\", result={\"a\": \"none\", \"n\": \"borrow\"})\n"
     "def f(a: list[int32], n: int32) -> list[int32]: ...\n",
     "names 'n', whose type 'int32' holds no storage"),
    ("@native(result={\"self\": \"borrow\"})\n"
     "def f(a: list[int32]) -> list[int32]: ...\n",
     "names 'self', which is not a parameter of 'f'"),
    # A by-value scalar holds no storage, whatever the result.
    ("@native(\"pick\", result={\"a\": \"borrow\", \"b\": \"borrow\"})\n"
     "def f(a: int32, b: int32) -> int32: ...\n",
     "names 'a', whose type 'int32' holds no storage"),
    # A view result over a by-reference owned leaf: only an owned result may be the argument.
    ("from tpy import StrView\n"
     "@native(\"first\", result={\"a\": \"borrow\"})\n"
     "def f(a: int) -> StrView: ...\n",
     "names 'a', whose type 'int' holds no storage"),
    # An owned leaf has no elements a result could be handed out of.
    ("@native(\"first\", result={\"a\": \"element\"})\n"
     "def f(a: int) -> int: ...\n",
     "names 'a', whose type 'int' holds no storage"),
    # A map declaring a borrow names every parameter that can lend: what
    # the result borrows of each is never left to the reader.
    ("@native(result={\"a\": \"borrow\"})\n"
     "def f(a: list[int32], key: str) -> list[int32]: ...\n",
     "result= does not say what the result holds of 'key' \\(str\\); "
     "add \"key\": \"none\" if nothing"),
    # "none" is read only against a declared borrow / element.
    ("@native(result={\"a\": \"none\"})\n"
     "def f(a: list[int32], b: list[int32]) -> list[int32]: ...\n",
     "gives 'a' \"none\" on 'f', which has no effect unless the map also "
     "declares a \"borrow\" or \"element\""),
    ("@native(result={\"a\": \"adopts\", \"b\": \"none\"})\n"
     "def f(a: list[int32], b: list[int32]) -> list[int32]: ...\n",
     "gives 'b' \"none\" on 'f', which has no effect unless the map also "
     "declares a \"borrow\" or \"element\""),
])
def test_declaration_errors(decl, message):
    with pytest.raises(SemanticError, match=message):
        _entry(_NATIVE + decl)


@pytest.mark.parametrize("ptype", ["int", "String"])
def test_owned_leaf_by_reference_with_an_owned_result_is_admitted(ptype):
    # `std::min` over `const BigInt&` hands back one of its arguments.
    entry = _entry(
        _NATIVE + "from tpy import String\n"
        f"@native(\"pick\", result={{\"a\": \"borrow\", \"b\": \"borrow\"}})\n"
        f"def pick(a: {ptype}, b: {ptype}) -> {ptype}: ...\n"
        "\n\n"
        f"def g(x: {ptype}, y: {ptype}) -> {ptype}:\n"
        "    return pick(x, y)\n")
    call, = [c for c in _calls(next(f for f in entry.ast.functions if f.name == "g").body, [])
             if getattr(c.func, "name", None) == "pick"]
    root = call.resolved_function_info.root
    assert root.borrow_declared and root.return_borrows_from == frozenset({0, 1})


def test_view_result_over_a_string_parameter_is_admitted():
    # A `String` passes by reference: a view result may point into it.
    entry = _entry(
        _NATIVE + "from tpy import String, StrView\n"
        "@native(\"first\", result={\"a\": \"borrow\"})\n"
        "def first(a: String) -> StrView: ...\n"
        "\n\n"
        "def g(x: String) -> int32:\n"
        "    return len(first(x))\n")
    call, = [c for c in _calls(next(f for f in entry.ast.functions if f.name == "g").body, [])
             if getattr(c.func, "name", None) == "first"]
    root = call.resolved_function_info.root
    assert root.borrow_declared and root.return_borrows_from == frozenset({0})


def test_builtin_min_max_round_over_int_declare_their_operands():
    entry = _entry(
        "def f(a: int, b: int, c: int) -> int:\n"
        "    return min(a, b) + max(a, b, c) + round(a)\n")
    calls = {c.func.name: c for c in _calls(next(f for f in entry.ast.functions if f.name == "f").body, [])
             if getattr(c.func, "name", None) in ("min", "max", "round")}
    assert calls["min"].resolved_function_info.root.return_borrows_from == frozenset({0, 1})
    assert calls["max"].resolved_function_info.root.return_borrows_from == frozenset({0, 1, 2})
    assert calls["round"].resolved_function_info.root.return_borrows_from == frozenset({0})
    assert all(c.resolved_function_info.root.borrow_declared for c in calls.values())


def _method_calls(node, out: list[TpyMethodCall]) -> list[TpyMethodCall]:
    """Every TpyMethodCall under `node`, in field order."""
    if isinstance(node, TpyMethodCall):
        out.append(node)
    if isinstance(node, list):
        for item in node:
            _method_calls(item, out)
    elif dataclasses.is_dataclass(node) and not isinstance(node, type):
        for f in dataclasses.fields(node):
            if f.name in ("resolved_function_info", "loc"):
                continue
            _method_calls(getattr(node, f.name), out)
    return out


_NODE = (
    "@native\n"
    "class Node:\n"
    "    v: int32\n"
    "\n"
)


def test_method_declaration_stamps_receiver_as_minus_one():
    # `self` names the receiver (index -1); the declaration replaces the
    # receiver inference an undeclared native method gets.
    entry = _entry(
        _NATIVE + _NODE
        + "    @native(result={\"other\": \"borrow\"})\n"
        "    def pick(self, other: Node) -> Node: ...\n"
        "\n"
        "    @native(result={\"self\": \"element\"})\n"
        "    def first(self) -> Node: ...\n"
        "\n"
        "    @native\n"
        "    def peek(self) -> Node: ...\n"
        "\n"
        "\n"
        "def f(a: Node, b: Node, d: dict[str, Node]) -> int32:\n"
        "    return (a.pick(b).v + a.first().v + a.peek().v\n"
        "            + d.get(\"k\", b).v)\n")
    fn = next(f for f in entry.ast.functions if f.name == "f")
    pick, first, peek, get = _method_calls(fn.body, [])
    root = pick.resolved_function_info.root
    assert root.borrow_declared
    assert root.return_borrows_from == frozenset({0})
    root = first.resolved_function_info.root
    assert root.return_borrows_from == frozenset({-1})
    assert root.element_borrows_from == frozenset({-1})
    root = peek.resolved_function_info.root
    assert not root.borrow_declared
    assert root.return_borrows_from == frozenset({-1})
    assert peek.result_form is ResultForm.NOT_DECLARED
    root = get.resolved_function_info.root
    assert root.borrow_declared
    assert root.return_borrows_from == frozenset({-1, 1})
    assert root.element_borrows_from == frozenset({-1})
    assert pick.result_form is ResultForm.BORROW
    assert get.result_form is ResultForm.BORROW


def test_method_call_site_verdict():
    entry = _entry(
        _RECORD
        + "def f(d: dict[str, P], fb: P, c: dict[str, int]) -> int:\n"
        "    m = d.get(\"a\", fb)\n"              # both lend: borrow
        "    r = d.get(\"a\", P(5)).v\n"          # temporary default: value
        "    t = d.get(\"a\", P(6))\n"            # held: warned copy
        "    n = c.get(\"a\", 7)\n"               # value
        "    return m.v + r + t.v + n\n")
    fn = next(f for f in entry.ast.functions if f.name == "f")
    calls = [c for c in _method_calls(fn.body, []) if c.method == "get"]
    B, RV, V = (ResultForm.BORROW, ResultForm.REFERENCE_VALUE,
                ResultForm.VALUE)
    assert [c.result_form for c in calls] == [B, RV, RV, V]
    assert [c.copy_observable for c in calls] == [False, True, True, False]
    warnings = [(d.loc.line, d.message) for d in entry.analyzer.diagnostics
                if d.level == DiagnosticLevel.WARNING
                and "into local" in d.message]
    assert warnings == [
        (15, "copies P into local 't'; use copy() to make this explicit")]


def test_open_t_composite_result_is_a_hedged_copy():
    # A value-shaped result naming an open `T` is decided as a bare open `T`
    # is: a hedged copy the binding warns about. Value payloads alone stay
    # a plain value.
    entry = _entry(
        _RECORD
        + "def f[V](d: dict[str, V | None], fb: V | None,\n"
        "         t: dict[str, tuple[V, int]], tb: tuple[V, int],\n"
        "         c: dict[str, int | None]) -> None:\n"
        "    m = d.get(\"a\", fb)\n"
        "    u = t.get(\"a\", tb)\n"
        "    n = c.get(\"a\", None)\n")
    fn = next(f for f in entry.ast.functions if f.name == "f")
    calls = [c for c in _method_calls(fn.body, []) if c.method == "get"]
    C, V = ResultForm.COPY, ResultForm.VALUE
    assert [c.result_form for c in calls] == [C, C, V]
    assert [c.copy_observable for c in calls] == [True, True, False]
    warnings = sorted(d.message for d in entry.analyzer.diagnostics
                      if d.level == DiagnosticLevel.WARNING
                      and "into local" in d.message)
    assert warnings == [
        "may copy V | None into local 'm' if not a value type; "
        "use copy() to make this explicit",
        "may copy tuple[V, int] into local 'u' if not a value type; "
        "use copy() to make this explicit"]


@pytest.mark.parametrize("decl, message", [
    ("    @staticmethod\n"
     "    @native(result={\"self\": \"borrow\", \"other\": \"none\"})\n"
     "    def make(other: Node) -> Node: ...\n",
     "names 'self', which names no parameter of the static method"),
    ("    @classmethod\n"
     "    @native(result={\"self\": \"borrow\", \"other\": \"none\"})\n"
     "    def make(cls, other: Node) -> Node: ...\n",
     "names 'self', which names no parameter of the class method"),
    ("    @native(result={\"self\": \"borrow\"})\n"
     "    def take(self: Own[Self]) -> Node: ...\n",
     "consumes its receiver"),
    ("    @native(result={\"other\": \"borrow\"})\n"
     "    def __add__(self, other: Node) -> Node: ...\n",
     "an operator method is reached through its own syntax"),
    ("    @property\n"
     "    @native(result={\"self\": \"borrow\"})\n"
     "    def me(self) -> Node: ...\n",
     "a property is reached through its own syntax"),
    ("    @native(result={\"nope\": \"borrow\"})\n"
     "    def pick(self, other: Node) -> Node: ...\n",
     "names 'nope', which is not a parameter of 'pick'"),
    ("    @native(result={\"self\": \"borrow\"})\n"
     "    def pick(self, other: Node) -> Node: ...\n",
     "does not say what the result holds of 'other'"),
    # "none" says nothing unless the map declares a borrow, on the
    # receiver as on any parameter.
    ("    @native(result={\"self\": \"none\", \"other\": \"none\"})\n"
     "    def pick(self, other: Node) -> Node: ...\n",
     "gives 'self' \"none\" on 'pick', which has no effect unless the map "
     "also declares a \"borrow\" or \"element\""),
    ("    @native(\"size\", result={\"self\": \"none\"})\n"
     "    def size(self) -> int32: ...\n",
     "gives 'self' \"none\" on 'size', which has no effect"),
    ("    @native(args={\"nope\": \"stored\"})\n"
     "    def put(self, other: Node) -> None: ...\n",
     "args= names 'nope', which is not a parameter of 'put'"),
])
def test_method_declaration_errors(decl, message):
    with pytest.raises(SemanticError, match=message):
        _entry(_NATIVE + "from tpy import Own\nfrom typing import Self\n"
               + _NODE + decl)


@pytest.mark.parametrize("decl, message", [
    ("@native(result={})\n"
     "def f(a: list[int32]) -> list[int32]: ...\n",
     "must name at least one parameter"),
    ("@native(result=\"a\")\n"
     "def f(a: list[int32]) -> list[int32]: ...\n",
     "expects a dict of names to strings"),
    ("@native(result={\"a\": 1})\n"
     "def f(a: list[int32]) -> list[int32]: ...\n",
     "expects a dict of names to strings"),
    # A dict literal keeps only a repeated key's last value, so a repeat
    # is refused rather than read as that value alone.
    ("@native(result={\"a\": \"borrow\", \"b\": \"borrow\", \"a\": \"none\"})\n"
     "def f(a: list[int32], b: list[int32]) -> list[int32]: ...\n",
     "result= names 'a' twice"),
    # Borrow and element on one parameter is only spellable as a repeat.
    ("@native(result={\"a\": \"borrow\", \"a\": \"element\"})\n"
     "def f(a: list[int32]) -> list[int32]: ...\n",
     "result= names 'a' twice"),
    ("@native(\"Node\")\nclass Node:\n"
     "    v: int32\n"
     "    @native(result={\"self\": \"borrow\", \"other\": \"none\", \"self\": \"element\"})\n"
     "    def pick(self, other: Node) -> Node: ...\n",
     "result= names 'self' twice"),
    ("@native(\"Node\")\nclass Node:\n"
     "    v: int32\n"
     "    @native(args={\"n\": \"stored\", \"n\": \"stored\"})\n"
     "    def put(self, n: Node) -> None: ...\n",
     "args= names 'n' twice"),
    # One binding decorator per stub: a second one's name, call form and
    # contract keywords would replace the first's silently.
    ("@native(\"f1\", result={\"a\": \"borrow\"})\n"
     "@cpp_template(\"({0})\", result={\"a\": \"none\"})\n"
     "def f(a: list[int32]) -> list[int32]: ...\n",
     "Function 'f' cannot have both @native and @cpp_template"),
    ("@cpp_template(\"({0})\")\n"
     "@native(\"f1\")\n"
     "def f(a: list[int32]) -> list[int32]: ...\n",
     "Function 'f' cannot have both @cpp_template and @native"),
    ("@cpp_template(\"ga({0})\")\n"
     "@cpp_template(\"gb({0})\")\n"
     "def f(a: int32) -> int32: ...\n",
     "Function 'f' cannot have both @cpp_template and @cpp_template"),
    ("@native(\"Node\")\nclass Node:\n"
     "    v: int32\n"
     "    @native(\"pick\", result={\"other\": \"borrow\"})\n"
     "    @cpp_template(\"{self}.pick({0})\", result={\"other\": \"none\"})\n"
     "    def pick(self, other: Node) -> Node: ...\n",
     "Method 'pick' cannot have both @native and @cpp_template"),
    ("@native(\"Node\")\nclass Node:\n"
     "    v: int32\n"
     "    @cpp_template(\"{self}.pick({0})\")\n"
     "    @native(\"pick\")\n"
     "    def pick(self, other: Node) -> Node: ...\n",
     "Method 'pick' cannot have both @cpp_template and @native"),
    ("@export(binding=\"C\")\n"
     "@cpp_template(\"({0})\")\n"
     "def f(a: int32) -> int32: ...\n",
     "Function 'f' cannot have both @export and @cpp_template"),
    ("@native(\"f1\", binding=\"C\")\n"
     "@export(binding=\"C\")\n"
     "def f(a: int32) -> int32: ...\n",
     "Function 'f' cannot have both @native and @export"),
    ("@native(\"Node\")\nclass Node:\n"
     "    v: int32\n"
     "    @cpp_template(\"{self}.a()\")\n"
     "    @cpp_template(\"{self}.b()\")\n"
     "    def g(self) -> int32: ...\n",
     "Method 'g' cannot have both @cpp_template and @cpp_template"),
    ("@native(result={\"a\": \"borrows\"})\n"
     "def f(a: list[int32]) -> list[int32]: ...\n",
     "gives 'a' the relation 'borrows'; a result relation is"),
    ("@native(\"Node\", result={\"a\": \"borrow\"})\nclass Node:\n"
     "    v: int32\n",
     "only valid on a function or method stub"),
    ("@native(\"Node\", args={\"self\": \"writes\"})\nclass Node:\n"
     "    v: int32\n",
     "only valid on a function or method stub"),
    ("@native(args={\"a\": \"stored\"})\n"
     "def f(a: list[int32]) -> None: ...\n",
     "args=...\\) is only valid on a method stub: a free function"),
    ("@native(\"Node\")\nclass Node:\n"
     "    v: int32\n"
     "    @native(args={\"other\": \"read\"})\n"
     "    def put(self, other: Node) -> None: ...\n",
     "gives 'other' the effect 'read'; an argument effect is"),
    ("@native(\"Node\")\nclass Node:\n"
     "    v: int32\n"
     "    @native(args={\"other\": \"writes\"})\n"
     "    def put(self, other: Node) -> None: ...\n",
     "only the receiver, 'self', is declared written"),
    ("@native(\"Node\")\nclass Node:\n"
     "    v: int32\n"
     "    @native(args={\"self\": \"stored\"})\n"
     "    def put(self, other: Node) -> None: ...\n",
     "the receiver is declared \"writes\""),
    ("@native(\"Node\")\nclass Node:\n"
     "    v: int32\n"
     "    @native(args={\"a\": \"stored\", \"b\": \"compared\"})\n"
     "    def put(self, a: Node, b: Node) -> None: ...\n",
     "declares both \"compared\" and \"stored\" arguments"),
])
def test_declaration_spelling_errors(decl, message):
    with pytest.raises(ParseError, match=message):
        _entry(_NATIVE + decl)


@pytest.mark.parametrize("decl, message", [
    ("@export\n@native(\"abs\", binding=\"C\")\n"
     "def f(a: int32) -> int32: ...\n",
     "cannot have both @export and @native"),
    ("@cpp_template(\"({0})\")\n@export\n"
     "def f(a: int32) -> int32: ...\n",
     "cannot have both @cpp_template and @export"),
    ("@export\n@export\n"
     "def f(a: int32) -> int32:\n    return a\n",
     "cannot have both @export and @export"),
])
def test_ext_module_export_is_a_binding_decorator(decl, message):
    # Inside an ext module `@export` exposes the function to CPython, and a
    # bodyless stub has nothing to expose.
    with pytest.raises(ParseError, match=message):
        _entry('__tpy__ = {"mod": "ext"}\n'
               "from tpy.extern import native, cpp_template, export\n"
               "from tpy import int32\n" + decl)


@pytest.mark.parametrize("decl, message", [
    ("@native(result={\"nope\": \"adopts\"})\n"
     "def f(a: list[int32]) -> list[int32]: ...\n",
     "result= names 'nope', which is not a parameter of 'f'"),
    ("@cpp_template(\"f({0}, {1})\", result={\"n\": \"adopts\"})\n"
     "def f(a: list[int32], n: int32) -> list[int32]: ...\n",
     "result= names 'n' \"adopts\", whose type 'int32' holds no storage"),
])
def test_adopts_errors(decl, message):
    with pytest.raises(SemanticError, match=message):
        _entry(_NATIVE + decl)


@pytest.mark.parametrize("decl, message", [
    # A binding with a TPy body declares nothing for it to take over.
    ("@native(result={\"a\": \"adopts\"})\n"
     "def f(a: list[int32]) -> list[int32]:\n"
     "    return a\n",
     "must have `...` body"),
    ("@native(\"Node\")\nclass Node:\n"
     "    v: int32\n"
     "    @native(result={\"other\": \"adopts\"})\n"
     "    def pick(self, other: Node) -> Node: ...\n",
     "gives 'other' \"adopts\", which is only valid on a free function stub"),
])
def test_adopts_spelling_errors(decl, message):
    with pytest.raises(ParseError, match=message):
        _entry(_NATIVE + decl)


def test_adopts_stamps_the_parameters():
    """`result=` "adopts" lands as the binding's adopted parameter indices;
    a map declaring no borrow need not name the other lendable `a`."""
    entry = _entry(_NATIVE + "@cpp_template(\"g({0}, {1})\", result={\"b\": \"adopts\"})\n"
                   "def g(a: list[int32], b: list[int32]) -> int32: ...\n"
                   "def f(a: list[int32]) -> int32:\n"
                   "    return g(a, a)\n")
    fn = next(f for f in entry.ast.functions if f.name == "f")
    call, = _calls(fn.body, [])
    assert call.resolved_function_info.root.adopted_params == frozenset({1})


_ELEM = (
    "from typing import Iterator\n"
    "from tpy import Own, ReferenceType, ComparableRef, dispatch\n"
    + _RECORD
)


@pytest.mark.parametrize("body, message", [
    # A tuple holding a class instance is neither a value nor a reference
    # type, at every form that hands an element back.
    ("def f(xs: list[tuple[P, int]]) -> None:\n    print(max(xs)[1])\n",
     "holds a reference type, which this call would copy"),
    ("def f(it: Iterator[tuple[P, int]], d: tuple[P, int]) -> None:\n"
     "    print(next(it, d)[1])\n",
     "holds a reference type, which this call would copy"),
    # An Optional element.
    ("def f(xs: list[P | None]) -> None:\n"
     "    print(max(xs, key=lambda p: 0) is None)\n",
     "holds a reference type, which this call would copy"),
    # A declared method's value-shaped result holding one: a union and a
    # tuple dict value.
    ("def f(d: dict[str, P | int], fb: P | int) -> None:\n"
     "    m = d.get(\"a\", fb)\n",
     "'P \\| int' holds a reference type, which 'get\\(...\\)' would copy"),
    ("def f(d: dict[str, tuple[P, int]], fb: tuple[P, int]) -> None:\n"
     "    print(d.get(\"a\", fb)[1])\n",
     "'tuple\\[P, int\\]' holds a reference type, which 'get\\(...\\)' "
     "would copy"),
    # A concrete class instance beside an open `T` is a CERTAIN copy, so it
    # takes the refusal, not the generic hedge.
    ("def f[V](d: dict[str, tuple[V, P]], fb: tuple[V, P]) -> None:\n"
     "    print(d.get(\"a\", fb)[1].v)\n",
     "'tuple\\[V, P\\]' holds a reference type, which 'get\\(...\\)' "
     "would copy"),
    ("def f[V](d: dict[str, list[V] | None], fb: list[V] | None) -> None:\n"
     "    m = d.get(\"a\", fb)\n",
     "'list\\[V\\] \\| None' holds a reference type, which "
     "'get\\(...\\)' would copy"),
    ("def f[V](a: tuple[V, P], b: tuple[V, P]) -> None:\n"
     "    print(min(a, b, key=lambda p: p[1].v)[1].v)\n",
     "'tuple\\[V, P\\]' holds a reference type, which 'min\\(...\\)' "
     "would copy"),
    # The open Optional copy returned whole: no owning return slot holds
    # an open payload yet, so the message suggests none.
    ("def f[V](d: dict[str, V | None], fb: V | None) -> V | None:\n"
     "    return d.get(\"a\", fb)\n",
     "Cannot return the result of 'get\\(...\\)' as 'V \\| None': in a "
     "generic body it is a copy"),
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


_BAG = (
    "from typing import Iterator\n"
    "from tpy import Own, NativeIterable, readonly\n"
    + _RECORD
    + "@native(\"::Bag\", elements=True)\n"
    "class Bag[T](NativeIterable[T]):\n"
    "    def __init__(self) -> None: ...\n"
    "\n"
    "    @native(\"tpy::__iter__\", function=True)\n"
    "    @readonly\n"
    "    def __iter__(self) -> Iterator[T]: ...\n"
    "\n"
    "    @native(\"grow_or\", result={\"self\": \"element\", \"d\": \"borrow\"})\n"
    "    def grow_or(self, d: T) -> T: ...\n"
    "\n"
    "\n"
    "def show(a: P, b: P) -> None:\n"
    "    print(a.v, b.v)\n"
    "\n"
    "\n"
)


def test_mutating_element_method_twice_in_one_statement_is_refused():
    # A container is not an iterator, but a method that mutates it may move
    # the element the first call handed back, so two calls with fresh
    # defaults in one statement are refused like two advances.
    with pytest.raises(SemanticError,
                       match="'grow_or\\(...\\)' advances 'b' twice"):
        _entry(_NATIVE + _BAG
               + "def f() -> None:\n"
               "    b = Bag[P]()\n"
               "    show(b.grow_or(P(0)), b.grow_or(P(0)))\n")


def test_readonly_container_get_twice_in_one_statement_is_accepted():
    # `dict.get` is readonly: a second call leaves the first element in
    # place.
    entry = _entry(
        _NATIVE + _RECORD
        + "def f(d: dict[str, P]) -> None:\n"
        "    print(d.get(\"a\", P(0)).v, d.get(\"b\", P(0)).v)\n")
    fn = next(f for f in entry.ast.functions if f.name == "f")
    gets = [c for c in _method_calls(fn.body, []) if c.method == "get"]
    assert [c.result_form for c in gets] == [ResultForm.REFERENCE_VALUE] * 2


_TAKER = (
    "from tpy import readonly\n"
    "@native\n"
    "class Taker:\n"
    "    @readonly\n"
    "    @native(\"take\", result={\"xs\": \"element\", \"d\": \"borrow\"})\n"
    "    def take(self, xs: list[P], d: P) -> P: ...\n"
    "\n"
    "    @readonly\n"
    "    @native(\"take_ro\", result={\"xs\": \"element\", \"d\": \"borrow\"})\n"
    "    def take_ro(self, xs: readonly[list[P]], d: P) -> P: ...\n"
    "\n"
    "\n"
    "def show(a: P, b: P) -> None:\n"
    "    print(a.v, b.v)\n"
    "\n"
    "\n"
)


def test_readonly_receiver_does_not_vouch_for_a_lent_parameter():
    # `@readonly` speaks for the receiver only: a mutable `xs` the stub lends
    # an element of may be moved by the second call, so two calls with fresh
    # defaults in one statement are refused like two advances.
    with pytest.raises(SemanticError,
                       match="'take\\(...\\)' advances 'xs' twice"):
        _entry(_NATIVE + _RECORD + _TAKER
               + "def f(xs: list[P]) -> None:\n"
               "    t = Taker()\n"
               "    show(t.take(xs, P(0)), t.take(xs, P(0)))\n")


def test_readonly_parameter_container_twice_in_one_statement_is_accepted():
    # A `readonly[...]` parameter is the fact that the callee leaves that
    # container alone, so its element is not a step.
    entry = _entry(_NATIVE + _RECORD + _TAKER
                   + "def f(xs: list[P]) -> None:\n"
                   "    t = Taker()\n"
                   "    print(t.take_ro(xs, P(0)).v, t.take_ro(xs, P(0)).v)\n")
    fn = next(f for f in entry.ast.functions if f.name == "f")
    takes = [c for c in _method_calls(fn.body, []) if c.method == "take_ro"]
    assert [c.result_form for c in takes] == [ResultForm.REFERENCE_VALUE] * 2


def test_none_on_a_consuming_receiver_is_accepted_and_stamps_nothing():
    # A consuming method cannot borrow its receiver, and "none" says so.
    entry = _entry(
        _NATIVE + "from tpy import Own\nfrom typing import Self\n" + _NODE
        + "    @native(result={\"self\": \"none\", \"other\": \"borrow\"})\n"
        "    def take(self: Own[Self], other: Node) -> Node: ...\n"
        "\n\n"
        "def f(b: Node) -> int32:\n"
        "    return Node(1).take(b).v\n")
    fn = next(f for f in entry.ast.functions if f.name == "f")
    take, = [c for c in _method_calls(fn.body, []) if c.method == "take"]
    root = take.resolved_function_info.root
    assert root.borrow_declared
    assert root.return_borrows_from == frozenset({0})
    assert root.element_borrows_from == frozenset()


@pytest.mark.parametrize("relation, elements", [("element", {0}), ("borrow", set())])
def test_none_on_the_receiver_beside_a_declared_borrow_is_accepted(relation, elements):
    # The map declares a borrow, so "none" on self says the result holds
    # nothing of the receiver: only `other` is stamped.
    entry = _entry(
        _NATIVE + "from tpy import NativeIterable\nfrom typing import Iterator\n"
        "@native(\"::Bag\", elements=True)\n"
        "class Bag[T](NativeIterable[T]):\n"
        "    def __init__(self) -> None: ...\n"
        "    @native(\"tpy::__iter__\", function=True)\n"
        "    @readonly\n"
        "    def __iter__(self) -> Iterator[T]: ...\n"
        f"    @native(\"take\", result={{\"self\": \"none\", \"other\": \"{relation}\"}})\n"
        "    @readonly\n"
        "    def take(self, other: list[T]) -> T: ...\n"
        "\n\n"
        "def f(b: Bag[list[int32]], xs: list[list[int32]]) -> int32:\n"
        "    return len(b.take(xs))\n")
    fn = next(f for f in entry.ast.functions if f.name == "f")
    take, = [c for c in _method_calls(fn.body, []) if c.method == "take"]
    root = take.resolved_function_info.root
    assert root.borrow_declared
    assert root.return_borrows_from == frozenset({0})
    assert root.element_borrows_from == frozenset(elements)


def test_result_and_args_on_one_decorator_both_stamp():
    entry = _entry(
        _NATIVE + "from tpy import NativeIterable, Own\nfrom typing import Iterator\n"
        "@native(\"::Bag\", elements=True)\n"
        "class Bag[T](NativeIterable[T]):\n"
        "    def __init__(self) -> None: ...\n"
        "    @native(\"tpy::__iter__\", function=True)\n"
        "    @readonly\n"
        "    def __iter__(self) -> Iterator[T]: ...\n"
        "    @native(\"swap\", result={\"self\": \"element\", \"v\": \"none\"},\n"
        "            args={\"self\": \"writes\"})\n"
        "    def swap(self, v: Own[T]) -> T: ...\n"
        "\n\n"
        "def f(b: Bag[list[int32]]) -> int32:\n"
        "    return len(b.swap([1]))\n")
    fn = next(f for f in entry.ast.functions if f.name == "f")
    swap, = [c for c in _method_calls(fn.body, []) if c.method == "swap"]
    root = swap.resolved_function_info.root
    assert root.borrow_declared
    assert root.element_borrows_from == frozenset({-1})
    assert root.native_mutates
