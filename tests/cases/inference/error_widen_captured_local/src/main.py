# A nested def (or a lambda / generator expression, recorded the same way) reads
# an inferred local at its type then; a later binding may not widen that type.
from tpy import int64


def widen(big: int64) -> None:
    x = 3
    def g() -> None:
        print(x + 1)
    # the wider binding after the capture: annotate `x: int64` before the def
    x = big  # tpyc: error(/'x' is read by nested function 'g' at line 8 while it has type int32, so this binding cannot make it int64; annotate its first binding before line 8: x: int64/)
    g()


widen(10000000000)
