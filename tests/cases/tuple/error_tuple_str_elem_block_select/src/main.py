# A block-local select of two `tuple[A, str]` parameters is refused: its
# borrow-form copy would copy the str (BUGS.md#tuple-select-deferred-shapes).
from tpy import int32


class A:
    def __init__(self, v: int32) -> None:
        self.v = v


def f(x: tuple[A, str], z: tuple[A, str], flag: bool, other: bool) -> None:
    if other:
        y = x if flag else z  # tpyc: error(/decl\.branch_slot_type/)
        print(y[1], y[0].v)


f((A(1), "hello"), (A(2), "world"), True, True)
