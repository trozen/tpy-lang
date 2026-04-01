# Warn when container constructors materialize borrowed references from iterators.
# The iterator is an rvalue, but its elements are Ref[T] -- copying into owned storage.
from tpy import Int32, Own, copy, copy_iter

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def identity(p: Point) -> Point:
    return p

def clone_point(p: Point) -> Own[Point]:
    return copy(p)

def double(x: Int32) -> Int32:
    return x * 2

def main() -> None:
    pts: list[Point] = [Point(1, 2), Point(3, 4)]
    vals: list[Int32] = [Int32(1), Int32(2)]

    # map returning Ref[Point] -> list copies on materialization
    a = list(map(identity, pts))  # tpyc: warning(/copies Point elements/)
    print(len(a))

    # value type elements -- no warning
    b = list(map(double, vals))  # tpyc: ok
    print(len(b))

    # Own[Point] return -- caller acknowledged ownership, no warning
    c = list(map(clone_point, pts))  # tpyc: ok
    print(len(c))

    # copy_iter() acknowledges the copy
    d = list(copy_iter(map(identity, pts)))  # tpyc: ok
    print(len(d))

main()
