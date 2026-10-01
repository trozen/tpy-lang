# A nested def (or a lambda / generator expression) reads a literal-seeded
# local at the type its stores so far give; a later binding may not widen it.
from tpy import int64


def widen(big: int64) -> None:
    x = 3
    def g() -> None:
        print(x + 1)
    # the wider binding after the capture: annotate `x: int64` instead
    x = big  # tpyc: error(/'x' was used as int32 at line 8 \(read by the nested function 'g'\), and this value is int64; annotate its first binding: x: int64 = 3/)
    g()


widen(10000000000)
