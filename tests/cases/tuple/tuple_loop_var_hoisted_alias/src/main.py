# A tuple loop var used AFTER the loop is hoisted to function scope in
# borrow form: each iteration lifts the storage element to pointers, and
# the post-loop var still aliases the LAST element (CPython rebinding).
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def main() -> None:
    items: list[tuple[int32, Box]] = [(1, Box(10)), (2, Box(20))]
    for t in items:
        pass
    t[1].val = 99
    print(t[0])
    print(items[1][1].val)


main()
