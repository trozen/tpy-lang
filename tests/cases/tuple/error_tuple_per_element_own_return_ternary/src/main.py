# The borrow-into-Own hazard UNIONs both ternary arms: a borrowed element in
# either arm rejects the name return into an Own[T] slot.
# TO BE FIXED: the scalar twin WARNS and copies here; this error becomes that
# warning (BUGS.md#borrowed-tuple-at-own-call-arg, plan unit U3 D1).
from tpy import int32, Own, copy


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def f(b: Box, c: bool) -> tuple[Own[Box], int32]:
    pair = (b, 0) if c else (copy(b), 0)
    return pair  # tpyc: error(/borrowed value as tuple element 0 Own\[Box\]/)


def main() -> None:
    pass


main()
