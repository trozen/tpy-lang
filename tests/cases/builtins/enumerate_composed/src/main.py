# enumerate() composed with map() -- val_or_ref elements in tuples must be
# unwrapped correctly when destructuring the (index, element) pair.
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
    pts: list[Point] = [Point(1, 2), Point(3, 4)]
    vals: list[int32] = [10, 20]

    # Non-value type through map: val_or_ref<Point> in tuple
    for i, p in enumerate(map(identity, pts)):
        print(i, p)

    # Value type through map: no val_or_ref wrapping
    for j, v in enumerate(map(double, vals)):
        print(j, v)

    # Mutation through composed reference proves no copy
    for k, q in enumerate(map(identity, pts)):
        q.x += 100
    for pt in pts:
        print(pt)

main()
