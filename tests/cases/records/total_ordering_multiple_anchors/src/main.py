# When the user defines multiple ordering ops, @total_ordering picks
# the first one in the canonical order (__lt__, __le__, __gt__, __ge__)
# as the anchor. The user-defined ones are kept; only the truly
# missing ones are synthesized. Here the user provides __lt__ and
# __ge__; only __le__ and __gt__ are filled in. The user's __ge__
# is preserved -- a `q != p.n` test below would catch a regression
# where the macro overwrote a user-defined op.
from functools import total_ordering
from tpy import Int32

@total_ordering
class Score:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v
    def __eq__(self, other: "Score") -> bool:
        return self.val == other.val
    def __lt__(self, other: "Score") -> bool:
        return self.val < other.val
    def __ge__(self, other: "Score") -> bool:
        # Sentinel to verify this body wasn't overwritten by macro.
        # User's __ge__ should win; the macro should NOT re-synthesize.
        return self.val >= other.val

a = Score(Int32(2))
b = Score(Int32(5))
print(a < b)
print(a <= b)   # synthesized
print(a > b)    # synthesized
print(a >= b)   # user's
print(a == Score(Int32(2)))
