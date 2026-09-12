# @total_ordering with __le__ as the anchor: synthesizes __lt__,
# __gt__, __ge__ in terms of __le__ and __eq__.
from functools import total_ordering
from tpy import int32

@total_ordering
class Bid:
    amount: int32
    def __init__(self, a: int32) -> None:
        self.amount = a
    def __eq__(self, other: "Bid") -> bool:
        return self.amount == other.amount
    def __le__(self, other: "Bid") -> bool:
        return self.amount <= other.amount

x = Bid(int32(10))
y = Bid(int32(20))
print(x < y)
print(x <= y)
print(x > y)
print(x >= y)
print(y > x)
