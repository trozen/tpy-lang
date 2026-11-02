# A union of user protocols with None: the isinstance guard and the assert
# keep each concept term off the None default's `std::nullptr_t`.
from typing import Protocol, runtime_checkable
from tpy import int32


class HasA(Protocol):
    def a(self) -> int32: ...


@runtime_checkable
class HasB(Protocol):
    def b(self) -> int32: ...


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


def main() -> None:
    ob = OnlyB()
    print("user_guard", user_guard(), user_guard(ob))
    print("user_assert", user_assert(ob))
    try:
        r = user_assert()
        print("user_assert", r)
    except AssertionError:
        print("user_assert: raised")


main()
