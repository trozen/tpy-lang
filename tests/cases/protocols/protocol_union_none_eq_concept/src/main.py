# An `__eq__` protocol in a union with None: the isinstance guard and the
# assert keep the concept term off the None default's `std::nullptr_t`.
# Eq's `__eq__` is one `object` provides, so CPython's isinstance answers
# True for every class (BUGS.md#isinstance-protocol-signatures-vs-names).
from typing import Protocol, runtime_checkable
from tpy import int32


class HasA(Protocol):
    def a(self) -> int32: ...


# C++ `std::nullptr_t` satisfies this concept (it has `==`), yet None
# conforms to no protocol (BUGS.md#isinstance-protocol-signatures-vs-names row 5)
@runtime_checkable
class Eq(Protocol):
    def __eq__(self, other: "Eq") -> bool: ...


class Tag:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __eq__(self, other: "Tag") -> bool:
        return self.n == other.n


# Eq on a nullable union: the None default takes the guard
def eq_guard(x: Eq | HasA | None = None) -> int32:
    if not isinstance(x, Eq):  # tpyc: ok
        return -1
    return 1


# Eq on a nullable union: the None default fails the assert
def eq_assert(x: Eq | HasA | None = None) -> int32:
    assert isinstance(x, Eq), "need eq"  # tpyc: ok
    return 1


def main() -> None:
    t = Tag(1)
    print("eq_guard", eq_guard(), eq_guard(t))
    try:
        r = eq_assert()
        print("eq_assert", r)
    except AssertionError:
        print("eq_assert: raised")


main()
