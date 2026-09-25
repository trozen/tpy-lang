# A declared list[float] slot does not convert a list[int32] VARIABLE: the
# copy would lose CPython's aliasing (docs/LANGUAGE_FEATURES.md, numeric tower).
from tpy import int32


def main(xs: list[int32]) -> None:
    # Only the literal pins to list[float]; `xs` keeps its own type.
    ys: list[float] = xs or [2.5]  # tpyc: error(/this `or` mixes int32 and float elements, and CPython keeps whichever value it picks; declare xs as list\[float\]$/)
    print(ys)


main([1])
