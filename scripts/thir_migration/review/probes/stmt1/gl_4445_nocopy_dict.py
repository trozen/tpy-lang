from tpy import int32, Own, readonly
class Point:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
from tplib import Box
d: dict[int32, Box[Point]] = {1: Box(Point(1))}
print(len(d))
