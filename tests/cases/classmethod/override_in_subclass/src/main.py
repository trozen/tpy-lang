# Reaching an INHERITED classmethod through a subclass is rejected (`cls` binds
# statically to the defining class); overriding it on the subclass is the
# supported way to get a per-class factory. See error_inherited_via_subclass.
from typing import Self

from tpy import Int32, Own


class Shape:
    def __init__(self, sides: Int32):
        self.sides = sides

    @classmethod
    def make(cls) -> Own[Self]:
        return cls(0)


class Square(Shape):  # tpyc: warning(/hides 'Shape.make'/)
    @classmethod
    def make(cls) -> Own[Self]:
        return cls(4)


def main() -> None:
    a = Shape.make()
    b = Square.make()
    print(a.sides, b.sides)


main()
