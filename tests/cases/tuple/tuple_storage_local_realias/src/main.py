# A REASSIGNED tuple local bound from lvalue storage sources takes the
# borrow-form pointer machinery (a reference can't rebind): each binding
# aliases its element, so mutation through the local reaches the stored
# element bound at that point.
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def main() -> None:
    items: list[tuple[int32, Box]] = [(1, Box(10)), (2, Box(20))]
    t = items[0]
    t[1].val = 99
    t = items[1]
    t[1].val = 88
    print(items[0][1].val)
    print(items[1][1].val)


main()
