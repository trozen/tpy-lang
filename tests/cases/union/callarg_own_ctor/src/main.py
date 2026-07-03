# Ctor rvalues into Own[union] arg slots pass inline; a value-union int
# literal renders bare. Fresh value per call -- read-only output is intended.
from tpy import Int32, Float64, Own


class A:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


class B:
    y: Int32

    def __init__(self, y: Int32) -> None:
        self.y = y


class Sink:
    u: A | B

    def __init__(self, v: Own[A | B]) -> None:
        self.u = v


def consume(v: Own[A | B]) -> Int32:
    sink = Sink(v)
    w = sink.u
    if isinstance(w, A):
        return w.x
    return w.y


# Discriminates on Float64 (== float under CPython) so the plain-int literal
# arg narrows identically on both runtimes (isinstance(3, Int32) would be
# False under CPython, where the literal is a plain int).
def pick(v: Int32 | Float64) -> Int32:
    if isinstance(v, Float64):
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
