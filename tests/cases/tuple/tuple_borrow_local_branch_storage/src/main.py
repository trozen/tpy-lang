# A branch-hoisted borrow-form tuple local where one arm assigns a literal
# and the other a STORAGE-form source (list subscript): the storage arm must
# be lifted element-wise to pointer form, and mutation through the joined
# local reaches whichever object the taken arm aliased.
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def pick(b: Box, cond: bool) -> Int32:
    items = [(7, Box(10))]
    if cond:
        t = (1, b)
    else:
        t = items[0]
    t[1].val = t[1].val + 1
    return items[0][1].val


def main() -> None:
    b = Box(5)
    print(pick(b, True))
    print(b.val)
    print(pick(b, False))


main()
