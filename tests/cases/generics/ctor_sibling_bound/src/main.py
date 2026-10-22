# A generic record's type-parameter bound that names a sibling parameter
# (`T: Getter[R]`) is checked with R substituted, for an inferred and a
# written construction alike.
from typing import Protocol


class Getter[R](Protocol):
    def get(self) -> R: ...


class IntGet:
    def get(self) -> int:
        return 7


class Pair[R, T: Getter[R]]:
    r: R
    t: T

    def __init__(self, r: R, t: T) -> None:
        self.r = r
        self.t = t


def main() -> None:
    # inferred: R = int from the first argument, T = IntGet meets Getter[int]
    # (a literal first argument, Pair(7, IntGet()), makes R = int32 and fails
    # the C++ build: BUGS.md#protocol-return-width-conformance-lenient)
    p = Pair(IntGet().get(), IntGet())  # tpyc: ok
    print("inferred", p.r, p.t.get())
    # written: the same check with the arguments spelled out
    q = Pair[int, IntGet](3, IntGet())  # tpyc: ok
    print("written", q.r, q.t.get())


main()
