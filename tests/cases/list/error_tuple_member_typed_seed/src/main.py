# A typed value in a tuple member of a list's first binding decides that
# member there: a wider store into it is refused naming the member.
from tpy import int8, int64


def a8() -> int8:
    return 100


def a64() -> int64:
    return 1099511627776


def main() -> None:
    ps = [(a8(), 2)]
    ps.append((a64(), 3))  # tpyc: error(/'ps' holds tuple\[int8, int32\] elements \(line 15\), and this value is int64 \(tuple element 0\); annotate its first binding: ps: list\[tuple\[int64, int32\]\] = \[\.\.\.\]/)
    print(ps)


main()
