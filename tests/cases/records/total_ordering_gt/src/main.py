# @total_ordering with __gt__ as the anchor: synthesizes __lt__,
# __le__, __ge__. Covers the half of the derivation table that
# `total_ordering_lt` doesn't reach.
from functools import total_ordering
from tpy import Int32

@total_ordering
class Rank:
    n: Int32
    def __init__(self, v: Int32) -> None:
        self.n = v
    def __eq__(self, other: "Rank") -> bool:
        return self.n == other.n
    def __gt__(self, other: "Rank") -> bool:
        return self.n > other.n

a = Rank(Int32(7))
b = Rank(Int32(3))
print(a > b)
print(a >= b)
print(a < b)
print(a <= b)
print(a == Rank(Int32(7)))
