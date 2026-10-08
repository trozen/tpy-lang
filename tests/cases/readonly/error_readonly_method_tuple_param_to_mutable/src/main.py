# A tuple param of a @readonly method is readonly where it holds a reference,
# so passing it on to a mutable tuple param is refused per element, as the
# literal `(o[0], o[1])` is -- the scalar twin `o: Counter` is refused alike.
from tpy import int32, readonly


class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def bump(p: tuple[Counter, int32]) -> None:
    p[0].n += 1


class Z:
    k: int32

    def __init__(self) -> None:
        self.k = 0

    @readonly
    def m(self, o: tuple[Counter, int32]) -> None:
        bump(o)  # tpyc: error(/Cannot pass readonly\[Counter\] as mutable Counter in argument 'p' \(tuple element 0\)/)


def main() -> None:
    Z().m((Counter(1), 2))


main()
