# A branch-bound `tuple[A, str]` local hoisted from parameters is refused: the
# hoist would copy the str (BUGS.md#tuple-select-deferred-shapes).
from tpy import int32


class A:
    def __init__(self, v: int32) -> None:
        self.v = v


def f(x: tuple[A, str], z: tuple[A, str], flag: bool) -> None:
    if flag:  # tpyc: error(/if\.hoist_type/)
        y = x
    else:
        y = z
    print(y[1], y[0].v)


f((A(1), "hello"), (A(2), "world"), True)
