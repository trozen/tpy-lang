# Branch-merge (UNION): the `if` arm binds the element from a borrow, so the
# post-branch name return into an Own[T] slot is rejected even if `else` copies.
# TO BE FIXED: the scalar twin WARNS and copies here; this error becomes that
# warning (BUGS.md#borrowed-tuple-at-own-call-arg, plan unit U3 D1).
from tpy import int32, Own, copy


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def f(b: Box, c: bool) -> tuple[Own[Box], int32]:
    if c:
        pair = (b, 0)
    else:
        pair = (copy(b), 1)
    return pair  # tpyc: error(/borrowed value as tuple element 0 Own\[Box\]/)


def main() -> None:
    pass


main()
