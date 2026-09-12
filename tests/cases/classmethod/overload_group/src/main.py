# An @overload group of classmethods: each stub carries the receiver slot, and
# the trailing implementation constructs through `cls`.
from typing import Self, overload
from tpy import int32, Own


class Point:
    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y

    @overload
    @classmethod
    def make(cls, x: int32) -> Own[Self]: ...

    @overload
    @classmethod
    def make(cls, x: int32, y: int32) -> Own[Self]: ...

    @classmethod
    def make(cls, x: int32, y: int32 = 0) -> Own[Self]:
        return cls(x, y)


def main() -> None:
    a = Point.make(1)
    b = Point.make(1, 2)
    print(a.x, a.y, b.x, b.y)


main()
