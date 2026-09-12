# Reassigning a narrowed Optional inside a while loop invalidates the narrowing.
from tpy import int32


def narrowing_cleared_on_reassign(x: int32 | None, other: int32 | None, n: int32) -> int32:
    total: int32 = 0
    if x is not None:
        i: int32 = 0
        while i < n:
            x = other
            total = total + x  # tpyc: warning(/Potential None access/)
            i = i + 1
    return total


print(narrowing_cleared_on_reassign(1, 2, 3))
print(narrowing_cleared_on_reassign(None, 2, 3))
