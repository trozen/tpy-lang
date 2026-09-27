# A float-returning callable local rebound to a function returning an int is
# refused: the local's inferred type does not convert the int result.
from typing import Callable

from tpy import int32


def one(x: float) -> int32:
    return 1


def rebind(h: Callable[[float], float]) -> None:
    k = h
    k = one  # tpyc: error(/Type mismatch in reassignment to 'k': expected Callable\[\[float\], float\], got Callable\[\[float\], int32\]/)
    print(k(1.0))


rebind(lambda x: x)
