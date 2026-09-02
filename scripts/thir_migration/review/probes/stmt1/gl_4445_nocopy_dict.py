from tpy import Int32, Own, readonly
class Point:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
from tplib import Box
d: dict[Int32, Box[Point]] = {1: Box(Point(1))}
print(len(d))
