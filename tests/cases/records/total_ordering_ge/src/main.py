# @total_ordering with __ge__ as the anchor: synthesizes __lt__,
# __le__, __gt__. Covers the remaining anchor-row in the derivation
# table.
from functools import total_ordering
from tpy import Int32

@total_ordering
class Weight:
    grams: Int32
    def __init__(self, g: Int32) -> None:
        self.grams = g
    def __eq__(self, other: "Weight") -> bool:
        return self.grams == other.grams
    def __ge__(self, other: "Weight") -> bool:
        return self.grams >= other.grams

a = Weight(Int32(50))
b = Weight(Int32(30))
print(a >= b)
print(a > b)
print(a <= b)
print(a < b)
print(b <= a)
