# A branch-hoisted owning tuple local: each branch binds an Own[tuple[...]]
# call, and the ref element is dereferenced AFTER the branch. The owning slot
# must be function-scoped (not block-scoped) or the borrow would dangle.
from tpy import int32, Own


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def make_pair(n: int32) -> Own[tuple[int32, Box]]:
    return (n, Box(n))


def use(c: bool) -> int32:
    if c:
        t = make_pair(9)
    else:
        t = make_pair(5)
    return t[1].val


def main() -> None:
    print(use(True))
    print(use(False))


main()
