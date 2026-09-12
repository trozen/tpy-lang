# A record local initialized from a `Ptr[T]` deref and later reassigned from an
# RVALUE: the reassign needs its own slot, so the alias and the fresh value must
# stay distinct -- the original point keeps its own coordinates.
from tpy import int32, Ptr


class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y


def main() -> None:
    pt = Point(1, 2)
    ptr: Ptr[Point] = pt
    alias: Point = ptr
    alias.x = 7  # the deref binding aliases pt
    print(pt.x, alias.x)
    alias = Point(3, 4)  # the rvalue reassign takes a fresh slot
    print(pt.x, alias.x)


main()
