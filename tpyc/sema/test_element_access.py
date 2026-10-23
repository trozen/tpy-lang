"""`@native` / `@cpp_template(element_access=...)`: where the parser accepts
the keyword, what registration validates (the dunder, the binding, the named
twin), and that the FunctionInfo field survives generic substitution and the
`@auto_readonly` clone -- for a user binding and the builtin stubs."""

from __future__ import annotations

import pytest

from ..diagnostics import SemanticError
from ..parse.nodes import ParseError
from ..thir.testutil import _compile, _entry
from ..typesys import INT32

HEADER = """\
from tpy import int32, Own, readonly, pure, auto_readonly, dispatch
from tpy.extern import native, cpp_template
"""

RING = """
@native("my::Ring")
class Ring[T]:
    @cpp_template("{self}[static_cast<std::size_t>({0})]")
    @pure
    @readonly
    def unchecked_get(self, index: int32) -> T: ...

    @cpp_template("{self}[static_cast<std::size_t>({0})] = {1}")
    def unchecked_set(self, index: int32, value: Own[T]) -> None: ...

    @native("at", element_access=unchecked_get)
    @pure
    @auto_readonly
    def __getitem__(self, index: int32) -> auto_readonly[T]: ...

    @cpp_template("put({self}, {0}, {1})", element_access=unchecked_set)
    def __setitem__(self, index: int32, value: Own[T]) -> None: ...

    @native("erase", element_access=True)
    def __delitem__(self, index: int32) -> None: ...

    @native("size")
    @pure
    @readonly
    def __len__(self) -> int32: ...
"""


def _ring_methods(name: str):
    compiler, modules = _compile(HEADER + RING)
    analyzer = _entry(modules).analyzer
    return analyzer, analyzer.registry.get_record("Ring").get_method_overloads(name)


# --- the field -----------------------------------------------------------------

def test_each_variant_carries_its_declared_contract():
    _, getters = _ring_methods("__getitem__")
    _, setters = _ring_methods("__setitem__")
    _, deleters = _ring_methods("__delitem__")
    _, lens = _ring_methods("__len__")
    assert [fi.element_access for fi in setters] == ["unchecked_set"]
    assert [fi.element_access for fi in deleters] == [True]
    assert [fi.element_access for fi in lens] == [None]
    # The @auto_readonly clone pair: both halves are the same element access.
    assert sorted(fi.is_readonly for fi in getters) == [False, True]
    assert {fi.element_access for fi in getters} == {"unchecked_get"}


def test_a_substituted_variant_keeps_its_contract():
    analyzer, getters = _ring_methods("__getitem__")
    for fi in getters:
        substituted = analyzer.type_ops.substitute_method_type_params(fi, {"T": INT32})
        assert substituted is not fi
        assert substituted.element_access == "unchecked_get"


@pytest.mark.parametrize("record, name, expected", [
    ("list", "__getitem__", "unchecked_get"),
    ("list", "__setitem__", "unchecked_set"),
    ("list", "__delitem__", True),
    ("dict", "__getitem__", True),
    ("dict", "__setitem__", True),
    ("dict", "__delitem__", True),
    ("str", "__getitem__", "unchecked_get"),
    ("bytearray", "__setitem__", "unchecked_set"),
])
def test_the_builtin_int_variant_declares_its_contract(record, name, expected):
    """Only the int-keyed (dict: key) variant carries it; slices do not."""
    _, modules = _compile("pass\n")
    info = _entry(modules).analyzer.registry.find_record(record)
    assert info is not None
    overloads = info.get_method_overloads(name)
    sliced = ["slice" in str(fi.params[0].type) for fi in overloads]
    assert {fi.element_access for fi, s in zip(overloads, sliced) if not s} == {expected}
    assert all(fi.element_access is None for fi, s in zip(overloads, sliced) if s)


# --- the parser ----------------------------------------------------------------

_TWIN_GET_SRC = ('    @cpp_template("{self}[static_cast<std::size_t>({0})]")\n'
                 '    @readonly\n'
                 '    def unchecked_get(self, index: int32) -> T: ...\n')

@pytest.mark.parametrize("source, message", [
    (HEADER + '@native("my::Ring", element_access=True)\nclass Ring[T]:\n    pass\n',
     r"@native\(element_access=\.\.\.\) is only valid on a method stub"),
    (HEADER + '@native("get", element_access=True)\ndef get(i: int32) -> int32: ...\n',
     r"@native\(element_access=\.\.\.\) is only valid on a method stub: a free function"),
    (HEADER + '@cpp_template("get({0})", element_access=True)\ndef get(i: int32) -> int32: ...\n',
     r"@cpp_template\(element_access=\.\.\.\) is only valid on a method stub: a free function"),
    # The twin is named by reference, never by a string.
    (HEADER + '@native("my::Ring")\nclass Ring[T]:\n' + _TWIN_GET_SRC
     + "    @native('at', element_access='unchecked_get')\n"
       '    def __getitem__(self, i: int32) -> T: ...\n',
     r"@native\(element_access=\.\.\.\) expects method name or bool"),
    (HEADER + '@native("my::Ring")\nclass Ring[T]:\n'
     '    @native("at", element_access=1)\n    def __getitem__(self, i: int32) -> T: ...\n',
     r"@native\(element_access=\.\.\.\) expects method name or bool"),
    # A value the parser cannot read is rejected, never taken as omitted.
    (HEADER + '@native("my::Ring")\nclass Ring[T]:\n' + _TWIN_GET_SRC
     + '    @native("at", element_access=Ring.unchecked_get)\n'
       '    def __getitem__(self, i: int32) -> T: ...\n',
     r"@native\(element_access=\.\.\.\) expects method name or bool"),
    (HEADER + '@native("my::Ring")\nclass Ring[T]:\n'
     '    @native("at", element_access=lambda s, i: 0)\n'
     '    def __getitem__(self, i: int32) -> T: ...\n',
     r"@native\(element_access=\.\.\.\) expects method name or bool"),
    # Below the variant the name is not bound yet when the decorator runs.
    (HEADER + '@native("my::Ring")\nclass Ring[T]:\n'
     '    @native("at", element_access=unchecked_get)\n'
     '    def __getitem__(self, i: int32) -> T: ...\n' + _TWIN_GET_SRC,
     r"element_access names 'unchecked_get', which is not defined above "
     r"'__getitem__' in class 'Ring'"),
    # A module-level function of that spelling is not the class's twin.
    (HEADER + 'def unchecked_get(index: int32) -> int32:\n    return index\n\n'
     '@native("my::Ring")\nclass Ring[T]:\n'
     '    @native("at", element_access=unchecked_get)\n'
     '    def __getitem__(self, i: int32) -> T: ...\n',
     r"element_access names 'unchecked_get', which is not defined above "
     r"'__getitem__' in class 'Ring'"),
], ids=["on-class", "native-function", "template-function", "string-name",
        "not-bool-or-name", "attribute", "lambda", "twin-below", "module-level-name"])
def test_the_parser_rejects_a_misplaced_element_access(source, message):
    with pytest.raises(ParseError, match=message):
        _compile(source)


def test_true_and_a_twin_above_parse():
    _, modules = _compile(HEADER + '@native("my::Ring")\nclass Ring[T]:\n' + _TWIN_GET_SRC
                          + '    @native("at", element_access=unchecked_get)\n'
                            '    @readonly\n'
                            '    def __getitem__(self, i: int32) -> T: ...\n'
                            '    @native("erase", element_access=True)\n'
                            '    def __delitem__(self, i: int32) -> None: ...\n'
                            '    @native("size")\n    @readonly\n'
                            '    def __len__(self) -> int32: ...\n')
    info = _entry(modules).analyzer.registry.get_record("Ring")
    assert {fi.element_access for fi in info.get_method_overloads("__getitem__")} == {"unchecked_get"}
    assert {fi.element_access for fi in info.get_method_overloads("__delitem__")} == {True}


def test_false_is_the_absent_contract():
    _, modules = _compile(HEADER + '@native("my::Ring")\nclass Ring[T]:\n'
                          '    @native("at", element_access=False)\n'
                          '    def __getitem__(self, i: int32) -> T: ...\n')
    info = _entry(modules).analyzer.registry.get_record("Ring")
    assert {fi.element_access for fi in info.get_method_overloads("__getitem__")} == {None}


# --- registration --------------------------------------------------------------

def _ring(*members: str) -> str:
    return HEADER + '@native("my::Ring")\nclass Ring[T]:\n' + "".join(
        "\n".join("    " + line for line in m.strip("\n").split("\n")) + "\n" for m in members)


_TWIN_GET = '''
@cpp_template("{self}[static_cast<std::size_t>({0})]")
@readonly
def unchecked_get(self, index: int32) -> T: ...
'''


@pytest.mark.parametrize("source, message", [
    (_ring('@native("at", element_access=True)\n@readonly\n'
           'def at(self, i: int32) -> T: ...'),
     r"element_access= on 'Ring.at': only a __getitem__, __setitem__ or "
     r"__delitem__ variant performs an element access"),
    (_ring('@native("raw")\n@readonly\ndef unchecked_get(self, index: int32) -> T: ...',
           '@native("at", element_access=unchecked_get)\n@readonly\n'
           'def __getitem__(self, i: int32) -> T: ...'),
     r"names 'Ring.unchecked_get', which is not bound by @cpp_template"),
    (_ring(_TWIN_GET,
           '@native("at", element_access=unchecked_get)\n@readonly\n'
           'def __getitem__(self, i: Own[T]) -> T: ...'),
     r"names 'Ring.unchecked_get', whose parameters do not match the variant's \(Own\[T\]\)"),
    (_ring('@cpp_template("{self}[static_cast<std::size_t>({0})]")\n@readonly\n'
           'def unchecked_get(self, index: int32) -> int32: ...',
           '@native("at", element_access=unchecked_get)\n@readonly\n'
           'def __getitem__(self, i: int32) -> T: ...'),
     r"names 'Ring.unchecked_get', whose result does not match the variant's 'T'"),
], ids=["not-a-subscript", "twin-not-template",
        "twin-params", "twin-result"])
def test_registration_rejects_an_invalid_element_access(source, message):
    with pytest.raises(SemanticError, match=message):
        _compile(source)



@pytest.mark.parametrize("source, error, message", [
    (_ring('@native("at", element_access=True)\n'
           'def __getitem__(self, i: int32) -> T:\n    return self.at(i)'),
     SemanticError, r"Methods on @native classes must have '\.\.\.' body"),
    (HEADER + 'class Ring:\n'
     '    @cpp_template("at({self}, {0})", element_access=True)\n'
     '    def __getitem__(self, i: int32) -> int32:\n        return i\n',
     ParseError, r"@cpp_template method '__getitem__' must have `\.\.\.` body"),
], ids=["native", "cpp-template"])
def test_a_method_with_a_body_cannot_declare_it(source, error, message):
    """Only a binding declares the contract: the keyword rides on @native /
    @cpp_template, and neither admits a TPy body."""
    with pytest.raises(error, match=message):
        _compile(source)


_CELL = '@native("my::Cell")\nclass Cell:\n    n: int32\n\n'


@pytest.mark.parametrize("source, why", [
    (HEADER + _CELL + _ring(
        '@cpp_template("{self}.raw_mut({0})")\n'
        'def unchecked_get(self, index: int32) -> Cell: ...',
        '@native("at", element_access=unchecked_get)\n'
        'def __getitem__(self, i: int32) -> Cell: ...').removeprefix(HEADER),
     "__getitem__ is implicitly readonly"),
    (_ring('@cpp_template("{self}.raw_mut({0})")\n'
           'def unchecked_get(self, index: int32) -> int32: ...',
           '@native("at", element_access=unchecked_get)\n@pure\n'
           'def __getitem__(self, i: int32) -> int32: ...'),
     "__getitem__ is readonly as @pure"),
    ("from dataclasses import dataclass\n" + HEADER
     + '@dataclass(frozen=True)\n@native("my::Ring")\nclass Ring:\n'
       '    n: int32\n\n'
       '    @cpp_template("{self}.raw_mut({0})")\n    @readonly(False)\n'
       '    def unchecked_get(self, index: int32) -> int32: ...\n\n'
       '    @cpp_template("{self}.at({0})", element_access=unchecked_get)\n'
       '    def __getitem__(self, i: int32) -> int32: ...\n',
     "__getitem__ is readonly in a frozen class"),
], ids=["implicit-pair", "pure", "frozen"])
def test_a_readonly_twin_rejection_names_why_the_variant_is_readonly(source, why):
    """The variant's readonly-ness is one the user did not spell: the
    diagnostic says where it comes from (`ImplicitReadonly`)."""
    with pytest.raises(SemanticError,
                       match=r"which does not accept a readonly receiver: "
                             + why + r" \(declare the twin @readonly\)"):
        _compile(source)
