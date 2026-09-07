# A readonly element source sets the borrow-decl const bit, so a branch-hoisted
# tuple over it falls outside the non-const slice.
from tpy import Int32, readonly


class Box:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


def pick(b: readonly[Box], c: bool) -> Int32:
    if c:  # tpyc: error(/if.hoist_type/)
        t = (1, b)
    else:
        t = (2, b)
    return t[0]


def main() -> None:
    print(pick(Box(1), True))


main()
