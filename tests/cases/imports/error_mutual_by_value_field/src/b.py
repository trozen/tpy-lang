from a import A
from tpy import int32

class B:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n

# Real cross-cycle reference: takes A by reference (non-value record,
# auto-wrapped in Ref). The gate still rejects A's by-value `other: B`
# field because that needs B's complete layout, which the cycle's
# `b_fwd.hpp` does not provide.
def take_a(a: A) -> int32:
    return a.val
