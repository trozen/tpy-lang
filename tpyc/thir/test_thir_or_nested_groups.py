"""A parenthesized or-pattern group is flattened at parse time, so a nested
spelling reaches codegen as the flat alternative list and must render
identically to it -- and still route through THIR.
"""

from .testutil import _assert_byte_identical, _assert_routes_byte_identical

_TYPES = (
    "from tpy import Int32\n"
    "class Dog:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
    "class Cat:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
    "class Bird:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
)

_BODY = (
    "def known(a: Dog | Cat | Bird) -> Int32:\n"
    "    match a:\n"
    "        case {plain}:\n            return 1\n"
    "    return 0\n"
    "def value(a: Dog | Cat | Bird) -> Int32:\n"
    "    match a:\n"
    "        case {bound}:\n            return v\n"
    "    return 0\n"
    "def main() -> None:\n"
    "    d: Dog | Cat | Bird = Dog(7)\n"
    "    print(known(d))\n"
    "    print(value(d))\n"
    "main()\n"
)

NESTED = _TYPES + _BODY.format(
    plain="(Dog() | Cat()) | Bird()",
    bound="(Dog(n=v) | Cat(n=v)) | Bird(n=v)")
FLAT = _TYPES + _BODY.format(
    plain="Dog() | Cat() | Bird()",
    bound="Dog(n=v) | Cat(n=v) | Bird(n=v)")


class TestNestedOrGroups:
    def test_nested_group_routes_byte_identical(self):
        _hpp, cpp = _assert_routes_byte_identical(NESTED, comments=False)
        # Both union-switch or-legs are exercised: the no-binding leg stacks
        # labels on one block, the binding leg emits a block per alternative.
        assert "case 2:\n    case 1:\n    case 0:\n" in cpp
        assert "__case_0_0" in cpp

    def test_nested_group_renders_as_flat_spelling(self):
        _nested_hpp, nested_cpp = _assert_byte_identical(NESTED, comments=False)
        _flat_hpp, flat_cpp = _assert_byte_identical(FLAT, comments=False)
        assert nested_cpp == flat_cpp
