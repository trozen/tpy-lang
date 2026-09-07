# Returning a `Ptr[readonly[T]]` param at a mutable `T` slot: that binds a
# mutable reference to a const pointee, so the return rejects.
from tpy import Int32, Ptr, readonly


class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y


def deref_mut(p: Ptr[readonly[Point]]) -> Point:
    return p  # tpyc: error(/return\.record_source/)


def main() -> None:
    pt = Point(1, 2)
    cp: Ptr[readonly[Point]] = pt
    print(deref_mut(cp).x)


main()
