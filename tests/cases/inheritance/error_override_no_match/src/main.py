# @override on a method that doesn't exist in the parent -- catches typos.
from tpy import int32
from typing import override

class Shape:
    def area(self) -> int32:
        return int32(0)


class Square(Shape):
    side: int32

    def __init__(self, side: int32) -> None:
        self.side = side

    @override
    def erea(self) -> int32:  # tpyc: error(/does not override/)
        return self.side * self.side
