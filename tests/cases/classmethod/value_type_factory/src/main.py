# A classmethod on a ValueType record: no Own needed on the return, the value
# is copied out like any other value type.
from typing import Self

from tpy import Int32, ValueType


class Vec2(ValueType):
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

    @classmethod
    def zero(cls) -> Self:
        return cls(0, 0)

    @classmethod
    def diagonal(cls, n: Int32) -> Self:
        return cls(n, n)


def main() -> None:
    z = Vec2.zero()
    d = Vec2.diagonal(4)
    print(z.x, z.y, d.x, d.y)


main()
