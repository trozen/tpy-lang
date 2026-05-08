# @total_ordering requires __eq__ -- error when only an ordering op
# is defined.
from functools import total_ordering
from tpy import Int32

@total_ordering
class NoEq:  # tpyc: error(/requires .* to define __eq__/)
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v
    def __lt__(self, other: "NoEq") -> bool:
        return self.val < other.val
