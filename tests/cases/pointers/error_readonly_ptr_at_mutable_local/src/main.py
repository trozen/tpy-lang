# Binding a `Ptr[readonly[T]]` to a mutable `T` local would hand out the const
# pointee mutably; away from an argument or a return the hint says to keep the
# pointer.
from tpy import Ptr, int32, readonly, take_ptr


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def f(p: Ptr[readonly[Point]]) -> int32:
    q: Point = p  # tpyc: error(/expected Point, got Ptr\[readonly\[Point\]\] -- keep the pointer: bind it to a name without an annotation or to a 'Ptr\[readonly\[Point\]\]' slot/)
    return q.x


def main() -> None:
    a = Point(1)
    print(f(take_ptr(a)))


main()
