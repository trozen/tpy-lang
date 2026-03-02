# Module-level tuple unpack with Own[T] (non-value-type pointer global)
from tpy import Own, Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def make_pair() -> tuple[Int32, Own[Point]]:
    return (Int32(42), Point(Int32(1), Int32(2)))

n, p = make_pair()
print(n)
print(p.x)
print(p.y)

def read_point() -> None:
    print(p.x)
    print(p.y)

read_point()
