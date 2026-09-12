# @total_ordering with __lt__ as the anchor: synthesizes __le__,
# __gt__, __ge__ in terms of __lt__ and __eq__. Demonstrates that the
# macro re-export through `functools` (which itself imports from
# `_functools_macros`) is wired correctly.
from functools import total_ordering
from tpy import int32

@total_ordering
class Score:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v
    def __eq__(self, other: "Score") -> bool:
        return self.val == other.val
    def __lt__(self, other: "Score") -> bool:
        return self.val < other.val

a = Score(int32(3))
b = Score(int32(5))
c = Score(int32(3))
print(a < b)
print(a <= b)
print(a > b)
print(a >= b)
print(a == c)
print(a <= c)
print(a >= c)
