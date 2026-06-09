# A borrow-into-Own hazard bound inside a loop body survives the loop exit
# (UNION-merged), so the post-loop name return into an Own[T] slot is rejected.
from tpy import Int32, Own, copy


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def f(b: Box, n: Int32) -> tuple[Own[Box], Int32]:
    pair = (copy(b), 0)
    i = 0
    while i < n:
        pair = (b, i)
        i += 1
    return pair  # tpyc: error(/borrowed value as tuple element 0 Own\[Box\]/)


def main() -> None:
    pass


main()
