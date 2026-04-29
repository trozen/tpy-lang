# Regression guard: after the in/not-in tuple-gate widening, a tuple
# LHS with a non-iterable RHS must still error (membership-block fallback).
from tpy import Int32


def main() -> None:
    n: Int32 = 5
    k: tuple[Int32, Int32] = (1, 2)
    print(k in n)  # tpyc: error(/non-iterable/)


main()
