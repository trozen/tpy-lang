# A literal-seeded local never becomes unsigned: its literals count as the
# default int, which has no common type with a uint32 value.
from tpy import uint32


def main(u: uint32) -> None:
    x = 0
    # the uint32 store that meets the int32 literals
    x = u  # tpyc: error(/'x' holds int32 values and this value is uint32, which have no common type; annotate its first binding: x: uint32 = 0/)
    print(x)


main(7)
