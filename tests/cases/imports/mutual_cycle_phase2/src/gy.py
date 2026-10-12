# The mirror of ga/gb: the genexpr's callee is in the member analyzed
# earlier.
from tpy import int32
import gz


def size(xs: list[int32]) -> int32:
    return len(xs)


def back(xs: list[int32]) -> int32:
    return gz.one() + size(xs)
