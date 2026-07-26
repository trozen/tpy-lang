# Under polymorphic dispatch a nested match naming a DIFFERENT subclass binds
# the shared slot at a second type, so it is rejected like any other retype.
# This is the idiomatic shape of the reject; the resolution tracked in BUGS.md
# is to bind the slot at the common supertype instead.
from typing import Protocol
from tpy import dynamic


@dynamic
class Shape(Protocol):
    def area(self) -> int: ...


class Square:
    side: int
    def __init__(self, side: int) -> None:
        self.side = side
    def area(self) -> int:
        return self.side * self.side


class Circle:
    r: int
    def __init__(self, r: int) -> None:
        self.r = r
    def area(self) -> int:
        return 3 * self.r * self.r


def pick(a: Shape, b: Shape) -> int:
    match a:
        case Square() as p:
            match b:
                case Circle() as p:  # tpyc: error(/bound as 'Square'.*and 'Circle'/)
                    pass
            return p.area()
        case _:
            return -1


def main() -> None:
    print(pick(Square(2), Circle(3)))


main()
