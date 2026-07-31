# A mixed owned+borrow tuple (`tuple[Own[Box], Box]`) keeps its per-element
# form at EVERY local decl path, not just a straight binding: the owned element
# stays by value and the borrowed element stays a pointer into the caller's
# object. Each function writes through the borrowed element and the caller
# reads the original, so a copy at any of these decl paths shows up as a wrong
# number rather than passing silently.
from tpy import Int32, Own


class Box:
    val: Int32

    def __init__(self, val: Int32) -> None:
        self.val = val


def make_mixed(b: Box) -> tuple[Own[Box], Box]:
    return (Box(1), b)


def rebind(b: Box, c: Box) -> Int32:
    p = make_mixed(b)
    p[1].val = 11
    p = make_mixed(c)  # tpyc: ok
    p[1].val = 22
    return p[0].val


def branch_hoisted(b: Box, c: Box, pick: bool) -> Int32:
    if pick:
        p = make_mixed(b)
    else:
        p = make_mixed(c)
    p[1].val = 33
    return p[0].val


def loop_carried(b: Box, c: Box) -> Int32:
    total = 0
    for i in range(2):
        if i == 0:
            p = make_mixed(b)
        else:
            p = make_mixed(c)
        p[1].val = 44 + i
        total = total + p[0].val
    return total


def try_hoisted(b: Box) -> Int32:
    try:
        p = make_mixed(b)
    except ValueError:
        p = make_mixed(b)
    p[1].val = 66
    return p[0].val


def walrus(b: Box) -> Int32:
    if (p := make_mixed(b))[0].val > 0:  # tpyc: ok
        p[1].val = 77
    return p[0].val


def main() -> None:
    b = Box(7)
    c = Box(8)

    print("rebind:", rebind(b, c), b.val, c.val)
    print("branch:", branch_hoisted(b, c, True), b.val, c.val)
    print("loop:", loop_carried(b, c), b.val, c.val)
    print("try:", try_hoisted(b), b.val)
    print("walrus:", walrus(b), b.val)


main()
