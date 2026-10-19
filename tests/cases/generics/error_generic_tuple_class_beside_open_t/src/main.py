# A `tuple[A, T, int32]` local (a class element beside an open T) is refused,
# as before (BUGS.md#tuple-select-deferred-shapes).
from tpy import int32


class A:
    def __init__(self, v: int32) -> None:
        self.v = v


def f[T](x: tuple[A, T, int32]) -> int32:
    y = x  # tpyc: error(/decl\.slot_type/)
    return y[2] + y[0].v


print(f((A(1), 2, 3)))
