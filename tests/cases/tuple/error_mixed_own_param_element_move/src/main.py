# Moving the OWNED element of a mixed owned+borrow tuple param into an
# Own[T] slot by subscript, from a method: a partial move out of the param
# needs place liveness, and the fully owned twin rejects it the same way
# (BUGS.md#consume-own-element-of-mixed-tuple). Unpacking first moves it.
from tpy import Own, int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def inc(self) -> None:
        self.n += 1


def sink(b: Own[Box]) -> Own[Box]:
    return b


def mut(b: Box) -> None:
    b.n = 5


class H:
    k: int32

    def __init__(self) -> None:
        self.k = 0

    def f(self, p: tuple[Own[Box], Box]) -> int32:
        return sink(p[0]).n + self.k  # tpyc: error(/call\.arg_shape\.own_record_f1/)


def main() -> None:
    b = Box(2)
    print(H().f((Box(1), b)))


main()
