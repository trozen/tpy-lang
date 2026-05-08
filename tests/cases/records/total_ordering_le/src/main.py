# @total_ordering with __le__ as the anchor: synthesizes __lt__,
# __gt__, __ge__ in terms of __le__ and __eq__.
from functools import total_ordering
from tpy import Int32

@total_ordering
class Bid:
    amount: Int32
    def __init__(self, a: Int32) -> None:
        self.amount = a
    def __eq__(self, other: "Bid") -> bool:
        return self.amount == other.amount
    def __le__(self, other: "Bid") -> bool:
        return self.amount <= other.amount

x = Bid(Int32(10))
y = Bid(Int32(20))
print(x < y)
print(x <= y)
print(x > y)
print(x >= y)
print(y > x)
