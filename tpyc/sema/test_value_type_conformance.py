"""ValueType conformance of composite types, asked by a ValueType record's
field validation and by every `T: ValueType` bound: a tuple, union,
Optional or generic recursive alias is a value type only when every part
is, since copying one copies the part it holds."""

from __future__ import annotations

import pytest

from ..diagnostics import SemanticError
from ..thir.testutil import _compile


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


@pytest.mark.parametrize(("field_type", "refused"), [
    ("T", False),
    ("tuple[T, int32]", False),
    ("tuple[T | None, int32]", False),
    ("T | None", True),
])
def test_a_field_naming_a_type_parameter_keeps_its_answer(
        field_type: str, refused: bool) -> None:
    # BUGS.md#generic-valuetype-field-param-unchecked
    error = _field_error(field_type, "[T]")
    if refused:
        assert error == (
            f"ValueType class 'V': field 't' has non-value type '{field_type}'")
    else:
        assert error is None


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
