# A call needing an argument temp nested TWO tuple levels deep in a return: the
# temp grant is per caller and does not recurse, so the inner tuple has no flush
# point of its own.
from tpy import Int32, StrView


def take_union(u: Int32 | StrView | None) -> Int32:
    return 0


def ret(k: Int32) -> tuple[bool, tuple[Int32, Int32]]:
    return (True, (take_union(k), 1))  # tpyc: error(/return\.tuple_source/)


def main() -> None:
    print(ret(1)[1][0])


main()
