# Ctor rvalues into Own[union] arg slots pass inline; a value-union int
# literal renders bare. Fresh value per call -- read-only output is intended.
from tpy import int32, float64, Own


class A:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class B:
    y: int32

    def __init__(self, y: int32) -> None:
        self.y = y


class Sink:
    u: A | B

    def __init__(self, v: Own[A | B]) -> None:
        self.u = v


def consume(v: Own[A | B]) -> int32:
    sink = Sink(v)
    w = sink.u
    if isinstance(w, A):
        return w.x
    return w.y


# Discriminates on float64 (== float under CPython) so the plain-int literal
# arg narrows identically on both runtimes (isinstance(3, int32) would be
# False under CPython, where the literal is a plain int).
def pick(v: int32 | float64) -> int32:
    if isinstance(v, float64):
        return -1
    return v


def main() -> None:
    print(consume(A(7)))
    print(consume(B(20)))
    print(pick(3))
    t = 0
    while consume(A(2)) > t:
        t = t + 1
    print(t)


main()
