# An element read of an unannotated list literal has the list's element type,
# which comes from the values stored in it, not from the slot that reads it.
from tpy import int8


def main() -> None:
    ys = [1, 300]
    # 300 does not fit an int8; the element is an int32 whatever it holds.
    n: int8 = ys[1]  # tpyc: error(/expected int8, got int32; 'ys' holds int32 elements, and a list\[int8\] would not hold 300: declare the variable as int32/)
    print(n)


main()
