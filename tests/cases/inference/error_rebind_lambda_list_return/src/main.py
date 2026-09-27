# A list-returning callable local rebound to a lambda returning an int list is
# refused: the inferred type types the lambda, not its list's elements.
from typing import Callable


def rebind(h: Callable[[], list[float]]) -> None:
    k = h
    k = lambda: [1]  # tpyc: error(/Type mismatch in reassignment to 'k': expected Callable\[\[\], list\[float\]\], got Callable\[\[\], list\[int32\]\]/)
    print(k())


rebind(lambda: [0.5])
