# A 3-arg range() whose step is an int literal wider than int32 keeps
# rejecting: the stepped arms thread the step token through the overflow
# helpers, whose render is pinned only for the int32-range subset.
from tpy import int64


def main():
    end = int64(20000000000)
    total = int64(0)
    for k in range(int64(0), end, 4000000000):  # tpyc: error(/iter.range_shape/)
        total += k
    print(total)


main()
