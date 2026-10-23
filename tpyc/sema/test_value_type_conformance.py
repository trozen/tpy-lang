"""ValueType conformance of composite types, asked by a ValueType record's
field validation and by every `T: ValueType` bound: a tuple, union,
Optional or generic recursive alias is a value type only when every part
is, since copying one copies the part it holds."""

from __future__ import annotations

import pytest

from ..diagnostics import DiagnosticLevel, SemanticError
from ..thir.testutil import _compile, _entry


_PRELUDE = """\
from tpy import int32, ValueType, Ptr, Span, readonly
from typing import Callable
class A:
    def __init__(self, v: int32) -> None:
        self.v = v
class Res:
    def __init__(self, v: int32) -> None:
        self.v = v
    def __del__(self) -> None:
        pass
class P(ValueType):
    def __init__(self, n: int32) -> None:
        self.n = n
type Tree[T] = int32 | tuple[T, Ptr[Tree[T]]]
"""


def _error(source: str) -> str | None:
    try:
        _compile(_PRELUDE + source)
    except SemanticError as e:
        return str(e)
    return None


def _field_error(field_type: str, type_params: str = "") -> str | None:
    return _error(f"""\
class V{type_params}(ValueType):
    def __init__(self, t: {field_type}) -> None:
        self.t = t
""")


@pytest.mark.parametrize("field_type", [
    "tuple[A, int32]",
    "tuple[tuple[A, int32], int32]",
    "tuple[readonly[A], int32]",
    "tuple[int32, Res]",
    "tuple[A, int32] | None",
    "tuple[A, int32] | tuple[int32, str]",
    "int32 | tuple[A, int32]",
])
def test_a_field_holding_a_reference_through_a_part_is_refused(
        field_type: str) -> None:
    assert _field_error(field_type) == (
        f"ValueType class 'V': field 't' has type '{field_type}', "
        "which holds a reference type")


@pytest.mark.parametrize("field_type", ["A | None", "A", "list[int32]"])
def test_a_field_of_non_value_shape_keeps_the_plain_message(
        field_type: str) -> None:
    assert _field_error(field_type) == (
        f"ValueType class 'V': field 't' has non-value type '{field_type}'")


def test_a_recursive_alias_field_is_refused() -> None:
    # Refused whatever it holds while field validation runs before the
    # alias is finalized (BUGS.md#valuetype-field-recursive-alias-refused).
    assert _field_error("Tree[A]") is not None


@pytest.mark.parametrize("field_type", [
    "tuple[int32, str]",
    "tuple[int32, str] | None",
    "int32 | tuple[int32, str]",
    "str | None",
    "Ptr[A]",
    "tuple[Ptr[A], int32]",
    "Span[int32]",
    "Callable[[A], int32]",
    "P",
    "tuple[P, int32] | None",
])
def test_a_field_of_value_parts_is_accepted(field_type: str) -> None:
    assert _field_error(field_type) is None


@pytest.mark.parametrize(("field_type", "message"), [
    ("T", "has non-value type 'T'"),
    ("T | None", "has non-value type 'T | None'"),
    ("tuple[T, int32]", "has type 'tuple[T, int32]', which holds a reference type"),
    ("tuple[T | None, int32]",
     "has type 'tuple[T | None, int32]', which holds a reference type"),
])
def test_an_unbounded_type_parameter_field_is_refused(
        field_type: str, message: str) -> None:
    assert _field_error(field_type, "[T]") == (
        f"ValueType class 'V': field 't' {message}")


@pytest.mark.parametrize("field_type", [
    "Ptr[T]",
    "Span[T]",
    "Callable[[T], int32]",
])
def test_an_unbounded_type_parameter_behind_a_view_is_accepted(
        field_type: str) -> None:
    assert _field_error(field_type, "[T]") is None


@pytest.mark.parametrize("field_type", [
    "T",
    "T | None",
    "tuple[T, int32]",
    "tuple[T | None, int32]",
])
def test_a_value_type_bounded_type_parameter_field_is_accepted(
        field_type: str) -> None:
    assert _field_error(field_type, "[T: ValueType]") is None


@pytest.mark.parametrize("field_type", ["tuple[T, A]", "tuple[T, A] | None"])
def test_a_bounded_type_parameter_beside_a_reference_part_is_refused(
        field_type: str) -> None:
    assert _field_error(field_type, "[T: ValueType]") == (
        f"ValueType class 'V': field 't' has type '{field_type}', "
        "which holds a reference type")


def test_a_bounded_class_body_optional_annotation_is_accepted() -> None:
    assert _error("""\
class V[T: ValueType](ValueType):
    x: T | None
    def __init__(self, x: T | None) -> None:
        self.x = x
""") is None


_BOUNDED_V = """\
class V[T: ValueType](ValueType):
    def __init__(self, x: T) -> None:
        self.x = x
"""


@pytest.mark.parametrize("construct", ["V[int32](1)", "V(2)", "V(P(3))"])
def test_a_bounded_record_takes_a_value_argument(construct: str) -> None:
    # The field store is a value copy the bound vouches for: no copy warning.
    _compiler, modules = _compile(
        _PRELUDE + _BOUNDED_V
        + f"def use() -> int32:\n    v = {construct}\n    return 1\n")
    assert [d.message for d in _entry(modules).analyzer.diagnostics
            if d.level is DiagnosticLevel.WARNING] == []


@pytest.mark.parametrize("construct", ["V[A](A(1))", "V(A(1))"])
def test_a_bounded_record_refuses_a_reference_argument(construct: str) -> None:
    error = _error(_BOUNDED_V + f"def use() -> int32:\n    v = {construct}\n"
                   "    return 1\n")
    assert error is not None and (
        "does not satisfy bound 'ValueType' for type parameter 'T' of 'V'"
        in error), error


def test_a_bounded_record_holding_a_bounded_generic_record_is_accepted() -> None:
    # Inner[T] conforms because Outer's T carries the ValueType bound.
    assert _error("""\
class Inner[T: ValueType](ValueType):
    def __init__(self, x: T) -> None:
        self.x = x

class Outer[T: ValueType](ValueType):
    def __init__(self, i: Inner[T]) -> None:
        self.i = i

def use() -> int32:
    o = Outer(Inner(1))
    w = o
    o = Outer(Inner(2))
    return w.i.x
""") is None


def test_a_generic_record_holding_no_type_parameter_is_accepted() -> None:
    assert _error("""\
class Cell[T](ValueType):
    def __init__(self, n: int32) -> None:
        self.n = n
""") is None


_BOUND_USES = {
    "generic function call": (
        "def keep[T: ValueType](x: T) -> int32:\n    return 1\n",
        "return keep(x)",
        "Cannot infer type arguments for 'keep'"),
    "class bound": (
        "class Box[T: ValueType]:\n"
        "    def __init__(self, x: T) -> None:\n        self.x = x\n",
        "b = Box(x)\n    return 1",
        "does not satisfy bound 'ValueType' for type parameter 'T' of 'Box'"),
    "method bound": (
        "class C:\n"
        "    def m[T: ValueType](self, x: T) -> int32:\n        return 1\n",
        "return C().m(x)",
        "Cannot infer type arguments for method 'm'"),
    "generic function reference": (
        "def keep[T: ValueType](x: T) -> int32:\n    return 1\n",
        "f: Callable[[SHAPE], int32] = keep\n    return f(x)",
        "Cannot use 'keep' as function reference"),
    "ValueType parameter": (
        "def g(x: ValueType) -> int32:\n    return 1\n",
        "return g(x)",
        "does not conform to protocol ValueType in argument 'x'"),
    "max with key": (
        "def k(t: SHAPE) -> int32:\n    return 1\n",
        "xs = [x]\n    r = max(xs, key=k)\n    return 1",
        "does not satisfy 'ValueType' required by 'max'"),
    "next with default": (
        "",
        "xs = [x]\n    r = next(iter(xs), x)\n    return 1",
        "does not satisfy 'ValueType' required by 'next'"),
}


def _bound_error(use: str, shape: str) -> str | None:
    decl, body, _ = _BOUND_USES[use]
    return _error((decl + f"def use(x: SHAPE) -> int32:\n    {body}\n")
                  .replace("SHAPE", shape))


@pytest.mark.parametrize("use", sorted(_BOUND_USES))
@pytest.mark.parametrize("shape", [
    "tuple[A, int32]",
    "tuple[A, int32] | None",
    "tuple[A, int32] | tuple[int32, str]",
    "int32 | tuple[A, int32]",
    "Tree[A]",
])
def test_the_bound_refuses_a_reference_held_through_a_part(
        use: str, shape: str) -> None:
    error = _bound_error(use, shape)
    assert error is not None and _BOUND_USES[use][2] in error, error


@pytest.mark.parametrize("use", sorted(_BOUND_USES))
@pytest.mark.parametrize("shape", [
    "tuple[int32, str] | None",
    "int32 | tuple[int32, str]",
    "Tree[int32]",
])
def test_the_bound_accepts_value_parts(use: str, shape: str) -> None:
    assert _bound_error(use, shape) is None


def test_min_through_comparable_value_names_the_held_reference() -> None:
    # Refused by Comparable either way; the message names the reference
    # the call would copy, as it does for the plain tuple.
    error = _error("def use(x: int32 | tuple[A, int32]) -> int32:\n"
                   "    xs = [x]\n    r = min(xs)\n    return 1\n")
    assert error is not None and (
        "'int32 | tuple[A, int32]' holds a reference type" in error), error
