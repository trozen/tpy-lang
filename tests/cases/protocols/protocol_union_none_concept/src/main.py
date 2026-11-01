# A union of user protocols with None: the isinstance guard and the assert
# keep each concept term off the None default's `std::nullptr_t`.
from typing import Protocol
from tpy import int32


class HasA(Protocol):
    def a(self) -> int32: ...


class HasB(Protocol):
    def b(self) -> int32: ...


# C++ `std::nullptr_t` satisfies this concept (it has `==`), yet None
# conforms to no protocol (BUGS.md#none-never-conforms-to-protocol)
class Eq(Protocol):
    def __eq__(self, other: "Eq") -> bool: ...


class Tag:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __eq__(self, other: "Tag") -> bool:
        return self.n == other.n


class OnlyB:
    def b(self) -> int32:
        return 9


# free function: guard, then the rest
def user_guard(x: HasA | HasB | None = None) -> int32:
    if not isinstance(x, HasB):  # tpyc: ok
        return -1
    return x.b()


# free function: assert, then the rest
def user_assert(x: HasA | HasB | None = None) -> int32:
    assert isinstance(x, HasB), "need b"  # tpyc: ok
    return x.b()


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
    ob = OnlyB()
    print("user_guard", user_guard(), user_guard(ob))
    print("user_assert", user_assert(ob))
    try:
        r = user_assert()
        print("user_assert", r)
    except AssertionError:
        print("user_assert: raised")
    t = Tag(1)
    print("eq_guard", eq_guard(), eq_guard(t))
    try:
        r = eq_assert()
        print("eq_assert", r)
    except AssertionError:
        print("eq_assert: raised")


main()
