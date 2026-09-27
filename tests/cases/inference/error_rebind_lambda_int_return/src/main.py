# A float-returning callable local rebound to a lambda returning an int is
# refused: the inferred type types the lambda's parameter, not its result.
from typing import Callable


def rebind(h: Callable[[float], float]) -> None:
    k = h
    k = lambda x: 1  # tpyc: error(/Type mismatch in reassignment to 'k': expected Callable\[\[float\], float\], got Callable\[\[float\], int32\]/)
    print(k(1.0))


rebind(lambda x: x)
