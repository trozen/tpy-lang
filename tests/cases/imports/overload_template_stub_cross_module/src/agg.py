# Defines an overloaded fn whose Iterable[Own[Int32]] stub is a C++ template
# while the impl (a union param) is not -- the group must be header-only.
from tpy import Int32, Own
from typing import Iterable, overload


@overload
def total(xs: Iterable[Own[Int32]]) -> Int32: ...
@overload
def total(xs: Int32) -> Int32: ...
def total(xs: Iterable[Own[Int32]] | Int32) -> Int32:
    if isinstance(xs, Int32):
        return xs
    s = 0
    for x in xs:
        s += x
    return s
