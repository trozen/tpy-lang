from typing import Self

from tpy import Int32, Own


class Point:
    def __init__(self, x: Int32):
        self.x = x

    @classmethod
    def origin(cls) -> Own[Self]:
        return cls(0)

    @classmethod
    def at(cls, x: Int32) -> Own[Self]:
        return cls(x)
