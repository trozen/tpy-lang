# The bound at `T = Own[X]` holds exactly when it holds at `X`, both ways:
# `Own[P]` owns a class instance, so `T: ValueType` still refuses it.
from tpy import Fn, Own, ValueType, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def make() -> Own[P]:
    return P(1)


def f_val[T: ValueType](mk: Fn[[], T]) -> int32:
    return 1


def main() -> None:
    print(f_val[Own[P]](make))  # tpyc: error(/'Own\[P\]' does not satisfy bound 'ValueType'/)


main()
