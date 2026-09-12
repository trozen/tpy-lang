# @override on a method that correctly overrides a parent class method.
# Should produce a non-polymorphic warning (expected behavior).
from tpy import int32
from typing import override

class Shape:
    def area(self) -> int32:
        return int32(0)

    def describe(self) -> str:
        return "shape"


class Square(Shape):
    side: int32

    def __init__(self, side: int32) -> None:
        self.side = side

    @override
    def area(self) -> int32:  # tpyc: warning(/non-polymorphic/)
        return self.side * self.side

    @override
    def describe(self) -> str:  # tpyc: warning(/non-polymorphic/)
        return "square"


def main() -> None:
    s = Square(int32(4))
    print(s.area())
    print(s.describe())

main()
