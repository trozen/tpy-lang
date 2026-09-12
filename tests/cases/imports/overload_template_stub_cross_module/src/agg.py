# Defines an overloaded fn whose Iterable[Own[int32]] stub is a C++ template
# while the impl (a union param) is not -- the group must be header-only.
from tpy import int32, Own
from typing import Iterable, overload


@overload
def total(xs: Iterable[Own[int32]]) -> int32: ...
@overload
def total(xs: int32) -> int32: ...
def total(xs: Iterable[Own[int32]] | int32) -> int32:
    if isinstance(xs, int32):
        return xs
    s = 0
    for x in xs:
        s += x
    return s
