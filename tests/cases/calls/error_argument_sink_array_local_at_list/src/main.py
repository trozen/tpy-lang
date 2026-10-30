# A literal-seeded list local the lowering keeps as a fixed array is no list
# a `list` parameter binds (BUGS.md#array-literal-into-readonly-list-param).
from tpy import int32, readonly


def total(xs: readonly[list[int32]]) -> int32:
    return len(xs)


def main() -> None:
    bs = [1, 2]
    print(total(bs))  # tpyc: error(/call\.arg_shape\.container/)


main()
