# Module-level tuple unpack with Own[T] (non-value-type pointer global)
from tpy import Own, int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def make_pair() -> tuple[int32, Own[Point]]:
    return (int32(42), Point(int32(1), int32(2)))

n, p = make_pair()
print(n)
print(p.x)
print(p.y)

def read_point() -> None:
    print(p.x)
    print(p.y)

read_point()
