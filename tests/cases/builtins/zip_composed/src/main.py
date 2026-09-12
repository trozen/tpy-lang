# zip() composed with map() -- owning_zip_iter must store iterators by value
# (not reference) to avoid dangling references from __iter__() returning Self&.
from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y
    def __str__(self) -> str:
        return f"({self.x}, {self.y})"

def identity(p: Point) -> Point:
    return p

def double(v: int32) -> int32:
    return v * 2

def main() -> None:
    pts1: list[Point] = [Point(1, 2), Point(3, 4)]
    pts2: list[Point] = [Point(5, 6), Point(7, 8)]
    vals: list[int32] = [10, 20]

    # Non-value types through map
    for a, b in zip(map(identity, pts1), map(identity, pts2)):
        print(a, b)

    # Mixed: non-value and value types
    for p, v in zip(map(identity, pts1), map(double, vals)):
        print(p, v)

    # Mutation through composed references proves no copy
    for a, b in zip(map(identity, pts1), map(identity, pts2)):
        a.x += 100
        b.y += 200
    for pt in pts1:
        print(pt)
    for pt in pts2:
        print(pt)

main()
