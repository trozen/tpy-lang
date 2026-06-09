# The borrow-into-Own hazard UNIONs both ternary arms: a borrowed element in
# either arm rejects the name return into an Own[T] slot.
from tpy import Int32, Own, copy


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def f(b: Box, c: bool) -> tuple[Own[Box], Int32]:
    pair = (b, 0) if c else (copy(b), 0)
    return pair  # tpyc: error(/borrowed value as tuple element 0 Own\[Box\]/)


def main() -> None:
    pass


main()
