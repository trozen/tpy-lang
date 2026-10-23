# A mixed owned+borrow tuple (`tuple[Own[Box], Box]`) keeps its per-element
# form at EVERY local decl path, not just a straight binding: the owned element
# stays by value and the borrowed element stays a pointer into the caller's
# object. Each function writes through the borrowed element and the caller
# reads the original, so a copy at any of these decl paths shows up as a wrong
# number rather than passing silently.
from tpy import int32, Own


class Box:
    val: int32

    def __init__(self, val: int32) -> None:
        self.val = val


def make_mixed(b: Box) -> tuple[Own[Box], Box]:
    return (Box(1), b)


def rebind(b: Box, c: Box) -> int32:
    p = make_mixed(b)
    p[1].val = 11
    p = make_mixed(c)  # tpyc: ok
    p[1].val = 22
    return p[0].val


def branch_hoisted(b: Box, c: Box, pick: bool) -> int32:
    if pick:
        p = make_mixed(b)
    else:
        p = make_mixed(c)
    p[1].val = 33
    return p[0].val


def loop_carried(b: Box, c: Box) -> int32:
    total = 0
    for i in range(2):
        if i == 0:
            p = make_mixed(b)
        else:
            p = make_mixed(c)
        p[1].val = 44 + i
        total = total + p[0].val
    return total


def try_hoisted(b: Box) -> int32:
    try:
        p = make_mixed(b)
    except ValueError:
        p = make_mixed(b)
    p[1].val = 66
    return p[0].val


def walrus(b: Box) -> int32:
    if (p := make_mixed(b))[0].val > 0:  # tpyc: ok
        p[1].val = 77
    return p[0].val


def relay(p: tuple[Box, Box]) -> tuple[Own[Box], Box]:
    t = p
    # an alias of a borrowed tuple parameter returned at a mixed slot: the
    # owned element copies, the borrowed one stays the caller's
    return t  # tpyc: warning(/copies Box into owned storage \(tuple element 0\)/)


def own_separate[T](q: Own[T], b: Box) -> tuple[Own[T], Box]:
    t = (q, b)
    # the owned element moves in, the borrowed one stays the caller's
    return t  # tpyc: ok


def mixed_param[T](q: tuple[Own[T], Box]) -> tuple[Own[T], Box]:
    # a mixed parameter hands back the caller's borrowed element
    return q  # tpyc: ok


def mixed_param_box(q: tuple[Own[Box], Box]) -> tuple[Own[Box], Box]:
    # the monomorphic twin
    return q  # tpyc: ok


def main() -> None:
    b = Box(7)
    c = Box(8)

    print("rebind:", rebind(b, c), b.val, c.val)
    print("branch:", branch_hoisted(b, c, True), b.val, c.val)
    print("loop:", loop_carried(b, c), b.val, c.val)
    print("try:", try_hoisted(b), b.val)
    print("walrus:", walrus(b), b.val)
    a = Box(1)
    r = relay((a, b))
    r[1].val = 9
    print("relay:", r[0].val, b.val)
    s1 = own_separate(Box(2), b)
    b.val = 10
    print("own_separate:", s1[0].val, s1[1].val)
    s2 = mixed_param((Box(3), b))
    b.val = 11
    print("mixed_param:", s2[0].val, s2[1].val)
    s3 = mixed_param_box((Box(4), b))
    b.val = 12
    print("mixed_param_box:", s3[0].val, s3[1].val)


main()
