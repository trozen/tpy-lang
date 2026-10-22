# A module variable joins its bindings, so an annotated slot it is stored
# into before a wider binding takes the checked narrowing, as in a function:
# the second pass raises OverflowError where it once truncated silently.
from tpy import int32, int64

n = 0
for i in range(2):
    k: int32 = n  # tpyc: cast_checked(int32)
    print("pass", i, k)
    n = int64(5000000000)
