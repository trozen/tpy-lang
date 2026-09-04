# A 3-arg range() whose step is an int literal wider than Int32 keeps
# rejecting: the stepped arms thread the step token through the overflow
# helpers, whose render is pinned only for the int32-range subset.
from tpy import Int64


def main():
    end = Int64(20000000000)
    total = Int64(0)
    for k in range(Int64(0), end, 4000000000):  # tpyc: error(/iter.range_shape/)
        total += k
    print(total)


main()
