# @override on a method that doesn't exist in the parent -- catches typos.
from tpy import Int32
from typing import override

class Shape:
    def area(self) -> Int32:
        return Int32(0)


class Square(Shape):
    side: Int32

    def __init__(self, side: Int32) -> None:
        self.side = side

    @override
    def erea(self) -> Int32:  # tpyc: error(/does not override/)
        return self.side * self.side
