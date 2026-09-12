# Regression guard: after the in/not-in tuple-gate widening, a tuple
# LHS with a non-iterable RHS must still error (membership-block fallback).
from tpy import int32


def main() -> None:
    n: int32 = 5
    k: tuple[int32, int32] = (1, 2)
    print(k in n)  # tpyc: error(/non-iterable/)


main()
