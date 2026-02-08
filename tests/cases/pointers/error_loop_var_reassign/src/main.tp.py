from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

def bad_loop_reassign(points: list[Point]) -> None:
    other: Point = Point(99, 99)
    for p in points:
        p = other  # tpyc: error(/Cannot reassign loop variable/)

points: list[Point] = [Point(1, 2)]
