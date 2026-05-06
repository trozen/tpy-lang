from a import A
from tpy import Int32

class B:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n

# Real cross-cycle reference: takes A by reference (non-value record,
# auto-wrapped in Ref). The gate still rejects A's by-value `other: B`
# field because that needs B's complete layout, which the cycle's
# `b_fwd.hpp` does not provide.
def take_a(a: A) -> Int32:
    return a.val
