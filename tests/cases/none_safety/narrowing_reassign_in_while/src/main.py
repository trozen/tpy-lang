# Reassigning a narrowed Optional inside a while loop invalidates the narrowing.
from tpy import Int32


def narrowing_cleared_on_reassign(x: Int32 | None, other: Int32 | None, n: Int32) -> Int32:
    total: Int32 = 0
    if x is not None:
        i: Int32 = 0
        while i < n:
            x = other
            total = total + x  # tpyc: warning(/Potential None access/)
            i = i + 1
    return total


print(narrowing_cleared_on_reassign(1, 2, 3))
print(narrowing_cleared_on_reassign(None, 2, 3))
