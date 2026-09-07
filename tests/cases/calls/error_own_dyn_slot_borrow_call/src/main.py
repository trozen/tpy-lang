# An `Own[@dynamic P]` parameter is an owning slot whose payload is
# non-copyable (the adapter erases the concrete type), so a borrow-returning
# call there is the located error the copy warning upgrades to -- the same
# message the FIELD spelling of the slot gets (`use(f.cached)`).
from typing import Protocol

from tpy import Int32, Own, dynamic


@dynamic
class Shape(Protocol):
    def area(self) -> Int32: ...


class Square:
    side: Int32

    def __init__(self, side: Int32) -> None:
        self.side = side

    def area(self) -> Int32:
        return self.side * self.side


class Factory:
    cached: Square

    def __init__(self) -> None:
        self.cached = Square(3)

    def borrow(self) -> Square:
        return self.cached


def use(s: Own[Shape]) -> Int32:
    return s.area()


def main() -> None:
    f = Factory()
    print(use(f.borrow()))  # tpyc: error(/cannot copy Square into owned storage of type 'Shape'/)


main()
