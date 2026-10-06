"""The element-storage facts a native stub declares: `@native(...,
elements=True)` on a class and `@native(..., mutates="elements")` on a
method -- where the parser accepts them, what registration reads off the
stub (`TypeDef.native_members`), the THIR stub callee fact, and sema's
iterator-invalidation check, for the builtin stubs and a user binding."""

from __future__ import annotations

from dataclasses import replace

import pytest

from ..compilation_context import activate_compiler
from ..diagnostics import DiagnosticLevel, SemanticError
from ..parse.nodes import ParseError
from ..type_def_registry import NativeMembers, _type_defs, get_type_def
from ..typesys import INT32, STR, NominalType, ReadonlyType
from . import nodes as th
from .lower import iter_module_callables, lower_function
from .scalar_leaves import binds_cursor, declared_members
from .test_method_stubs import _replace_node, nodes
from .testutil import _compile, _entry
from .validate import THIRValidationError, validate_function

HEADER = """\
from typing import Iterator
from tpy import int32, Own, NativeIterable, ValueType, readonly, pure, auto_readonly, dispatch
from tpy.extern import native, cpp_template
"""

RING = """
@native("my::Ring", elements=True)
class Ring[T](NativeIterable[T]):
    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[T]: ...
    @native("at")
    @pure
    @readonly
    def __getitem__(self, i: int32) -> T: ...
    @native("put", mutates="elements")
    def put(self, i: int32, v: Own[T]) -> None: ...
    @native("push")
    def push(self, v: Own[T]) -> None: ...
"""

TABLE = """
@native("my::Table", elements=True)
class Table[K, V](NativeIterable[K]):
    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[K]: ...
    @native("at")
    @pure
    @readonly
    def __getitem__(self, key: readonly[K]) -> V: ...
"""

VIEW = """
@native("my::View", borrowing_view=True)
class View[T](ValueType, NativeIterable[T]):
    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[T]: ...
"""

# A loop over a Ring whose body writes through `r.WRITE`.
LOOP = """
def loop(r: Ring[int32]) -> int32:
    t = 0
    for v in r:
        r.WRITE
        t += v
    return t
"""

WRITES = """
def writes(r: Ring[int32], v: int32) -> None:
    r.put(0, v)
    r.push(v)
"""

# A stub class whose only member is `__iter__`, with one more method spliced in.
ITERABLE = """
@native("my::Ring", elements=True)
class Ring[T](NativeIterable[T]):
    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[T]: ...
"""


def _nominal(qname: str, *args) -> NominalType:
    return NominalType(qname.rsplit(".", 1)[-1], tuple(args), _module_qname=qname)


# --- registry facts -----------------------------------------------------------

_CURSOR = NativeMembers(0, None, False, True)

BUILTIN_MEMBERS = {
    "builtins.list": (True, _CURSOR),
    "builtins.set": (True, _CURSOR),
    "builtins.dict": (True, NativeMembers(0, 1, True, True)),
    "tpy.Array": (True, _CURSOR),
    "tpy.Span": (False, _CURSOR),
    "tpy.varargs": (False, _CURSOR),
    "builtins.dict_keys": (False, _CURSOR),
    "builtins.dict_values": (False, NativeMembers(1, None, False, True)),
    "builtins.dict_items": (False, NativeMembers(0, 1, False, False)),
    # SpanIter's `__iter__` returns Self; the str and bytes views own a leaf's buffer.
    "tpy.SpanIter": (False, None),
    "tpy.StrView": (False, None),
    "tpy.BytesView": (False, None),
}


@pytest.fixture
def compiled_stubs():
    return _compile("pass\n")


@pytest.mark.parametrize("qname", BUILTIN_MEMBERS)
def test_a_builtin_stub_declares_its_storage_members(qname, compiled_stubs):
    td = _type_defs[qname]
    assert (td.owns_elements, td.native_members) == BUILTIN_MEMBERS[qname]


def test_exactly_the_listed_builtin_stubs_declare_members(compiled_stubs):
    declared = {q for q, td in _type_defs.items() if td.owns_elements or td.native_members is not None}
    assert declared == {q for q, (owns, members) in BUILTIN_MEMBERS.items() if owns or members is not None}


@pytest.mark.parametrize("stub, qname, owns, members", [
    # The element is the parameter the readonly `__iter__` yields; a subscript returning it is positional.
    (RING, "__main__.Ring", True, _CURSOR),
    # A subscript keyed by the element that returns another parameter declares the value member.
    (TABLE, "__main__.Table", True, NativeMembers(0, 1, True, True)),
    # A view declares the members it views and owns none.
    (VIEW, "__main__.View", False, _CURSOR),
], ids=["positional", "keyed", "view"])
def test_a_user_stub_declares_its_storage_members(stub, qname, owns, members):
    compiler, _ = _compile(HEADER + stub)
    with activate_compiler(compiler):
        td = get_type_def(qname)
        assert (td.owns_elements, td.native_members) == (owns, members)


def test_declared_members_read_the_instantiated_type_arguments():
    compiler, _ = _compile(HEADER + RING + TABLE)
    with activate_compiler(compiler):
        # Members are positions: a dict whose key and value share a type has two of them.
        dict_ss = _nominal("builtins.dict", STR, STR)
        assert declared_members(dict_ss) == (STR, STR, True) and binds_cursor(dict_ss)
        # An Array's length is no member.
        array = _nominal("tpy.Array", INT32, 3)
        assert declared_members(array) == (INT32, None, False) and binds_cursor(array)
        # The items view declares both members but binds no cursor: it yields them together.
        items = _nominal("builtins.dict_items", STR, INT32)
        assert declared_members(items) == (STR, INT32, False) and not binds_cursor(items)
        assert declared_members(_nominal("builtins.dict_values", STR, INT32)) == (INT32, None, False)
        # An access wrapper on the argument is kept.
        span = _nominal("tpy.Span", ReadonlyType(INT32))
        assert declared_members(span) == (ReadonlyType(INT32), None, False)
        # A user stub reads the same way.
        assert declared_members(_nominal("__main__.Ring", STR)) == (STR, None, False)
        assert declared_members(_nominal("__main__.Table", STR, INT32)) == (STR, INT32, True)
        # A type whose stub declares nothing, and a non-container, have no members.
        span_iter = _nominal("tpy.SpanIter", INT32)
        assert declared_members(span_iter) is None and not binds_cursor(span_iter)
        assert declared_members(STR) is None


# --- parse rejections ---------------------------------------------------------

_NOT_ON_CLASS = r"@native\(mutates=\.\.\.\) is only valid on a method stub"
_NOT_ON_METHOD = r"@native\(elements=\.\.\.\) is only valid on a class"
_DECLARES_NO_WRITE = r"@native\(mutates=\.\.\.\) on 'at': the method declares that it does not write its receiver"
_NO_RECEIVER = r"@native\(mutates=\.\.\.\) on 'make': a method without a receiver has no elements to write"


def _method(*decorators: str, signature: str = "def at(self, i: int32) -> T: ...") -> str:
    lines = "".join(f"    {d}\n" for d in decorators)
    return HEADER + ITERABLE + lines + f"    {signature}\n"


@pytest.mark.parametrize("source, message", [
    (HEADER + '@native("my::Ring", mutates="elements")\nclass Ring[T]:\n    pass\n', _NOT_ON_CLASS),
    (_method('@native("put", elements=True)', signature="def put(self, v: Own[T]) -> None: ..."),
     _NOT_ON_METHOD),
    (HEADER + '@native("put", elements=True)\ndef put(v: int32) -> None: ...\n', _NOT_ON_METHOD),
    (HEADER + '@native("put", mutates="elements")\ndef put(v: int32) -> None: ...\n',
     r"@native\(mutates=\.\.\.\) is only valid on a method stub: a free function has no receiver"),
    # "structure" is what an undeclared mutating method already means.
    (_method('@native("put", mutates="shape")', signature="def put(self, v: Own[T]) -> None: ..."),
     r'@native\(mutates=\.\.\.\) only supports mutates="elements"'),
    (_method('@native("put", mutates="structure")', signature="def put(self, v: Own[T]) -> None: ..."),
     r'@native\(mutates=\.\.\.\) only supports mutates="elements"'),
    # The conflict is checked after every decorator is read, so either order rejects.
    (_method('@native("at", mutates="elements")', "@readonly"), _DECLARES_NO_WRITE),
    (_method("@readonly", '@native("at", mutates="elements")'), _DECLARES_NO_WRITE),
    (_method('@native("at", mutates="elements")', "@pure"), _DECLARES_NO_WRITE),
    (_method('@native("at", mutates="elements")', "@auto_readonly"), _DECLARES_NO_WRITE),
    (_method("@staticmethod", '@native("make", mutates="elements")',
             signature="def make(n: int32) -> None: ..."), _NO_RECEIVER),
    (_method("@classmethod", '@native("make", mutates="elements")',
             signature="def make(cls, n: int32) -> None: ..."), _NO_RECEIVER),
    # A template binding's mutating method stays a structure write.
    (_method('@cpp_template("put({self}, {v})", mutates="elements")',
             signature="def put(self, v: Own[T]) -> None: ..."),
     r"@cpp_template\(\) got unexpected keyword argument 'mutates'"),
    # What a method does with its parts is a method's declaration.
    (HEADER + '@native("my::Ring", element_effect="insert")\nclass Ring[T]:\n    pass\n',
     r"@native\(element_effect=\.\.\.\) is only valid on a method stub"),
    (HEADER + '@native("put", element_effect="insert")\ndef put(v: int32) -> None: ...\n',
     r"@native\(element_effect=\.\.\.\) is only valid on a method stub: a free function"),
    (_method('@native("put", element_effect="store")', signature="def put(self, v: Own[T]) -> None: ..."),
     r'@native\(element_effect=\.\.\.\) is "insert" or "lookup"'),
], ids=["mutates-on-class", "elements-on-method", "elements-on-function", "mutates-on-function",
        "mutates-shape", "mutates-structure", "mutates-then-readonly", "readonly-then-mutates",
        "mutates-pure", "mutates-auto-readonly", "mutates-staticmethod", "mutates-classmethod",
        "cpp-template-mutates", "effect-on-class", "effect-on-function", "effect-unknown"])
def test_the_parser_rejects_a_misplaced_storage_declaration(source, message):
    with pytest.raises(ParseError, match=message):
        _compile(source)


# --- registration rejections --------------------------------------------------

_NO_ELEMENT = r"@native\(elements=True\) requires a @readonly __iter__ returning Iterator\[T\]"

_ITER_K = """
@native("my::Pair", elements=True)
class Pair[K, V](NativeIterable[K]):
    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[K]: ...
"""


@pytest.mark.parametrize("stub, message", [
    ('@native("my::Ring", elements=True)\nclass Ring[T]:\n'
     '    @native("push")\n    def push(self, v: Own[T]) -> None: ...\n', _NO_ELEMENT),
    # A mutating `__iter__` (a consuming one) names no element the value keeps.
    ('@native("my::Ring", elements=True)\nclass Ring[T](NativeIterable[T]):\n'
     '    @native("tpy::__iter__", function=True)\n    def __iter__(self) -> Iterator[T]: ...\n', _NO_ELEMENT),
    # The element must be one of the class's own type parameters.
    ('@native("my::Ring", elements=True)\nclass Ring[T](NativeIterable[int32]):\n'
     '    @native("tpy::__iter__", function=True)\n    @pure\n    @readonly\n'
     '    def __iter__(self) -> Iterator[int32]: ...\n', _NO_ELEMENT),
    ('@native("my::View", elements=True, borrowing_view=True)\nclass View[T](ValueType, NativeIterable[T]):\n'
     '    @native("tpy::__iter__", function=True)\n    @pure\n    @readonly\n'
     '    def __iter__(self) -> Iterator[T]: ...\n',
     r"@native\(elements=True\) cannot be combined with borrowing_view=True"),
    ('@native("my::Pair", elements=True)\nclass Pair[K, V](NativeIterable[K]):\n'
     '    @dispatch\n    @native("tpy::__iter__", function=True)\n    @pure\n    @readonly\n'
     '    def __iter__(self) -> Iterator[K]: ...\n'
     '    @dispatch\n    @native("tpy::__iter__", function=True)\n    @pure\n    @readonly\n'
     '    def __iter__(self, n: int32) -> Iterator[V]: ...\n',
     r"declares more than one element: its readonly __iter__ overloads yield different type parameters"),
    (_ITER_K + '    @native("at")\n    @pure\n    @readonly\n    def __getitem__(self, i: int32) -> V: ...\n',
     r"declares a subscript returning a member other than its element, which must be keyed by the element"),
    (_ITER_K + '    @native("at")\n    @pure\n    @readonly\n    def __getitem__(self, key: V) -> K: ...\n',
     r"declares a subscript keyed by a type parameter its __iter__ does not yield"),
    (_ITER_K + '    @dispatch\n    @native("at")\n    @pure\n    @readonly\n'
     '    def __getitem__(self, i: int32) -> K: ...\n'
     '    @dispatch\n    @native("at")\n    @pure\n    @readonly\n'
     '    def __getitem__(self, key: readonly[K]) -> V: ...\n',
     r"declares more than one subscript member: its __getitem__ overloads return different type parameters"),
    # Iterating both members at once leaves no element a subscript could address.
    ('@native("my::Pair", elements=True)\nclass Pair[K, V](NativeIterable[tuple[K, V]]):\n'
     '    @native("tpy::__iter__", function=True)\n    @pure\n    @readonly\n'
     '    def __iter__(self) -> Iterator[tuple[K, V]]: ...\n'
     '    @native("at")\n    @pure\n    @readonly\n    def __getitem__(self, i: int32) -> K: ...\n',
     r"iterates two members at once and also declares a subscript member"),
], ids=["no-iter", "mutable-iter", "iter-not-a-parameter", "elements-and-view", "two-elements",
        "positional-other-member", "keyed-by-non-element", "two-subscript-members", "tuple-and-subscript"])
def test_registration_rejects_a_stub_whose_members_are_undeclared_or_ambiguous(stub, message):
    with pytest.raises(SemanticError, match=message):
        _compile(HEADER + stub)


# --- THIR stub callee ---------------------------------------------------------

def _lowered(source: str, name: str):
    compiler, modules = _compile(source)
    entry = _entry(modules)
    func, self_type = next((f, st) for f, st in iter_module_callables(entry.ast, entry.analyzer)
                           if f.name == name)
    with activate_compiler(compiler):
        return compiler, lower_function(func, entry.analyzer, self_type=self_type)


def _calls(fn: th.THIRFunction) -> dict[str, th.THIRMethodCall]:
    return {c.stub_callee.identity.qualified_name: c for c in nodes(fn, th.THIRMethodCall)}


def test_a_user_method_stub_publishes_its_declared_element_write():
    _, fn = _lowered(HEADER + RING + WRITES, "writes")
    calls = _calls(fn)
    assert calls["__main__.Ring.put"].stub_callee.mutates_elements
    # An undeclared mutating method is a structure write.
    assert not calls["__main__.Ring.push"].stub_callee.mutates_elements


def test_the_validator_rejects_an_element_write_on_a_method_that_declares_no_write():
    compiler, fn = _lowered(HEADER + RING + WRITES, "writes")
    with activate_compiler(compiler):
        validate_function(fn)
        put = _calls(fn)["__main__.Ring.put"]
        stub = put.stub_callee
        for damaged in (replace(stub, readonly=(True, *stub.readonly[1:])),
                        replace(stub, contract=th.THIRStubContract.PURE)):
            with pytest.raises(THIRValidationError, match="invalid stub callee"):
                validate_function(_replace_node(fn, put, replace(put, stub_callee=damaged)))


# --- sema ---------------------------------------------------------------------

@pytest.mark.parametrize("write, warns", [("put(0, v)", False), ("push(v)", True)])
def test_iterator_invalidation_reads_a_user_stubs_declaration(write, warns):
    _, modules = _compile(HEADER + RING + LOOP.replace("WRITE", write))
    warnings = [d.message for d in _entry(modules).analyzer.diagnostics if d.level is DiagnosticLevel.WARNING]
    assert any("Mutation of 'r' while iterating" in w for w in warnings) is warns, warnings


# A container whose element is no type parameter cannot declare `elements=True`.
BAG = """
@native("my::Bag")
class Bag(NativeIterable[int32]):
    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[int32]: ...
    @native("put", mutates="elements")
    def put(self, i: int32, v: int32) -> None: ...
    @native("push")
    def push(self, v: int32) -> None: ...
"""


@pytest.mark.parametrize("write, warns", [("put(0, v)", False), ("push(v)", True)])
def test_an_element_write_is_trusted_on_a_class_that_declares_no_elements(write, warns):
    # The declaration is the stub author's audit of the method, whatever the class declares.
    compiler, modules = _compile(HEADER + BAG + LOOP.replace("Ring[int32]", "Bag").replace("WRITE", write))
    warnings = [d.message for d in _entry(modules).analyzer.diagnostics if d.level is DiagnosticLevel.WARNING]
    assert any("Mutation of 'r' while iterating" in w for w in warnings) is warns, warnings
    with activate_compiler(compiler):
        bag = get_type_def("__main__.Bag")
        assert not bag.owns_elements and bag.native_members is None
