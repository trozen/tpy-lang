# isinstance() on Optional[Protocol] -- narrows away None at compile time
from typing import Protocol, Sized, runtime_checkable
from tpy import int32


@runtime_checkable
class HasN(Protocol):
    def n(self) -> int32: ...


class A:
    v: int32

    def __init__(self) -> None:
        self.v = 4

    def n(self) -> int32:
        return self.v


def count_if_sized(items: Sized | None = None) -> int32:
    if isinstance(items, Sized):
        return len(items)
    return -1


def check_not(items: Sized | None = None) -> int32:
    if not isinstance(items, Sized):
        return -1
    return len(items)


# a method call after the early return (a decorated user protocol)
def method_tail(x: HasN | None = None) -> int32:
    if not isinstance(x, HasN):  # tpyc: ok
        return -1
    return x.n()


def main() -> None:
    # Sized | None
    nums: list[int32] = [1, 2, 3, 4, 5]
    print(count_if_sized(nums))
    print(count_if_sized())

    # not isinstance
    print(check_not(nums))
    print(check_not())

    # method call after the early return
    a = A()
    print("method_tail", method_tail(a), method_tail())


main()
