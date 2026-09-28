# A `nonlocal` binding may not widen the enclosing function's inferred local:
# that function was analyzed at the narrower type and would truncate the value.
from tpy import int64


def widen(big: int64) -> None:
    x = 3
    def g() -> None:
        nonlocal x
        # the wider binding reaches the enclosing int32 local
        x = big  # tpyc: error(/'x' has type int32 in the enclosing function, and this 'nonlocal' binding would make it int64; annotate its first binding there: x: int64/)
    g()
    print(x)


widen(10000000000)
