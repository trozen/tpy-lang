# A select of a borrow tuple and an owned temporary (a call written in the
# select) is refused (BUGS.md#tuple-select-deferred-shapes).
from tpy import int32, Own


class A:
    def __init__(self, v: int32) -> None:
        self.v = v


def mk_own[T](a: Own[T]) -> Own[tuple[T, int32]]:
    return (a, 2)


def pick[T](x: tuple[T, int32], a: Own[T], flag: bool) -> int32:
    y = x if flag else mk_own(a)  # tpyc: error(/call\.ret_type\.tuple/)
    return y[1]


def main() -> None:
    print(pick((A(1), 1), A(2), False))


main()
