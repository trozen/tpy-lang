# A borrow-into-Own hazard bound inside a loop body survives the loop exit
# (UNION-merged), so the post-loop name return into an Own[T] slot is rejected.
# TO BE FIXED: the scalar twin WARNS and copies here; this error becomes that
# warning (BUGS.md#borrowed-tuple-at-own-call-arg, plan unit U3 D1).
from tpy import int32, Own, copy


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def f(b: Box, n: int32) -> tuple[Own[Box], int32]:
    pair = (copy(b), 0)
    i = 0
    while i < n:
        pair = (b, i)
        i += 1
    return pair  # tpyc: error(/borrowed value as tuple element 0 Own\[Box\]/)


def main() -> None:
    pass


main()
