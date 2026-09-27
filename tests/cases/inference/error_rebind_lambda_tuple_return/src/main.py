# A tuple-returning callable local rebound to a lambda returning an int tuple
# is refused, and the message spells the tuple's resolved element types.
from typing import Callable


def rebind(h: Callable[[], tuple[float, float]]) -> None:
    k = h
    k = lambda: (1, 2)  # tpyc: error(/Type mismatch in reassignment to 'k': expected Callable\[\[\], tuple\[float, float\]\], got Callable\[\[\], tuple\[int32, int32\]\]/)
    print(k())


rebind(lambda: (0.5, 1.5))
