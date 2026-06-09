# Branch-merge (UNION): the `if` arm binds the element from a borrow, so the
# post-branch name return into an Own[T] slot is rejected even if `else` copies.
from tpy import Int32, Own, copy


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def f(b: Box, c: bool) -> tuple[Own[Box], Int32]:
    if c:
        pair = (b, 0)
    else:
        pair = (copy(b), 1)
    return pair  # tpyc: error(/borrowed value as tuple element 0 Own\[Box\]/)


def main() -> None:
    pass


main()
