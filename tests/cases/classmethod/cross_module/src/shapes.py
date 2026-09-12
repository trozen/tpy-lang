from typing import Self

from tpy import int32, Own


class Point:
    def __init__(self, x: int32):
        self.x = x

    @classmethod
    def origin(cls) -> Own[Self]:
        return cls(0)

    @classmethod
    def at(cls, x: int32) -> Own[Self]:
        return cls(x)
