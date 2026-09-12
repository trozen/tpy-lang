# @total_ordering requires at least one ordering op -- error when only
# __eq__ is present.
from functools import total_ordering
from tpy import int32

@total_ordering
class OnlyEq:  # tpyc: error(/define one of __lt__, __le__, __gt__, __ge__/)
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v
    def __eq__(self, other: "OnlyEq") -> bool:
        return self.val == other.val
